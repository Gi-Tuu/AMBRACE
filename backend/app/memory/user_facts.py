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

# ── 细粒度槽开关（2026-09-10，用户拍板）──────────────────────────────────
# slot -> AGENT_FLAGS 键；与 MUTABLE_SLOTS 的 6 槽一一对应。全部默认 False（含 location）。
USER_FACT_SLOT_FLAGS: dict[str, str] = {
    "location": "user_fact_location",
    "job": "user_fact_job",
    "relationship": "user_fact_relationship",
    "living": "user_fact_living",
    "goal_state": "user_fact_goal_state",
    "health": "user_fact_health",
}


def user_fact_slot_enabled(slot: str) -> bool:
    """某事实槽是否启用：总闸 global_user_facts 开→全槽启用；否则看该槽独立 flag（默认全关）。

    延迟读取 AGENT_FLAGS（函数内 import）：保证 runtime flag 热更即时生效，且避免模块级循环 import。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        if bool(AGENT_FLAGS.get("global_user_facts", False)):
            return True
        flag = USER_FACT_SLOT_FLAGS.get(slot)
        return bool(AGENT_FLAGS.get(flag, False)) if flag else False
    except Exception:
        return False


def enabled_user_fact_slots() -> list[str]:
    """当前启用的槽列表（按 MUTABLE_SLOTS 声明顺序）；全关返回空列表。"""
    return [s for s in MUTABLE_SLOTS if user_fact_slot_enabled(s)]


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


async def get_active_user_facts(user_id: int, slots: list[str] | None = None) -> list[GlobalUserFact]:
    """取某用户事实槽（按 slot 排序）；失败返回空列表。

    slots=None → 只取【已启用槽】（enabled_user_fact_slots，安全默认）：使 [USER NOW]、
                 跨角色对齐 align/sweep 等既有调用点天然只处理启用槽，无需逐点加 if；
    slots=列表 → 只取白名单槽（测试 / 定向调用）；显式传空列表 → 返回空。
    注：(user_id, slot) 唯一约束保证每槽只有一行（单值取代），无需再按槽去重。
    """
    if slots is None:
        slots = enabled_user_fact_slots()
    if not slots:
        return []
    try:
        async with async_session_factory() as db:
            return list((await db.execute(
                select(GlobalUserFact).where(
                    GlobalUserFact.user_id == user_id,
                    GlobalUserFact.slot.in_(list(slots)),
                ).order_by(GlobalUserFact.slot)
            )).scalars().all())
    except Exception:
        return []


async def build_user_now_text(user_id: int, slots: list[str] | None = None,
                              max_tokens_hint: int = 300) -> str:
    """所有角色共享的「用户最新状态」分区文本；冲突时以它为准（提示词层面声明权威）。

    - 无任何事实 → "无"；
    - 按槽位中文标签逐行渲染，带「更新于 YYYY-MM-DD」；
    - 超出配额裁剪尾部（2 字符 ≈ 1 token，与 context 裁剪口径一致）；
    - slots 语义同 get_active_user_facts（None=只取启用槽）。
    """
    rows = await get_active_user_facts(user_id, slots=slots)
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


# ── C2-③ 回家/到家信号位置收敛（2026-09-10）────────────────────────────
# 计划/假想语气：出现即判定为「尚未发生」，不应当作现状（归前瞻意图处理）
_HOME_PLAN_BLOCKERS = (
    "想", "打算", "准备", "计划", "明天", "后天", "下周", "周末", "等会", "一会",
    "待会", "以后", "要是", "如果", "等我", "过两天", "放假", "考完", "忙完",
)
# 明确「回到/到达 家」；(?!乡|老|娘) 排除 家乡/老家/娘家（城市未知，不臆造）
_HOME_RE = re.compile(r"(?:回到了?|到了?|回了?|返程回到?|已经回到?)\s*家(?!乡|老|娘)(?:里|中|了|啦|咯|喽)?")
# 异地宾语（回学校/回公司/回酒店…）：不是"回家"，交给普通地点归槽
_ELSEWHERE_RE = re.compile(r"回(?:到)?(?!家)\s*[\u4e00-\u9fa5]{0,6}(?:学校|校区|公司|宿舍|酒店|宾馆|单位|厂里)")


def detect_home_return(text: str) -> bool:
    """用户是否在表达「已经回到自己家/居住地」（现状语气）。保守，宁可不命中。

    命中歧义（如「回了趟家又走了」= 已离开）按保守口径不命中（NEEDS_RUNTIME_VERIFICATION）。
    """
    t = (text or "").strip()
    if not t:
        return False
    if any(b in t for b in _HOME_PLAN_BLOCKERS):
        return False
    if _ELSEWHERE_RE.search(t):
        return False
    return bool(_HOME_RE.search(t))


async def settle_location_on_home_return(user_id: int, text: str,
                                         source: str = "chat_home_return") -> bool:
    """回家信号 → location 槽收敛到常驻城市（旧出行城市由 upsert 记入 previous_value）。

    - location 槽未启用 / 非回家语气 → 不写，返回 False；
    - 常驻城市取 User.user_location（用户自设城市，home 代理）；取不到则【不写】（不臆造城市），
      旧位置交由 location 72h 新鲜窗自然过期；
    - 收敛写入复用 upsert_user_fact（单值取代 + previous_value 留痕，零新机制）。
    """
    if not user_fact_slot_enabled("location"):
        return False
    if not detect_home_return(text):
        return False
    home_city: str | None = None
    try:
        from app.models.user import User
        async with async_session_factory() as db:
            u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if u is not None:
            home_city = (getattr(u, "user_location", None) or "").strip() or None
    except Exception:
        home_city = None
    if not home_city:
        return False
    try:
        change = await upsert_user_fact(user_id, "location", home_city, source=source)
        return change is not None
    except Exception:
        return False
