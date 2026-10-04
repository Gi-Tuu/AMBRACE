# -*- coding: utf-8 -*-
"""用户当前现状「权威锚点」（C3，2026-09-10）：主聊天 / 主动消息 / 记忆复习三通道共用。

与 C1 时态标签一正一反：记忆行带［往事］/［旧安排·已过期］（声明「那是过去」），本锚点正向
声明「TA 现在怎样」（权威现状），防止旧位置/旧状态被当此刻续写。

数据源（全部为用户已授权或 flag 门控，默认零新增外呼、零越界、失败静默）：
  源1 per-char WorldFact（subject_type=user，predicate∈status/activity/location/mood，每谓词最新且过 C2 新鲜窗）；
  源2 User 表城市（location_enabled 已授权的位置感知，独立老开关，不依赖 global_user_facts）；
  源3 GlobalUserFact（已启用槽 + 共享 location：user_current_location_share 默认开、不吃细槽总闸；
      relationship/health 仍须显式开启，共享读路径不旁路 opt-in）。
无任何现状 → 返回空串（调用方据此不注入，默认零行为变化）。
"""
from __future__ import annotations

_ANCHOR_HEADER = "TA 当前已知现状（以此为准，旧记忆不得与此矛盾）"
_PREDICATE_LABELS = {"location": "位置", "status": "状态", "mood": "心情", "activity": "近况"}
_SLOT_LABELS = {"location": "位置", "job": "工作", "relationship": "感情",
                "living": "居住", "goal_state": "近期目标", "health": "健康"}
# per-char 现状的取值顺序（位置优先——它最容易与旧记忆矛盾）。A28-S2：把顺序从函数体里提出来当常量，
# 结构化读取与文本 renderer 共用同一份，避免两处各写一遍而分叉。
_ANCHOR_PREDICATE_ORDER = ("location", "status", "mood", "activity")


async def _char_world_user_facts(char_id: int, user_id: int) -> dict[str, str]:
    """源1：per-char WorldFact 中 subject=user 的现状，每谓词取最新（当前无写入点，预留 + 兼容未来）。

    过滤条件与 memory_review 旧锚点对齐（含 subject_id==user_id），仅把谓词扩到含 mood、
    并由「只取一条」改为「每谓词各取最新一条」。
    """
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.memory import WorldFact
        from app.events.facts import _TRANSIENT_FRESH_HOURS, _predicate_fresh
        from app.utils.timeutil import now_naive_utc
        now = now_naive_utc()
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(WorldFact.predicate, WorldFact.object_value, WorldFact.asserted_at)
                .where(
                    WorldFact.character_id == char_id,
                    WorldFact.user_id == user_id,
                    WorldFact.subject_type == "user",
                    WorldFact.subject_id == user_id,
                    WorldFact.status == "active",
                    WorldFact.predicate.in_(("status", "activity", "location", "mood")),
                )
                .order_by(WorldFact.asserted_at.desc())
            )).all()
    except Exception:
        return {}
    latest: dict[str, str] = {}
    for p, v, asserted_at in rows:  # 已倒序，每谓词取第一条非空
        if p in latest or not v:
            continue
        hours = _TRANSIENT_FRESH_HOURS.get(p)
        if hours is not None and not _predicate_fresh(asserted_at, now, hours):
            continue  # C2：过期现状不注入（与 facts.get_active_facts 同口径）
        latest[p] = v
    # 死读路径埋点（P0 语义统一 · 第 2 步，2026-09-29；**零行为**）：方案 §1.4 判定本源
    # 「subject_type=user 全仓零写入 ⇒ 恒空」，7 天计数恒 0 即证伪「预留」，供步骤 5 / 断点 #9
    # 决定删除本查询或补写入。只计数：obs_event 内部 flag 门控 + fire-and-forget + 自带吞异常，
    # 再包一层 try 保证埋点任何情况都不影响返回值。
    try:
        from app.memory.observability import obs_event
        obs_event(char_id, "user_subject_world_fact_rows",
                  {"user_id": user_id, "rows": len(rows), "kept": len(latest),
                   "predicates": sorted(latest)})
    except Exception:
        pass
    return latest


async def _profile_location(user_id: int) -> str | None:
    """源2：User 表当前城市（仅 location_enabled 已授权；location_city 优先，其次手填城市）。"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.user import User
        async with async_session_factory() as db:
            u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if u is None or not getattr(u, "location_enabled", False):
            return None
        return (getattr(u, "location_city", None) or getattr(u, "user_location", None) or None)
    except Exception:
        return None


async def _global_slot_facts(user_id: int) -> dict[str, str]:
    """源3：GlobalUserFact 可共享槽（已启用槽 + 共享 location，flag 门控）。

    2026-09-17 批次二任务2：改用 get_shared_user_facts —— 位置槽走独立开关
    user_current_location_share（默认开、不吃细槽总闸），低活跃朋友角色也能拿到权威位置；
    relationship/health 仍须显式开启（红线：共享读路径不旁路 opt-in）。
    """
    try:
        from app.memory.user_facts import get_shared_user_facts
        return await get_shared_user_facts(user_id)
    except Exception:
        return {}


async def get_current_user_state(*, character_id: int, user_id: int,
                                 include_profile_location: bool = True) -> dict:
    """结构化读取三源现状（A28-S2，2026-10-05）：`{"entries": [{key,label,value,source}], "empty": bool}`。

    与 `current_user_state_anchor()` 的关系＝**读取与渲染分开**（原文方案 §六）：
    Workspace / Reflection / Proactivity 将来读结构化结果，prompt 继续用文本 renderer，两者共用同一份取数与同一套优先级。

    口径与历史逐字节一致：per-char 现状按 `_ANCHOR_PREDICATE_ORDER` 优先，其次 User 表城市，再次共享槽；
    **同标签去重不覆盖**（位置已有就不替换）。任何异常 → 空 entries（失败静默、绝不抛给主链路）。

    只装**三源真给得出**的字段：库里没有 mood 的写入点就是没有，不在此虚构 `fresh_until`/`authority` 之类。
    """
    entries: list[dict] = []
    seen_labels: set[str] = set()

    def _push(key: str, label: str, value, source: str) -> None:
        if not value or label in seen_labels:
            return
        seen_labels.add(label)
        entries.append({"key": key, "label": label, "value": str(value), "source": source})

    try:
        char_facts = await _char_world_user_facts(character_id, user_id)
        for p in _ANCHOR_PREDICATE_ORDER:
            _push(p, _PREDICATE_LABELS[p], char_facts.get(p), "world_fact")
        if include_profile_location:
            _push("location", "位置", await _profile_location(user_id), "profile_location")
        for slot, val in (await _global_slot_facts(user_id)).items():
            _push(slot, _SLOT_LABELS.get(slot, slot), val, "global_slot")
    except Exception:
        return {"entries": [], "empty": True}
    return {"entries": entries, "empty": not entries}


def render_current_state_anchor(entries, max_chars: int = 200) -> str:
    """把结构化现状渲染成权威锚点文本——**输出与拆分前逐字节相同**（含顺序、`…` 截断与首尾换行）。"""
    items = list(entries or [])
    if not items:
        return ""
    body = "；".join(f"{e['label']}：{e['value']}" for e in items)
    if len(body) > max_chars:
        body = body[:max_chars].rstrip("，；,;") + "…"
    return f"\n{_ANCHOR_HEADER}：{body}。\n"


async def current_user_state_anchor(*, character_id: int, user_id: int,
                                    include_profile_location: bool = True,
                                    max_chars: int = 200) -> str:
    """聚合三源 → 一段权威现状文本；无现状返回 ""。绝不抛错阻断主回复。

    A28-S2 后本函数＝`get_current_user_state()` → `render_current_state_anchor()` 的薄封装。
    **入口名与签名保持不变**：它是 4 处生产调用点（section_current_state / reflection / runtime / world_state）
    与 42 处测试打桩共同钉住的接缝——改名或换参数形态会让打桩静默失效（搬家四律 R1）。
    """
    try:
        state = await get_current_user_state(character_id=character_id, user_id=user_id,
                                             include_profile_location=include_profile_location)
        return render_current_state_anchor(state["entries"], max_chars=max_chars)
    except Exception:
        return ""
