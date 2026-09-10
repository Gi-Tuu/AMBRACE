# -*- coding: utf-8 -*-
"""用户当前现状「权威锚点」（C3，2026-09-10）：主聊天 / 主动消息 / 记忆复习三通道共用。

与 C1 时态标签一正一反：记忆行带［往事］/［旧安排·已过期］（声明「那是过去」），本锚点正向
声明「TA 现在怎样」（权威现状），防止旧位置/旧状态被当此刻续写。

数据源（全部为用户已授权或 flag 门控，默认零新增外呼、零越界、失败静默）：
  源1 per-char WorldFact（subject_type=user，predicate∈status/activity/location/mood，每谓词最新且过 C2 新鲜窗）；
  源2 User 表城市（location_enabled 已授权的位置感知，独立老开关，不依赖 global_user_facts）；
  源3 GlobalUserFact（仅 enabled_user_fact_slots() 启用槽，flag 门控，默认空）。
无任何现状 → 返回空串（调用方据此不注入，默认零行为变化）。
"""
from __future__ import annotations

_ANCHOR_HEADER = "TA 当前已知现状（以此为准，旧记忆不得与此矛盾）"
_PREDICATE_LABELS = {"location": "位置", "status": "状态", "mood": "心情", "activity": "近况"}
_SLOT_LABELS = {"location": "位置", "job": "工作", "relationship": "感情",
                "living": "居住", "goal_state": "近期目标", "health": "健康"}


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
    """源3：GlobalUserFact 启用槽（flag 门控，默认空）。"""
    try:
        from app.memory.user_facts import get_active_user_facts, enabled_user_fact_slots
        if not enabled_user_fact_slots():
            return {}
        return {f.slot: f.value for f in (await get_active_user_facts(user_id)) if f.value}
    except Exception:
        return {}


async def current_user_state_anchor(*, character_id: int, user_id: int,
                                    include_profile_location: bool = True,
                                    max_chars: int = 200) -> str:
    """聚合三源 → 一段权威现状文本；无现状返回 ""。绝不抛错阻断主回复。"""
    try:
        parts: dict[str, str] = {}
        char_facts = await _char_world_user_facts(character_id, user_id)
        for p in ("location", "status", "mood", "activity"):  # 位置优先（最易与旧记忆矛盾）
            if char_facts.get(p):
                parts[_PREDICATE_LABELS[p]] = char_facts[p]
        if include_profile_location:
            prof_loc = await _profile_location(user_id)
            if prof_loc and "位置" not in parts:  # 不覆盖 per-char 现状、去重
                parts["位置"] = prof_loc
        for slot, val in (await _global_slot_facts(user_id)).items():
            label = _SLOT_LABELS.get(slot, slot)
            if val and label not in parts:  # 同标签去重，位置以 per-char/profile 优先
                parts[label] = val
        if not parts:
            return ""
        body = "；".join(f"{k}：{v}" for k, v in parts.items())
        if len(body) > max_chars:
            body = body[:max_chars].rstrip("，；,;") + "…"
        return f"\n{_ANCHOR_HEADER}：{body}。\n"
    except Exception:
        return ""
