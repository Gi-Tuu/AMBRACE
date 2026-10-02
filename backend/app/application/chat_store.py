"""会话与落库层：会话解析/创建、用户消息落库、未读与已读维护。

本模块自 ``application/chat_service.py`` 逐字节搬入（A20 批 5 第一刀，2026-10-02）。
边界＝**会话解析/创建、消息落库、未读与已读维护**；不发消息、不跑 agent。

机械改写只有两类（判定/文案/返回值一字未改）：

1. 缩进（原样为 0，未动）。
2. 被 tests/ 按 chat_service 打桩的名字与**本组兄弟函数互调**改走 ``_cs.<name>``
   （函数体内 ``from app.application import chat_service as _cs``，**不得提到顶层**——顶层会成环）：
   ``async_session_factory``（``setattr(chat_service, "async_session_factory", …)`` 实测 6 处）、
   ``spawn_background``（2 处）、``append_domain_event``（1 处），
   兄弟调用 ``create_session``→``get_latest_session_id`` / ``mark_session_read``→``get_owned_session``
   （``setattr(chat_service, "get_latest_session_id", …)`` 实测 19 处，是最多的那类桩）。
   本模块**不得**具名 import 这四名，否则桩静默失效 ⇒ 退化成真查库/真发事件。

其余依赖（模型/SQLAlchemy/标准库）按原模块原名直接 import；logger 名沿用 ``services.chat``（D-1 口径）。
chat_service 侧保留全部具名重导出，库内 6 个模块与字符串路径打桩均不受影响。
"""
import json
from datetime import datetime, timezone

from sqlalchemy import select, func, and_, or_

from app.models.chat import ChatSession
from app.models.chat import ChatMessage
from app.models.character import AICharacter
from app.events.types import EventType as _ET
from app.utils.logger import get_logger

_logger = get_logger("services.chat")


async def get_latest_session_id(user_id: int | None, character_id: int) -> int | None:
    """选择该角色真正活跃的会话：按最新一条消息时间排序（无消息回退创建时间/ID）。

    历史坑：mark_session_read 联动标记已读曾触发 ORM onupdate 污染 updated_at，
    同角色多会话 updated_at 相同时按 updated_at 排序会选错会话（表现为聊天记录"只剩早期"）。
    现在统一以消息时间为准。
    """
    from app.application import chat_service as _cs  # 被桩依赖与兄弟调用在 chat_service 命名空间现取
    conds = [
        ChatSession.character_id == character_id,
        ChatSession.is_active == True,
    ]
    if user_id is not None:
        conds.append(ChatSession.user_id == user_id)
    async with _cs.async_session_factory() as db:
        result = await db.execute(
            select(ChatSession.id, func.max(ChatMessage.created_at).label("last_msg_at"))
            .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.id)
            .where(*conds)
            .group_by(ChatSession.id)
            .order_by(
                func.coalesce(func.max(ChatMessage.created_at), ChatSession.created_at).desc(),
                ChatSession.id.desc(),
            )
            .limit(1)
        )
        row = result.first()
        return row[0] if row else None


async def create_session(user_id: int, character_id: int) -> dict:
    """获取或创建聊天会话，优先复用最新活跃会话（按最新消息时间选会话）"""
    from app.application import chat_service as _cs  # 被桩依赖与兄弟调用在 chat_service 命名空间现取
    existing_id = await _cs.get_latest_session_id(user_id, character_id)
    if existing_id:
        _logger.debug("Reusing existing session: id=%d", existing_id)
        return {"id": existing_id, "character_id": character_id, "greeting": None}

    async with _cs.async_session_factory() as db:

        session = ChatSession(user_id=user_id, character_id=character_id)
        db.add(session)
        await db.flush()
        await db.refresh(session)

        result = await db.execute(
            select(AICharacter).where(AICharacter.id == character_id)
        )
        char = result.scalar_one_or_none()
        greeting = char.greeting_message if char and char.greeting_message else ""

        if greeting:
            greeting_msg = ChatMessage(
                session_id=session.id, sender_type="ai", content=greeting,
            )
            db.add(greeting_msg)
            await db.flush()
            await db.commit()
        await db.commit()
        # 3.10 事件流水（P0）：仅新建分支发会话创建事件（复用分支上面已 return）
        await _cs.append_domain_event(
            _ET.CHAT_SESSION_CREATED.value, "chat_session", session.id,
            entity_type="chat_session", entity_id=session.id,
            actor_type="user", actor_id=user_id,
            payload={"character_id": character_id, "with_greeting": bool(greeting)},
            origin="user_message",
        )
        _logger.info("New session created: id=%d char=%d greeting=%s", session.id, character_id, bool(greeting))
        return {"id": session.id, "character_id": character_id, "greeting": greeting}


async def _persist_user_message(
    session_id: int, user_id: int, character_id: int, content: str,
    quote: dict | None = None, save_user_message: bool = True,
    shared_memory: bool = False, channel: str | None = None,
) -> tuple[int | None, dict | None]:
    """用户消息落库，返回 (user_msg_id, user_msg_info)。

    save_user_message=False 跳过落库；shared_memory=True 触发 Shared Memory 标记（chunked）。
    channel 非空时把渠道来源标记合并进 extra_meta JSON（保留 quote 语义：两者可同时存在，
    谁都不空才落 JSON；两者皆空则 extra_meta 保持 None，行为与现状零变化）。
    """
    from app.application import chat_service as _cs  # 被桩依赖与兄弟调用在 chat_service 命名空间现取
    user_msg_id = None
    user_msg_info = None
    if not save_user_message:
        return user_msg_id, user_msg_info
    async with _cs.async_session_factory() as db:
        _meta = None
        if quote and isinstance(quote, dict):
            _meta = {"quote": quote}
        if channel:
            if _meta is None:
                _meta = {}
            _meta["channel"] = channel
        _q = json.dumps(_meta, ensure_ascii=False) if _meta else None
        um = ChatMessage(session_id=session_id, sender_type="user", content=content,
                         extra_meta=_q)
        db.add(um)
        await db.flush()
        user_msg_id = um.id
        # P-fix（2026-08-31）：SSE 流式路径经本函数落用户消息时补刷新 chat_sessions.updated_at，
        # 与 chunked/主动路径一致（agent 落库处同样写 updated_at=now naive UTC）。
        _sess = (await db.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )).scalar_one_or_none()
        if _sess:
            _sess.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        user_msg_info = {
            "id": um.id, "session_id": session_id, "sender_type": "user",
            "content": um.content, "created_at": um.created_at.isoformat(),
            "extra_meta": um.extra_meta,
        }
    # 3.10 事件流水（P0）：用户消息（HTTP/WS-chunked/SSE 三路径统一口；save_user_message=False 时不发）
    if user_msg_id:
        await _cs.append_domain_event(
            _ET.CHAT_MESSAGE_SENT.value, "chat_session", session_id,
            entity_type="chat_message", entity_id=user_msg_id,
            actor_type="user", actor_id=user_id,
            payload={"sender_type": "user", "route": "persist",
                     "content": content, "has_quote": bool(quote)},
            idempotency_key=f"chat.message_sent:chat_message:{user_msg_id}",
            origin="user_message",
        )
    # Shared Memory（Phase C，2026-08-14）：用户消息含“记住/第一次/纪念日”等标记意图 → 异步创建共同经历
    if shared_memory:
        try:

            async def _mark_shared():
                try:
                    from app.memory.shared_events import maybe_create_shared_event
                    async with _cs.async_session_factory() as _db2:
                        await maybe_create_shared_event(_db2, user_id, character_id, content)
                except Exception as _e:
                    _logger.warning("shared event mark failed: %s", _e)

            _cs.spawn_background(_mark_shared())
        except Exception:
            pass
    return user_msg_id, user_msg_info


async def get_owned_session(db, session_id: int, user_id: int):
    """按用户归属获取会话，不存在或非本人返回 None（api/chat.py 公共依赖）"""
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session or session.user_id != user_id or not session.is_active:
        return None
    return session


async def get_unread_counts(db, user_id: int) -> list[dict]:
    """每个角色的未读 AI 消息数（一条 GROUP BY 查询，替代逐会话 COUNT 的 N+1）"""
    result = await db.execute(
        select(
            ChatSession.id,
            ChatSession.character_id,
            func.count(ChatMessage.id).label("cnt"),
        )
        .outerjoin(
            ChatMessage,
            and_(
                ChatMessage.session_id == ChatSession.id,
                ChatMessage.sender_type == "ai",
                or_(
                    ChatSession.last_read_at.is_(None),
                    ChatMessage.created_at > ChatSession.last_read_at,
                ),
            ),
        )
        .where(ChatSession.user_id == user_id, ChatSession.is_active == True)
        .group_by(ChatSession.id, ChatSession.character_id)
    )
    rows = result.all()
    return [
        {"session_id": r.id, "character_id": r.character_id, "count": r.cnt}
        for r in rows
        if r.cnt > 0
    ]


async def mark_session_read(db, session_id: int, user_id: int) -> bool:
    """标记会话已读（联动同角色其他活跃会话，避免残留未读）；会话不存在返回 False。

    历史遗留：同一角色可能残留多个活跃会话，点进聊天页时一并标记已读。
    用原生 SQL 更新，避免 SQLAlchemy ORM 会话自动附加 updated_at=onupdate（CURRENT_TIMESTAMP）
    污染 updated_at，导致"按最新会话复用"选错（聊天记录只剩早期的问题）。
    """
    from app.application import chat_service as _cs  # 被桩依赖与兄弟调用在 chat_service 命名空间现取
    from sqlalchemy import text

    session = await _cs.get_owned_session(db, session_id, user_id)
    if session is None:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # 库内统一 naive UTC
    # F-14（v3.4.6 审查）：先取将被联动标记的全部活跃会话 id，事件按会话各落一条（聚合各自
    # session）；此前只挂传入 session_id，被联动会话没有事件。
    _rows = await db.execute(
        text(
            "SELECT id FROM chat_sessions "
            "WHERE user_id = :u AND character_id = :c AND is_active = 1"
        ),
        {"u": session.user_id, "c": session.character_id},
    )
    _affected_ids = [r[0] for r in _rows.all()]
    await db.execute(
        text(
            "UPDATE chat_sessions SET last_read_at = :t "
            "WHERE user_id = :u AND character_id = :c AND is_active = 1"
        ),
        {"t": now, "u": session.user_id, "c": session.character_id},
    )
    await db.commit()
    # 3.10 事件流水（P0）：已读流转（含同角色其他活跃会话联动）。F-14：幂等键改为
    # 「session + UTC 自然日」——原完整时间戳完全不幂等，高频已读导致事件膨胀；同日
    # 重复只保留一条。仍用原生 SQL 更新，不污染 updated_at（见上方历史坑注释）。
    _day = now.strftime("%Y-%m-%d")
    for _sid in _affected_ids:
        await _cs.append_domain_event(
            _ET.CHAT_SESSION_READ.value, "chat_session", _sid,
            actor_type="user", actor_id=user_id,
            payload={"last_read_at": now.isoformat(), "character_id": session.character_id},
            idempotency_key=f"chat.session_read:{_sid}:{_day}",
            origin="user_message",
        )
    return True
