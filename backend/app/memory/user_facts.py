# -*- coding: utf-8 -*-
"""用户级可变事实（§20，2026-09-04 落地）：upsert（记录旧值）、读取、[USER NOW] 注入文本。

- 全部本地规则：关键词正则归槽 + DB 单值槽 upsert，**零额外 LLM 调用**。
- 失败静默（铁律）：任何异常返回 None / [] / "无"，绝不阻塞主链路。
- 只对「可变单值槽」做取代（location/job/relationship/living/goal_state/health）；
  一次性事件仍 append-only 进 memories。槽位识别宁紧勿松（不确定回 None → 走原记忆逻辑）。
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.user import GlobalUserFact
from app.utils.timeutil import now_naive_utc

# 可变单值槽：key=槽位；value=(中文标签, 关键词正则列表)。
# 正则字符串 `re.search`（任一命中即归槽）。宁紧勿松：一次性事件/场景词别误归为可变状态。
MUTABLE_SLOTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "location": (
        "位置/城市",
        # F-4（2026-09-04，宁紧勿松）：纯趋向词「回到/回来/回了」须要求地点宾语（不再裸匹配
        # 「我回来了」）；否定/非地点宾语黑名单（正题/话题/问题/从前/以前/过去/状态/心情/梦里/记忆）；
        # 「从…(到|回)」要求右端为地点词（避免「从失败里走出来」误命中）。地点宾语一律要求 1-8 个汉字。
        ("城市", "住在", "居住在", "定居", "在.*(上班|上学|生活)",
         r"(?:回到|回到了|搬回|来到|去了|到了)\s*(?![\u4e00-\u9fa5]{0,8}(?:正题|话题|问题|从前|以前|过去|状态|心情|梦里|记忆))[\u4e00-\u9fa5]{1,8}",
         "搬家", "搬到", "搬回",
         r"从.+?(?:到|回)(?![\u4e00-\u9fa5]{0,8}(?:正题|话题|问题|从前|以前|过去|状态|心情|梦里|记忆))[\u4e00-\u9fa5]{1,8}"),
    ),
    "job": (
        "工作/学业",
        ("上班", "公司", "入职", "离职", "辞职", "学校", "毕业", "专业", "跳槽", "转行"),
    ),
    "relationship": (
        "感情状态",
        ("分手", "复合", "单身", "结婚", "恋爱", "在一起", "离婚", "订婚", "脱单"),
    ),
    "living": (
        "居住情况",
        ("搬家", "租房", "宿舍", "家里住", "同居", "合租", "独居"),
    ),
    "goal_state": (
        "进行中计划状态",
        ("准备", "打算", "在考", "备考", "项目", "面试", "筹备", "计划", "争取"),
    ),
    "health": (
        "身体状态",
        ("生病", "住院", "出院", "康复", "手术", "怀孕", "体检", "吃药"),
    ),
}

# 一次性经历：不做槽位取代（append-only 进 memories）
EVENT_ONLY = {"event"}


def classify_slot(text: str) -> str | None:
    """轻量本地槽位识别（关键词正则）；命中才归槽，不确定返回 None（走原记忆逻辑，不误判）。"""
    if not text:
        return None
    for slot, (_label, patterns) in MUTABLE_SLOTS.items():
        if any(re.search(p, text) for p in patterns):
            return slot
    return None


async def upsert_user_fact(
    user_id: int,
    slot: str,
    value: str,
    *,
    source: str = "chat",
    confidence: float = 1.0,
) -> tuple[str | None, str] | None:
    """新值取代旧值（单值槽），返回 (previous_value, value)；值未变或失败返回 None。幂等。

    - ``previous_value`` 供对旧记忆做失效匹配；
    - ``(user_id, slot)`` 唯一约束保证不产生重复行。
    """
    slot = (slot or "").strip()
    value = (value or "").strip()[:200]
    if not slot or not value:
        return None
    try:
        async with async_session_factory() as db:
            row = (await db.execute(
                select(GlobalUserFact).where(
                    GlobalUserFact.user_id == user_id,
                    GlobalUserFact.slot == slot,
                )
            )).scalar_one_or_none()
            if row is not None and row.value == value:
                return None  # 无变化（幂等）
            old = row.value if row else None
            if row is None:
                db.add(GlobalUserFact(
                    user_id=user_id, slot=slot, value=value, previous_value=old,
                    source=source, confidence=confidence, valid_from=now_naive_utc(),
                ))
            else:
                row.previous_value = old
                row.value = value
                row.source = source
                row.confidence = confidence
                row.valid_from = now_naive_utc()  # 【F-2】当前值生效起点，与"更新于"文案一致
                row.updated_at = now_naive_utc()
            await db.commit()
            return old, value
    except Exception:
        return None


async def get_active_user_facts(user_id: int) -> list[GlobalUserFact]:
    """取某用户全部事实槽（按 slot 排序）；失败返回空列表。"""
    try:
        async with async_session_factory() as db:
            return list((await db.execute(
                select(GlobalUserFact).where(GlobalUserFact.user_id == user_id)
                .order_by(GlobalUserFact.slot)
            )).scalars().all())
    except Exception:
        return []


async def build_user_now_text(user_id: int, max_tokens_hint: int = 300) -> str:
    """所有角色共享的「用户最新状态」分区文本；冲突时以它为准（提示词层面声明权威）。

    - 无任何事实 → "无"；
    - 按槽位中文标签逐行渲染，带「更新于 YYYY-MM-DD」；
    - 超出配额裁剪尾部（2 字符 ≈ 1 token，与 context 裁剪口径一致）。
    """
    rows = await get_active_user_facts(user_id)
    if not rows:
        return "无"
    label = {k: v[0] for k, v in MUTABLE_SLOTS.items()}
    budget = max_tokens_hint * 2  # 2 字符 ≈ 1 token（与 context 裁剪口径一致）
    lines: list[str] = []
    used = 0
    for r in rows:
        line = f"- {label.get(r.slot, r.slot)}：{r.value}（更新于 {str(r.valid_from or r.updated_at)[:10]}）"
        if used + len(line) > budget:
            break
        used += len(line)
        lines.append(line)
    return "\n".join(lines) or "无"
