# -*- coding: utf-8 -*-
"""M2 2b：用户文本消息来源绑定与异步事务桥。

可信身份由聊天落库/会话实体提供；模型或请求不能自报 owner/actor。
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text

from app.flags.agent_flags import AGENT_FLAGS
from app.memory import source_events as se
from app.models.chat import ChatMessage, ChatSession


_extraction_source: ContextVar[tuple[Binding, int] | None] = ContextVar("extraction_source", default=None)


class SourceIdentityError(RuntimeError):
    pass


def enabled() -> bool:
    return _extraction_source.get() is not None or bool(AGENT_FLAGS.get("memory_source_proof_v1", False))


async def enabled_for(user_id: int) -> bool:
    """按账号解析来源证明闸（用户级灰度支持，2026-11-07）。

    走 `resolve_flag` 解析链（server_locked → 全局 → 该账号 user_runtime_flags 覆盖），
    任何异常 fail-open 回全局 `enabled()`。签名/落库调用方（异步上下文）请用本函数；
    `enabled()`（进程级全局）保留给无法携带账号的场合。
    """
    try:
        if not user_id:
            return enabled()
        from app.application.flag_service import resolve_flag
        return bool(await resolve_flag("memory_source_proof_v1", user_id))
    except Exception:  # noqa: BLE001 —— fail-open 回全局值
        return enabled()


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


def _perception_batch_text(texts) -> str:
    return "\n".join(sorted(t for t in texts if t))


def _resolve_perception_batch_sync(conn, *, user_id: int, character_id: int,
                                   message_id: int, memory_content: str):
    """感知批次解析：批次 id＝该轮用户消息 id；事件幂等复用（generation 恒 1）。"""
    from datetime import timedelta

    from app.memory.write import (SNAPSHOT_MAX_ROWS, SNAPSHOT_WINDOW_MINUTES,
                                  snapshot_overlap)
    from app.utils.timeutil import now_naive_utc

    msg = conn.execute(text(
        "SELECT id,session_id,created_at FROM chat_messages WHERE id=:m"),
        {"m": int(message_id)}).mappings().first()
    if msg is None:
        return None
    sess = conn.execute(text(
        "SELECT user_id,character_id FROM chat_sessions WHERE id=:s"),
        {"s": msg["session_id"]}).mappings().first()
    if sess is None or int(sess["user_id"]) != int(user_id) \
            or int(sess["character_id"]) != int(character_id):
        return None
    cutoff = now_naive_utc() - timedelta(minutes=SNAPSHOT_WINDOW_MINUTES)
    snaps = conn.execute(text(
        "SELECT content, image_desc FROM phone_snapshots "
        "WHERE user_id=:u AND created_at>=:c ORDER BY created_at DESC, id DESC LIMIT :n"),
        {"u": int(user_id), "c": cutoff, "n": int(SNAPSHOT_MAX_ROWS)}).mappings().all()
    texts = [t for t in ((row["content"] or row["image_desc"] or "") for row in snaps) if t]
    # 绑定口径比打标口径宽松：只要与本轮语料有 >=1 个长词交集即可（打标要 >=3，用于"是否算感知来源"；
    # 绑定只需证明"这条记忆确实来自本轮感知"，批次本身已由消息 id + 归属复核锁定）。
    if not texts or not snapshot_overlap(memory_content, texts, min_overlap=1):
        return None
    batch = _perception_batch_text(texts)
    existing = conn.execute(text(
        "SELECT event_uid FROM memory_source_events "
        "WHERE kind='perception_batch' AND source_row_id=:m AND owner_user_id=:u "
        "AND character_id=:c AND deleted=0 ORDER BY seq DESC LIMIT 1"),
        {"m": int(message_id), "u": int(user_id), "c": int(character_id)}).mappings().first()
    if existing is not None and se.visible(conn, existing["event_uid"]):
        uid = existing["event_uid"]
    else:
        at = msg["created_at"]
        if isinstance(at, str):  # 原生 SQL 在 sqlite 下时间列是字符串
            at = datetime.fromisoformat(at.replace(" ", "T"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        uid = se.issue_source_event(
            conn, kind="perception_batch", source_row_id=int(message_id),
            owner_user_id=int(user_id), character_id=int(character_id),
            event_at=at, content=batch, session_id=int(msg["session_id"]))
    return Binding(event_uid=uid, owner_user_id=int(user_id),
                   character_id=int(character_id), session_id=int(msg["session_id"]),
                   content_sha256=_sha(batch), event_at=msg["created_at"])


async def resolve_perception_batch_binding(db, *, memory):
    """把「本轮用户消息」当感知批次锚点（见 _resolve_perception_batch_sync）。"""
    if not enabled() or memory.source != "perception" or memory.source_id is None:
        return None
    return await db.run_sync(lambda sync_db: _resolve_perception_batch_sync(
        sync_db.connection(), user_id=int(memory.user_id),
        character_id=int(memory.character_id), message_id=int(memory.source_id),
        memory_content=memory.content or ""))


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

