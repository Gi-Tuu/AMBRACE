# -*- coding: utf-8 -*-
"""M2 2a 权威写内核（未接聊天/旧 save_memory）。

只提供独立事务 API；调用方必须显式传入服务端身份/来源，wire 不可自证。
"""
from __future__ import annotations

import hashlib
from functools import wraps
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.models.memory import (
    MemoryReallowGrant,
    MemoryRestoreQuarantine,
    MemorySourceEvent,
    MemoryWriteReceipt,
)

# 2026-11-04（2b 切片 1）：kind 从单值扩为集合。新增 `user_reauthorization`＝用户对墓碑记忆的
# 显式重新授权确认事件（在线一段式恢复的锚点；旧证据仍不得复活，见 _authorize_tombstone 的
# 「src.seq 必须晚于 confirm_seq」约束）。`SUPPORTED_KIND` 保留给既有调用/测试引用。
SUPPORTED_KIND = "chat_message"
# 2026-11-04（2b 切片 2 / a2）：感知来源不做逐快照事件（快照行无角色归属；事件代次按
# (kind,row) 记，多角色会互相顶掉），改为锚定「本轮注入批次」：批次 id＝该轮用户消息 id。
SUPPORTED_KINDS = frozenset({"chat_message", "user_reauthorization", "perception_batch"})
SUPPORTED_ACTOR = "authenticated_user"
SUPPORTED_ACTIONS = frozenset({"create", "update", "merge", "supersede", "stale"})
RECOVERY_LOCK_UID = "__recovery_lock__"
# 业务字段白名单：身份/归属/主键/来源证明/写键/时间等一律由服务端控制，不从调用方覆盖
ALLOWED_MEMORY_FIELDS = frozenset({
    "memory_type", "sub_type", "title", "content", "importance", "scope",
    "epistemic_status", "speaker_type", "speaker_id", "related_memory_id",
    "group_id", "is_archived", "is_pinned", "is_locked", "strength_days",
    "last_reinforce_at", "review_count", "next_review_at", "why_it_matters",
    "is_core", "core_category", "confirmation_count", "departed_names",
})


class KernelError(ValueError):
    pass


def _sha256(text_value: str) -> str:
    return hashlib.sha256((text_value or "").encode("utf-8")).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        dt = value
    if dt.tzinfo is None:
        raise KernelError("event_at_requires_tz")
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _begin(conn):
    conn.exec_driver_sql("BEGIN IMMEDIATE")


@contextmanager
def _maybe_conn(target):
    """engine=新开连接并 BEGIN IMMEDIATE；Connection=加入调用方事务（不 commit/rollback）。"""
    if isinstance(target, Connection):
        yield target
    else:
        with target.connect() as conn:
            _begin(conn)
            yield conn


def _local_atomic_operation(fn):
    """借用连接时只回滚本次操作，调用方可继续提交其它业务。"""
    @wraps(fn)
    def wrapped(target, *args, **kwargs):
        if not isinstance(target, Connection):
            return fn(target, *args, **kwargs)
        # sqlite legacy transaction mode可能尚未真正BEGIN；不能让RELEASE提交外层。
        if target.dialect.name == "sqlite":
            driver = target.connection.driver_connection
            if not driver.in_transaction:
                target.exec_driver_sql("BEGIN IMMEDIATE")
        with target.begin_nested() as operation:
            result = fn(target, *args, **kwargs)
            if isinstance(result, str) and result.startswith("refused"):
                operation.rollback()
            return result
    return wrapped


@_local_atomic_operation
def issue_source_event(engine, *, kind: str, source_row_id: int, owner_user_id: int,
                       character_id: int, event_at: datetime | str, content: str,
                       session_id: int | None = None,
                       generation: int | None = None) -> str:
    """在来源事务内签发 event_uid/seq；只存 content hash，不存正文。"""
    if kind not in SUPPORTED_KINDS:
        raise KernelError("unsupported_source_kind")
    if owner_user_id <= 0 or character_id <= 0 or source_row_id <= 0:
        raise KernelError("bad_owner_or_character")
    external = isinstance(engine, Connection)
    with _maybe_conn(engine) as conn:
        try:
            if recovery_locked(conn):
                raise KernelError("recovery_locked")
            conn.execute(text("INSERT OR IGNORE INTO source_seq(id,seq) VALUES (1,0)"))
            seq = conn.execute(text(
                "UPDATE source_seq SET seq=seq+1 WHERE id=1 RETURNING seq")).scalar_one()
            if generation is None:
                generation = (conn.execute(text(
                    "SELECT COALESCE(MAX(generation),0)+1 FROM memory_source_events "
                    "WHERE kind=:k AND source_row_id=:r"), {"k": kind, "r": source_row_id})).scalar_one()
            uid = "mse-" + str(getattr(uuid, "uuid7", uuid.uuid4)())
            conn.execute(MemorySourceEvent.__table__.insert().values(
                event_uid=uid, kind=kind, source_row_id=source_row_id,
                generation=int(generation), seq=int(seq), owner_user_id=owner_user_id,
                character_id=character_id, session_id=session_id,
                event_at=_utc(event_at), content_sha256=_sha256(content),
                deleted=False, created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ))
            if not external: conn.commit()
            return uid
        except Exception:
            if not external: conn.rollback()
            raise




def issue_source_event_tx(conn, **kwargs):
    """加入调用方事务；保存点保护失败操作，不接管外层提交。"""
    return issue_source_event(conn, **kwargs)



def write_authoritative_memory_tx(conn, **kwargs):
    """加入调用方事务；外层 commit/rollback 决定原子性。"""
    return write_authoritative_memory(conn, **kwargs)


@_local_atomic_operation
def revoke_source_event(engine, *, event_uid: str, owner_user_id: int,
                        character_id: int) -> bool:
    """可信来源事务撤销来源事件；不物理删历史，只标记不可再作新证据。"""
    external = isinstance(engine, Connection)
    with _maybe_conn(engine) as conn:
        try:
            cur = conn.execute(text(
                "UPDATE memory_source_events SET deleted=1 "
                "WHERE event_uid=:u AND owner_user_id=:o AND character_id=:c"),
                {"u": event_uid, "o": owner_user_id, "c": character_id})
            if not external: conn.commit()
            return cur.rowcount == 1
        except Exception:
            if not external:
                conn.rollback()
            raise


def current_seq(engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT seq FROM source_seq WHERE id=1")).scalar_one())


def recovery_locked(conn) -> bool:
    return conn.execute(text(
        "SELECT 1 FROM memory_restore_quarantine WHERE event_uid=:u"),
        {"u": RECOVERY_LOCK_UID}).first() is not None


def lock_recovery(engine, *, reason: str = "restore_without_external_revocation") -> None:
    """恢复环境全局锁定：缺外部撤销资料时禁止权威写；无隐式解锁入口。"""
    with engine.connect() as conn:
        _begin(conn)
        try:
            conn.execute(text(
                "INSERT OR REPLACE INTO memory_restore_quarantine(event_uid,reason,restored_at) "
                "VALUES (:u,:r,:t)"),
                {"u": RECOVERY_LOCK_UID, "r": reason[:255],
                 "t": datetime.now(timezone.utc).replace(tzinfo=None)})
            conn.commit()
        except Exception:
            conn.rollback(); raise


def visible(conn, event_uid: str) -> bool:
    if recovery_locked(conn):
        return False
    row = conn.execute(text(
        "SELECT e.deleted, e.kind, e.source_row_id, e.generation, "
        "(SELECT MAX(x.generation) FROM memory_source_events x "
        " WHERE x.kind=e.kind AND x.source_row_id=e.source_row_id) AS cur, "
        "(SELECT 1 FROM memory_restore_quarantine q WHERE q.event_uid=e.event_uid) AS q "
        "FROM memory_source_events e WHERE e.event_uid=:u"), {"u": event_uid}).mappings().first()
    if row is None or row["deleted"] or int(row["generation"]) != int(row["cur"] or 0):
        return False
    return row["q"] is None


def _tombstone(conn, memory_id: int):
    return conn.execute(text(
        "SELECT * FROM memory_tombstones WHERE memory_id=:m"), {"m": memory_id}).mappings().first()


def _write_source(conn, *, event_uid, owner_user_id, character_id, actor_user_id):
    if recovery_locked(conn):
        return None, "refused_recovery_locked"
    src = conn.execute(text(
        "SELECT * FROM memory_source_events WHERE event_uid=:u"),
        {"u": event_uid}).mappings().first()
    if src is None or src["deleted"]:
        return None, "refused_missing_source"
    if not visible(conn, event_uid):
        q = conn.execute(text(
            "SELECT 1 FROM memory_restore_quarantine WHERE event_uid=:u"),
            {"u": event_uid}).first()
        return None, "refused_quarantined" if q else "refused_stale_generation"
    if int(src["owner_user_id"]) != int(owner_user_id) \
            or int(src["character_id"]) != int(character_id):
        return None, "refused_owner_scope"
    if int(actor_user_id) != int(owner_user_id):
        return None, "refused_owner_actor"
    return src, None


def _write_values(conn, *, memory_id, owner_user_id, character_id, memory_values):
    values = {k: v for k, v in dict(memory_values).items()
              if k in ALLOWED_MEMORY_FIELDS}
    if not values:
        return None, None, "refused_no_business_fields"
    mem = conn.execute(text(
        "SELECT user_id,character_id FROM memories WHERE id=:m"),
        {"m": memory_id}).mappings().first()
    if mem is not None and (int(mem["user_id"]) != int(owner_user_id)
                            or int(mem["character_id"]) != int(character_id)):
        return None, None, "refused_memory_scope"
    return values, mem, None


def _write_duplicate(conn, *, idempotency_key, action, owner_user_id, memory_id, event_uid):
    if not idempotency_key or not isinstance(idempotency_key, str):
        return None, "refused_empty_idempotency_key"
    if action not in SUPPORTED_ACTIONS:
        return None, "refused_bad_action"
    old = conn.execute(text(
        "SELECT * FROM memory_write_receipts WHERE authoritative=1 AND idempotency_key=:k"),
        {"k": idempotency_key}).mappings().first()
    if old is None:
        return None, None
    if (int(old["owner_user_id"] or 0) == int(owner_user_id)
            and int(old["memory_id"] or 0) == int(memory_id)
            and old["source_event_uid"] == event_uid
            and old["action"] == action):
        return "duplicate", None
    return None, "refused_idempotency_conflict"


def _consume_grant(conn, *, memory_id, owner_user_id, character_id, grant_token,
                   tomb, allow_missing):
    if not grant_token:
        return "refused_no_grant" if not allow_missing else None
    row = conn.execute(text(
        "SELECT * FROM memory_reallow_grants WHERE token_hash=:h"),
        {"h": _token_hash(grant_token)}).mappings().first()
    if row is None or row["used"] or row["invalidated"] \
            or int(row["memory_id"]) != int(memory_id) \
            or int(row["owner_user_id"]) != int(owner_user_id) \
            or int(row["character_id"]) != int(character_id) \
            or int(row["epoch"]) != int(tomb["epoch"]) \
            or int(row["confirm_seq"]) != int(tomb["confirm_seq"]):
        return "refused_bad_grant"
    cur = conn.execute(text(
        "UPDATE memory_reallow_grants SET used=1 "
        "WHERE token_hash=:h AND used=0 AND invalidated=0"),
        {"h": _token_hash(grant_token)})
    if cur.rowcount != 1:
        return "refused_grant_cas"
    conn.execute(text(
        "UPDATE memory_tombstones SET state='reallowed_active' WHERE memory_id=:m"),
        {"m": memory_id})
    return None


def _authorize_tombstone(conn, *, memory_id, owner_user_id, character_id, src,
                         grant_token, fail_before_commit):
    tomb = _tombstone(conn, memory_id)
    if tomb is None:
        return "refused_unexpected_grant" if grant_token else None
    if int(tomb["owner_user_id"]) != int(owner_user_id) \
            or int(tomb["character_id"]) != int(character_id):
        return "refused_tombstone_scope"
    if tomb["state"] == "closed":
        return "refused_tombstoned"
    if int(src["seq"]) <= int(tomb["revoked_seq"]):
        return "refused_stale_source"
    if int(src["seq"]) <= int(tomb["confirm_seq"]):
        return "refused_before_confirm"
    if tomb["state"] == "reallowed":
        refusal = _consume_grant(conn, memory_id=memory_id,
                                 owner_user_id=owner_user_id,
                                 character_id=character_id,
                                 grant_token=grant_token, tomb=tomb,
                                 allow_missing=False)
        if refusal:
            return refusal
        if fail_before_commit == "grant":
            raise KernelError("inject_after_grant")
    elif tomb["state"] == "reallowed_active":
        if grant_token:
            return "refused_bad_grant"
    else:
        return "refused_tombstoned"
    return None


def _persist_memory(conn, *, memory_id, owner_user_id, character_id, event_uid,
                    values, mem, fail_before_commit):
    values = dict(values)
    values["source_event_uid"] = event_uid
    from app.models.memory import Memory as MemoryModel
    if mem is None:
        values.update(id=memory_id, user_id=owner_user_id,
                      character_id=character_id)
        conn.execute(MemoryModel.__table__.insert().values(**values))
    else:
        sets = ", ".join(f"{k}=:{k}" for k in values if k in MemoryModel.__table__.c)
        conn.execute(text(f"UPDATE memories SET {sets} WHERE id=:_id"),
                     {**values, "_id": memory_id})
    check = conn.execute(text(
        "SELECT user_id,character_id FROM memories WHERE id=:m"),
        {"m": memory_id}).mappings().first()
    if check is None or int(check["user_id"]) != int(owner_user_id) \
            or int(check["character_id"]) != int(character_id):
        return "refused_memory_scope"
    if fail_before_commit == "memory":
        raise KernelError("inject_after_memory")
    return None


def _persist_receipt(conn, *, event_uid, memory_id, owner_user_id, character_id,
                     actor_kind, actor_user_id, action, idempotency_key,
                     fail_before_commit):
    conn.execute(MemoryWriteReceipt.__table__.insert().values(
        character_id=character_id, memory_id=memory_id, action=action,
        reason=None, detail_json=None, source_event_uid=event_uid,
        owner_user_id=owner_user_id, actor_kind=actor_kind,
        actor_user_id=actor_user_id, envelope_version=1, authoritative=True,
        idempotency_key=idempotency_key,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    if fail_before_commit == "receipt":
        raise KernelError("inject_after_receipt")
    if fail_before_commit == "commit":
        raise KernelError("inject_before_commit")


def _prepare_write(conn, *, event_uid, memory_id, owner_user_id, character_id,
                   actor_user_id, action, idempotency_key, memory_values,
                   grant_token, fail_before_commit):
    src, refusal = _write_source(conn, event_uid=event_uid,
                                 owner_user_id=owner_user_id,
                                 character_id=character_id,
                                 actor_user_id=actor_user_id)
    if refusal:
        return None, None, None, refusal
    values, mem, refusal = _write_values(
        conn, memory_id=memory_id, owner_user_id=owner_user_id,
        character_id=character_id, memory_values=memory_values)
    if refusal:
        return None, None, None, refusal
    duplicate, refusal = _write_duplicate(
        conn, idempotency_key=idempotency_key, action=action,
        owner_user_id=owner_user_id, memory_id=memory_id, event_uid=event_uid)
    if refusal or duplicate:
        return None, None, None, refusal or duplicate
    refusal = _authorize_tombstone(
        conn, memory_id=memory_id, owner_user_id=owner_user_id,
        character_id=character_id, src=src, grant_token=grant_token,
        fail_before_commit=fail_before_commit)
    if refusal:
        return None, None, None, refusal
    return src, values, mem, None


def _execute_write(conn, *, event_uid, memory_id, owner_user_id, character_id,
                   actor_kind, actor_user_id, action, idempotency_key,
                   values, mem, fail_before_commit):
    refusal = _persist_memory(
        conn, memory_id=memory_id, owner_user_id=owner_user_id,
        character_id=character_id, event_uid=event_uid, values=values,
        mem=mem, fail_before_commit=fail_before_commit)
    if refusal:
        return refusal
    _persist_receipt(
        conn, event_uid=event_uid, memory_id=memory_id,
        owner_user_id=owner_user_id, character_id=character_id,
        actor_kind=actor_kind, actor_user_id=actor_user_id, action=action,
        idempotency_key=idempotency_key, fail_before_commit=fail_before_commit)
    return None


@_local_atomic_operation
def write_authoritative_memory(engine, *, event_uid: str, memory_id: int,
                               owner_user_id: int, character_id: int,
                               actor_kind: str, actor_user_id: int, action: str,
                               idempotency_key: str, memory_values: dict,
                               grant_token: str | None = None,
                               fail_before_commit: str | None = None) -> str:
    """权威 Memory+receipt+grant+tombstone 同一事务；2a 不接线上路径。"""
    if actor_kind != SUPPORTED_ACTOR:
        return "refused_unsupported_actor"
    external = isinstance(engine, Connection)
    with _maybe_conn(engine) as conn:
        _, values, mem, refusal = _prepare_write(
            conn, event_uid=event_uid, memory_id=memory_id,
            owner_user_id=owner_user_id, character_id=character_id,
            actor_user_id=actor_user_id, action=action,
            idempotency_key=idempotency_key, memory_values=memory_values,
            grant_token=grant_token, fail_before_commit=fail_before_commit)
        if refusal:
            return refusal
        refusal = _execute_write(
            conn, event_uid=event_uid, memory_id=memory_id,
            owner_user_id=owner_user_id, character_id=character_id,
            actor_kind=actor_kind, actor_user_id=actor_user_id, action=action,
            idempotency_key=idempotency_key, values=values, mem=mem,
            fail_before_commit=fail_before_commit)
        if refusal:
            return refusal
        if not external:
            conn.commit()
        return "accepted"




@_local_atomic_operation
def bind_existing_memory(engine, *, event_uid: str, memory_id: int,
                         owner_user_id: int, character_id: int, action: str,
                         idempotency_key: str) -> str:
    """为已存在的 Memory 写权威 receipt；只允许 create/update 等业务动作。"""
    external = isinstance(engine, Connection)
    with _maybe_conn(engine) as conn:
        src, refusal = _write_source(conn, event_uid=event_uid,
                                     owner_user_id=owner_user_id,
                                     character_id=character_id,
                                     actor_user_id=owner_user_id)
        if refusal:
            return refusal
        mem = conn.execute(text(
            "SELECT user_id,character_id FROM memories WHERE id=:m"),
            {"m": memory_id}).mappings().first()
        if mem is None or int(mem["user_id"]) != int(owner_user_id) \
                or int(mem["character_id"]) != int(character_id):
            return "refused_memory_scope"
        duplicate, refusal = _write_duplicate(
            conn, idempotency_key=idempotency_key, action=action,
            owner_user_id=owner_user_id, memory_id=memory_id, event_uid=event_uid)
        if refusal or duplicate:
            return refusal or duplicate
        refusal = _authorize_tombstone(
            conn, memory_id=memory_id, owner_user_id=owner_user_id,
            character_id=character_id, src=src, grant_token=None,
            fail_before_commit=None)
        if refusal:
            return refusal
        _persist_receipt(
            conn, event_uid=event_uid, memory_id=memory_id,
            owner_user_id=owner_user_id, character_id=character_id,
            actor_kind="authenticated_user", actor_user_id=owner_user_id,
            action=action, idempotency_key=idempotency_key,
            fail_before_commit=None)
        if not external:
            conn.commit()
        return "accepted"


@_local_atomic_operation
def forget_memory(engine, *, memory_id: int, owner_user_id: int, character_id: int) -> None:
    external = isinstance(engine, Connection)
    with _maybe_conn(engine) as conn:
        try:
            mem = conn.execute(text("SELECT * FROM memories WHERE id=:m"),
                               {"m": memory_id}).mappings().first()
            if mem is None or int(mem["user_id"]) != owner_user_id \
                    or int(mem["character_id"]) != character_id:
                raise KernelError("memory_scope_mismatch")
            seq = conn.execute(text("SELECT seq FROM source_seq WHERE id=1")).scalar_one()
            conn.execute(text(
                "INSERT INTO memory_tombstones(memory_id,owner_user_id,character_id,epoch,"
                "revoked_seq,confirm_seq,state,created_at) VALUES "
                "(:m,:o,:c,1,:s,0,'closed',:t) "
                "ON CONFLICT(memory_id) DO UPDATE SET epoch=epoch+1,"
                "revoked_seq=excluded.revoked_seq,confirm_seq=0,state='closed'"),
                {"m": memory_id, "o": owner_user_id, "c": character_id, "s": int(seq),
                 "t": datetime.now(timezone.utc).replace(tzinfo=None)})
            conn.execute(text(
                "UPDATE memory_reallow_grants SET invalidated=1 "
                "WHERE memory_id=:m AND used=0"), {"m": memory_id})
            if not external: conn.commit()
        except Exception:
            if not external:
                conn.rollback()
            raise


@_local_atomic_operation
def reallow_memory(engine, *, memory_id: int, owner_user_id: int, character_id: int,
                   confirm_seq: int) -> str:
    external = isinstance(engine, Connection)
    with _maybe_conn(engine) as conn:
        try:
            tomb = conn.execute(text(
                "SELECT * FROM memory_tombstones WHERE memory_id=:m"),
                {"m": memory_id}).mappings().first()
            if tomb is None or int(tomb["owner_user_id"]) != owner_user_id \
                    or int(tomb["character_id"]) != character_id:
                raise KernelError("owner_or_scope_mismatch")
            cur = conn.execute(text("SELECT seq FROM source_seq WHERE id=1")).scalar_one()
            if int(confirm_seq) != int(cur):
                raise KernelError("confirm_seq_not_current_trusted_boundary")
            epoch = int(tomb["epoch"]) + 1
            conn.execute(text(
                "UPDATE memory_reallow_grants SET invalidated=1 "
                "WHERE memory_id=:m AND used=0"), {"m": memory_id})
            token = "grant-" + secrets.token_urlsafe(32)
            conn.execute(MemoryReallowGrant.__table__.insert().values(
                token_hash=_token_hash(token), memory_id=memory_id,
                owner_user_id=owner_user_id, character_id=character_id, epoch=epoch,
                confirm_seq=int(confirm_seq), used=False, invalidated=False,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            conn.execute(text(
                "UPDATE memory_tombstones SET epoch=:e, confirm_seq=:s, state='reallowed' "
                "WHERE memory_id=:m"), {"e": epoch, "s": int(confirm_seq), "m": memory_id})
            if not external: conn.commit()
            return token
        except Exception:
            if not external:
                conn.rollback()
            raise


@_local_atomic_operation
def quarantine_restore(engine, *, event_uid: str, reason: str) -> None:
    external = isinstance(engine, Connection)
    with _maybe_conn(engine) as conn:
        try:
            src = conn.execute(text(
                "SELECT owner_user_id, character_id FROM memory_source_events WHERE event_uid=:u"),
                {"u": event_uid}).mappings().first()
            conn.execute(MemoryRestoreQuarantine.__table__.insert().values(
                event_uid=event_uid,
                owner_user_id=(src["owner_user_id"] if src else None),
                character_id=(src["character_id"] if src else None),
                reason=reason[:255],
                restored_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            if not external: conn.commit()
        except Exception:
            if not external:
                conn.rollback()
            raise
