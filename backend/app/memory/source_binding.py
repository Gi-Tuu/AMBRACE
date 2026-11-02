# -*- coding: utf-8 -*-
"""M2 2b：用户文本消息来源绑定与异步事务桥。

可信身份由聊天落库/会话实体提供；模型或请求不能自报 owner/actor。
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timezone

from sqlalchemy import text

from app.flags.agent_flags import AGENT_FLAGS
from app.memory import source_events as se
from app.models.chat import ChatMessage, ChatSession


_extraction_source: ContextVar[tuple[Binding, int] | None] = ContextVar("extraction_source", default=None)


class SourceIdentityError(RuntimeError):
    pass


def enabled() -> bool:
    return _extraction_source.get() is not None or bool(AGENT_FLAGS.get("memory_source_proof_v1", False))


@dataclass(frozen=True)
class Binding:
    event_uid: str
    owner_user_id: int
    character_id: int
    session_id: int
    content_sha256: str
    event_at: object


def _sha(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


async def sign_chat_message(db, msg: ChatMessage, *, user_id: int,
                            character_id: int) -> str | None:
    """在消息同一事务内签发来源；owner/char 只从 ChatSession 实体取。"""
    if msg.source_event_uid:
        return msg.source_event_uid
    session = await db.get(ChatSession, msg.session_id)
    if session is None or int(session.user_id) != int(user_id) \
            or int(session.character_id) != int(character_id):
        raise ValueError("source_session_scope")
    if msg.sender_type != "user" or msg.id is None:
        raise ValueError("source_not_user_message")
    at = msg.created_at
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    uid = await db.run_sync(lambda sync_db: se.issue_source_event(
        sync_db.connection(), kind="chat_message", source_row_id=msg.id,
        owner_user_id=int(session.user_id), character_id=int(session.character_id),
        event_at=at, content=msg.content, session_id=msg.session_id))
    msg.source_event_uid = uid
    return uid


def _resolve_sync(conn, *, msg_id: int, user_id: int, character_id: int,
                  expected_uid: str | None):
    msg = conn.execute(text(
        "SELECT id,session_id,sender_type,content,source_event_uid "
        "FROM chat_messages WHERE id=:m"), {"m": msg_id}).mappings().first()
    if msg is None or msg["sender_type"] != "user" or not msg["source_event_uid"]:
        return None
    if expected_uid and expected_uid != msg["source_event_uid"]:
        return None
    sess = conn.execute(text(
        "SELECT user_id,character_id FROM chat_sessions WHERE id=:s"),
        {"s": msg["session_id"]}).mappings().first()
    if sess is None or int(sess["user_id"]) != int(user_id) \
            or int(sess["character_id"]) != int(character_id):
        return None
    src = conn.execute(text(
        "SELECT * FROM memory_source_events WHERE event_uid=:u"),
        {"u": msg["source_event_uid"]}).mappings().first()
    if src is None or not se.visible(conn, src["event_uid"]):
        return None
    if int(src["owner_user_id"]) != int(user_id) \
            or int(src["character_id"]) != int(character_id) \
            or int(src["source_row_id"]) != int(msg_id) \
            or int(src["session_id"] or 0) != int(msg["session_id"]) \
            or src["content_sha256"] != _sha(msg["content"]):
        return None
    return Binding(event_uid=src["event_uid"], owner_user_id=int(user_id),
                   character_id=int(character_id), session_id=int(msg["session_id"]),
                   content_sha256=src["content_sha256"], event_at=src["event_at"])


async def resolve_chat_binding(db, *, user_id: int, character_id: int,
                               source: str | None, source_id: int | None,
                               expected_uid: str | None = None) -> Binding | None:
    """写入口按 source_id 解析并复核真实消息/来源；不是只校验 UID 格式。"""
    if not enabled() or source != "chat" or source_id is None:
        return None
    return await db.run_sync(lambda sync_db: _resolve_sync(
        sync_db.connection(), msg_id=int(source_id), user_id=int(user_id),
        character_id=int(character_id), expected_uid=expected_uid))


async def bind_memory(db, memory, binding: Binding, *, action: str) -> str:
    """在 Memory 已 flush、同一 AsyncSession 事务内写权威 receipt。"""
    key = f"m2b:{binding.event_uid}:{int(memory.id)}:{action}"
    return await db.run_sync(lambda sync_db: se.bind_existing_memory(
        sync_db.connection(), event_uid=binding.event_uid,
        memory_id=int(memory.id), owner_user_id=int(binding.owner_user_id),
        character_id=int(binding.character_id), action=action,
        idempotency_key=key))


async def revoke_chat_message(db, msg: ChatMessage, *, user_id: int) -> None:
    """删除消息事务内撤销其来源；无来源/关闸时 no-op。"""
    if not enabled() or not getattr(msg, "source_event_uid", None):
        return
    session = await db.get(ChatSession, msg.session_id)
    if session is None or int(session.user_id) != int(user_id):
        raise ValueError("source_session_scope")
    await db.run_sync(lambda sync_db: se.revoke_source_event(
        sync_db.connection(), event_uid=msg.source_event_uid,
        owner_user_id=int(session.user_id), character_id=int(session.character_id)))


async def validate_extraction_source(db, *, user_id, character_id, session_id,
                                     source_id, user_message, expected_uid=None) -> Binding:
    if source_id is None:
        raise SourceIdentityError("extraction_source_missing")
    binding = await db.run_sync(lambda sync_db: _resolve_sync(
        sync_db.connection(), msg_id=int(source_id), user_id=int(user_id),
        character_id=int(character_id), expected_uid=expected_uid))
    if binding is None or binding.session_id != session_id \
            or binding.content_sha256 != _sha(user_message):
        raise SourceIdentityError("extraction_source_changed")
    await _validate_extraction_access(db, binding)
    return binding


async def _validate_extraction_access(db, binding):
    from app.application.tenant_service import same_tenant
    row = (await db.execute(text(
        "SELECT s.is_active AS session_active,c.is_active AS character_active,"
        "c.user_id AS character_owner,u.deleted_at,u.disabled_at "
        "FROM chat_sessions s JOIN ai_characters c ON c.id=s.character_id "
        "JOIN users u ON u.id=s.user_id WHERE s.id=:s "
        "AND s.user_id=:u AND s.character_id=:c"),
        {"s": binding.session_id, "u": binding.owner_user_id,
         "c": binding.character_id})).mappings().first()
    if row is None or not row["session_active"] or not row["character_active"] \
            or row["deleted_at"] is not None or row["disabled_at"] is not None \
            or not await same_tenant(db, binding.owner_user_id, row["character_owner"]):
        raise SourceIdentityError("extraction_scope_changed")


@contextmanager
def pin_extraction_source(binding, source_id):
    token = _extraction_source.set((binding, source_id))
    try:
        yield
    finally:
        _extraction_source.reset(token)


async def _validate_pinned_source(db):
    pin = _extraction_source.get()
    if pin is None:
        return
    expected, source_id = pin
    current = await db.run_sync(lambda sync_db: _resolve_sync(
        sync_db.connection(), msg_id=int(source_id), user_id=expected.owner_user_id,
        character_id=expected.character_id, expected_uid=expected.event_uid))
    if current is None or current != expected:
        raise SourceIdentityError("extraction_source_changed")
    await _validate_extraction_access(db, current)


def pinned_source_uid() -> str | None:
    pin = _extraction_source.get()
    return pin[0].event_uid if pin is not None else None


def pinned_source_binding():
    pin = _extraction_source.get()
    return pin[0] if pin is not None else None


async def check_extraction_source():
    if _extraction_source.get() is None:
        return
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        await _validate_pinned_source(db)


async def guard_extraction_write(db):
    pin = _extraction_source.get()
    if pin is None:
        return
    # Acquire the SQLite writer lock before validation and retain it through this commit.
    with db.no_autoflush:
        await db.execute(text(
            "UPDATE memory_source_events SET event_uid=event_uid WHERE event_uid=:uid"),
            {"uid": pin[0].event_uid})
        await _validate_pinned_source(db)

