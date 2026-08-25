"""chat_history section（步骤5）：最近完整消息 + 更早日概要 → 模板槽「chat_history」。

从 ``context_builder.build_context_legacy`` 迁出，逻辑与旧版完全一致（零行为变化）：
- 最近 1 天完整消息（超 ``MAX_RECENT_MESSAGES`` 的部分并入日摘要，更早部分按天分组补生成/占位）；
- 主语标注（用户(昵称/代词)/你(角色名)）+ 时间戳（同天 [HH:MM]，跨天 [MM-DD HH:MM]）；
- 图片/文件/语音/引用消息的注入格式与旧版一致；
- 更早消息概要（``_build_older_summaries``，单次最多补生成 1 天）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from app.agent.context.sections import ContextSection, register_section, TARGET_TEMPLATE

_logger = logging.getLogger("agent.context.section_summaries")


async def _load_char_and_user(state: dict) -> tuple:
    """载入角色与用户（供本 section 使用）；任一无则返回 (None, None)。"""
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.character import AICharacter
    from app.models.user import User
    char = None
    user = None
    try:
        async with async_session_factory() as db:
            char = (await db.execute(
                select(AICharacter).where(AICharacter.id == state["character_id"])
            )).scalar_one_or_none()
    except Exception as e:
        _logger.warning("summaries char load failed: %s", e)
    try:
        async with async_session_factory() as db:
            user = (await db.execute(
                select(User).where(User.id == state.get("user_id", 1))
            )).scalar_one_or_none()
    except Exception as e:
        _logger.warning("summaries user load failed: %s", e)
    return char, user


async def chat_history_section(state: dict, ctx: dict) -> str:
    """chat_history 分区：最近 1 天完整消息 + 更早日概要（template 槽；无消息返回空串）。"""
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.chat_message import ChatMessage
    from app.agent import context_builder as _cb

    char, user = await _load_char_and_user(state)
    if char is None:
        return ""
    char_name = char.name
    user_name = (user.nickname or user.username or "\u7528\u6237") if user else "\u7528\u6237"

    # 最近1天完整消息
    one_day_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    async with async_session_factory() as db:
        recent_result = await db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.session_id == state["session_id"],
                ChatMessage.created_at >= one_day_ago,
            )
            .order_by(ChatMessage.created_at.asc())
        )
        recent_msgs = list(recent_result.scalars().all())

    older_extra = []
    if len(recent_msgs) > _cb.MAX_RECENT_MESSAGES:
        older_extra = recent_msgs[:-_cb.MAX_RECENT_MESSAGES]
        recent_msgs = recent_msgs[-_cb.MAX_RECENT_MESSAGES:]

    import json as _json
    _gender_cn_user = "他" if (user and (user.gender or "").strip().lower() in ("male", "男")) else ("她" if (user and (user.gender or "").strip().lower() in ("female", "女")) else "TA")
    chat_history_lines = []
    _bj_today = datetime.now(timezone(timedelta(hours=8))).date()
    for msg in recent_msgs:
        sender = f"用户({user_name}/{_gender_cn_user})" if msg.sender_type == "user" else f"你({char_name})"
        _ts = ""
        try:
            if msg.created_at is not None:
                from app.utils.timeutil import shift_utc_naive
                _mt_bj = shift_utc_naive(msg.created_at, 8)
                _hhmm = f"{_mt_bj.hour:02d}:{_mt_bj.minute:02d}"
                _ts = f"[{_hhmm}] " if _mt_bj.date() == _bj_today else f"[{_mt_bj.month:02d}-{_mt_bj.day:02d} {_hhmm}] "
        except Exception:
            _ts = ""
        content = msg.content[:200] if len(msg.content) > 200 else msg.content
        if msg.image_url:
            desc_text = ""
            try:
                meta = _json.loads(msg.extra_meta or "{}")
                desc_text = (meta.get("image_desc") or {}).get("text", "") or ""
            except Exception:
                desc_text = ""
            if desc_text:
                line = f"[\u56fe\u7247\uff0c\u5185\u5bb9\uff1a{desc_text[:120]}]"
                if content:
                    line += f"\uff08\u7528\u6237\u8bf4\uff1a{content[:80]}\uff09"
                content = line
            else:
                content = f"[\u56fe\u7247] {content}" if content else "[\u56fe\u7247]"
        else:
            try:
                meta = _json.loads(msg.extra_meta or "{}")
            except Exception:
                meta = {}
            if meta.get("file"):
                f_meta = meta["file"]
                summary = (f_meta.get("summary") or "").strip()
                fname = f_meta.get("name") or ""
                if summary:
                    content = f"[\u6587\u4ef6\u300a{fname}\u300b\uff0c\u5185\u5bb9\u6458\u8981\uff1a{summary[:2000]}]"
                else:
                    fsize = f_meta.get("size") or ""
                    ftype = f_meta.get("type") or ""
                    content = f"[\u6587\u4ef6\u300a{fname}\u300b\uff08{ftype}{fsize}\uff09]"
            elif meta.get("voice"):
                v_meta = meta["voice"]
                tr = (v_meta.get("transcript") or "").strip()
                if tr:
                    content = f"[\u8bed\u97f3\u6d88\u606f\uff0c\u7528\u6237\u8bf4\uff1a{tr[:200]}]"
                else:
                    content = "[\u8bed\u97f3\u6d88\u606f\uff08\u6682\u65e0\u6cd5\u8f6c\u5199\uff09]"
        try:
            _qmeta = _json.loads(msg.extra_meta or "{}").get("quote")
        except Exception:
            _qmeta = None
        if isinstance(_qmeta, dict) and _qmeta.get("content"):
            _q_sender = _qmeta.get("sender")
            _q_label = user_name if _q_sender == "user" else char_name
            _q_text = str(_qmeta.get("content"))[:100]
            _q_line = f"（引用了{_q_label}的消息：{_q_text}）"
            content = f"{content} {_q_line}" if content else _q_line
        chat_history_lines.append(f"{_ts}{sender}: {content}")
    chat_history = "\n".join(chat_history_lines) or ""

    # 更早消息概要
    async with async_session_factory() as db:
        older_result = await db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.session_id == state["session_id"],
                ChatMessage.created_at < one_day_ago,
            )
            .order_by(ChatMessage.created_at.asc())
            .limit(5000)
        )
        older_msgs = list(older_result.scalars().all()) + older_extra

    if older_msgs:
        older_summary = await _cb._build_older_summaries(state, older_msgs, char_name, ctx["trim"])
        if older_summary:
            if chat_history:
                chat_history = older_summary + "\n\n---\n\n" + chat_history
            else:
                chat_history = older_summary

    return chat_history


register_section(ContextSection(
    key="chat_history",
    builder=chat_history_section,
    target=TARGET_TEMPLATE,
    slot="chat_history",
    quota_tokens=4000,
    order=36,
))
