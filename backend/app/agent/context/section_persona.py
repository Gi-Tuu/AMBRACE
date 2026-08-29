"""persona sections（步骤5）：人格上下文块 → 各模板槽。

从 ``context_builder.build_context_legacy`` 迁出，逻辑与旧版完全一致（零行为变化）：
- relationship / current_status / relationship_state（模板槽，不裁剪）；
- character_feelings / storyline_recall / recent_emotion（模板槽，按各自配额裁剪）；
- identity_profile / active_topics / storyline_status（模板槽，identity/storyline 裁剪）；
- user_emotion（规则器情绪，失败静默「无」）/ user_manual_state（手动八维状态，无值空串）；
- cognitive_plan（感知+规划指令，开关关闭时空串）；
- user_info（user_profile + user_notes → _build_user_info 整体 500 token 裁剪）。

``assemble_persona_context`` 在同一轮 context 构建内只算一次（经 ``ctx`` 缓存），
保证各槽取到完全相同的块（字节级一致）。
"""
from __future__ import annotations

import logging

from app.agent.context.sections import ContextSection, register_section, TARGET_TEMPLATE

_logger = logging.getLogger("agent.context.section_persona")


async def _persona(state: dict, ctx: dict) -> dict:
    """assemble_persona_context 结果（同轮缓存）。"""
    p = ctx.get("_persona")
    if p is None:
        from app.agent.persona import assemble_persona_context
        p = await assemble_persona_context(state.get("character_id"), state.get("user_id", 1))
        ctx["_persona"] = p
    return p


def _mk_persona_slot(name):
    async def _section(state, ctx):
        p = await _persona(state, ctx)
        return p.get(name) or ""
    return _section


async def user_emotion_section(state: dict, ctx: dict) -> str:
    """user_emotion 分区：规则器情绪感知（template 槽；失败静默「无」）。"""
    user_emotion = "无"
    try:
        emo = (state.get("perception") or {}).get("emotion") or ""
        if not emo:
            from app.utils.emotion import detect_user_emotion
            emo = detect_user_emotion(state.get("user_message", ""))
        if emo:
            user_emotion = emo
    except Exception as e:
        _logger.warning("Failed to detect user emotion: %s", e)
    return user_emotion


async def user_manual_state_section(state: dict, ctx: dict) -> str:
    """user_manual_state 分区：用户手动八维状态（template 槽；全默认或无则空串）。"""
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.user_state import UserState
    from app.agent.context_builder import _build_user_manual_state_text

    user_manual_state = ""
    try:
        async with async_session_factory() as db:
            _ur = await db.execute(select(UserState).where(UserState.user_id == state.get("user_id", 1)))
            _u = _ur.scalar_one_or_none()
        if _u is not None:
            _cn = {"mood": "心情", "body_temp": "体温", "desire": "性欲", "possessiveness": "占有欲",
                   "fatigue": "疲惫感", "sensitivity": "敏感度", "comfort": "舒适感", "anger": "怒气值"}
            _vals = {k: getattr(_u, k) for k in _cn}
            if any(v != 50 for v in _vals.values()):
                _parts = [f"{_cn[k]}{v}" for k, v in _vals.items() if v != 50]
                user_manual_state = _build_user_manual_state_text(_parts)
    except Exception as e:
        _logger.warning("Failed to load user states: %s", e)
    return user_manual_state


async def cognitive_plan_section(state: dict, ctx: dict) -> str:
    """cognitive_plan 分区：感知+规划指令（template 槽；开关关闭时空串）。"""
    cognitive_plan = ""
    if state.get("cognitive_loop_enabled") and state.get("perception"):
        try:
            from app.agent.perception import build_perception_section
            _sec = build_perception_section(state.get("perception"))
            _hint = (state.get("perception") or {}).get("length_hint") or "medium"
            _len_cn = {"long": "较长", "short": "简短", "medium": "适中"}.get(_hint, "适中")
            cognitive_plan = (
                (_sec + "\n" if _sec else "") +
                "- 开始回复前先在内心判断这次对话的类型与用户情绪，再决定策略（共情陪伴/直接回答/简短回应/认真接住）与篇幅（建议" + _len_cn + "）。\n"
                "- 规划完成后，先单独输出一行策略标记：【策略：<策略名>；长度：<短/中/长>】，再输出正文；每回合只输出一行策略标记。"
            )
        except Exception as e:
            _logger.warning("Cognitive plan build failed: %s", e)
            cognitive_plan = ""
    return cognitive_plan


async def user_info_section(state: dict, ctx: dict) -> str:
    """user_info 分区：用户画像 + 用户笔记 → 整体裁剪（template 槽）。"""
    from app.agent.context_builder import _build_user_info
    user_profile_text = ""
    user_notes_text = ""
    try:
        from app.agent.user_profile import build_user_profile_text
        user_profile_text = await build_user_profile_text(state.get("user_id", 1))
    except Exception:
        try:
            from sqlalchemy import select
            from app.db.database import async_session_factory
            from app.models.user import User
            async with async_session_factory() as db:
                user = (await db.execute(select(User).where(User.id == state.get("user_id", 1)))).scalar_one_or_none()
            user_name = (user.nickname or user.username or "\u7528\u6237") if user else "\u7528\u6237"
            user_profile_text = f"用户昵称: {user_name}"
        except Exception:
            user_profile_text = ""
    try:
        from app.agent.user_profile import build_user_notes_text
        user_notes_text = await build_user_notes_text(state.get("user_id", 1))
    except Exception as e:
        _logger.warning("Load user notes failed: %s", e)
        user_notes_text = ""
    return _build_user_info(user_profile_text, user_notes_text)


register_section(ContextSection(key="relationship", builder=_mk_persona_slot("relationship"), target=TARGET_TEMPLATE, slot="relationship", quota_tokens=0, order=10))
register_section(ContextSection(key="current_status", builder=_mk_persona_slot("current_status"), target=TARGET_TEMPLATE, slot="current_status", quota_tokens=0, order=11))
register_section(ContextSection(key="relationship_state", builder=_mk_persona_slot("relationship_state"), target=TARGET_TEMPLATE, slot="relationship_state", quota_tokens=0, order=12))
register_section(ContextSection(key="character_feelings", builder=_mk_persona_slot("character_feelings"), target=TARGET_TEMPLATE, slot="character_feelings", quota_tokens=300, order=13))
register_section(ContextSection(key="storyline_recall", builder=_mk_persona_slot("storyline_recall"), target=TARGET_TEMPLATE, slot="storyline_recall", quota_tokens=0, order=14))
register_section(ContextSection(key="recent_emotion", builder=_mk_persona_slot("recent_emotion"), target=TARGET_TEMPLATE, slot="recent_emotion", quota_tokens=300, order=15))
register_section(ContextSection(key="storyline_status", builder=_mk_persona_slot("storyline_status"), target=TARGET_TEMPLATE, slot="storyline_status", quota_tokens=300, order=16))
register_section(ContextSection(key="active_topics", builder=_mk_persona_slot("active_topics"), target=TARGET_TEMPLATE, slot="active_topics", quota_tokens=0, order=17))
register_section(ContextSection(key="identity_profile", builder=_mk_persona_slot("identity_profile"), target=TARGET_TEMPLATE, slot="identity_profile", quota_tokens=400, order=18))
register_section(ContextSection(key="user_emotion", builder=user_emotion_section, target=TARGET_TEMPLATE, slot="user_emotion", quota_tokens=300, order=19))
register_section(ContextSection(key="user_manual_state", builder=user_manual_state_section, target=TARGET_TEMPLATE, slot="user_manual_state", quota_tokens=300, order=20))
register_section(ContextSection(key="cognitive_plan", builder=cognitive_plan_section, target=TARGET_TEMPLATE, slot="cognitive_plan", quota_tokens=0, order=21))
register_section(ContextSection(key="user_info", builder=user_info_section, target=TARGET_TEMPLATE, slot="user_info", quota_tokens=500, order=22))
