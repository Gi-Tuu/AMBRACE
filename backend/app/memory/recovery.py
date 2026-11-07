# -*- coding: utf-8 -*-
"""M2 2b：墓碑记忆的在线「一段式原子重新授权」（2026-11-04 用户拍板 A）。

语义（与内核约束一致）：
- 只对**已被 forget（墓碑）** 的记忆有效；旧证据不得复活——内核要求写入来源的 seq
  必须**晚于**墓碑的 confirm_seq，所以恢复必须锚定一次新的「用户确认」来源事件；
- 同一事务内完成三步：reallow（按当前可信边界 confirm_seq 发一次性 grant）→ 签发
  `user_reauthorization` 来源事件（seq 落在确认边界之后）→ 权威写回（消费 grant、
  墓碑转 reallowed_active、写权威 receipt）；
- grant token 不出本模块、不可重放（CAS 消费）；flag `memory_source_proof_v1` 关时拒绝，
  现网零行为变化；
- 重复调用（已 reallowed_active）按幂等成功返回，不重复写。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from app.flags.agent_flags import AGENT_FLAGS
from app.utils.timeutil import now_naive_utc
from app.memory import source_events as se

KIND = "user_reauthorization"
# 写回时只取这些既有业务字段（ALLOWED_MEMORY_FIELDS 子集）；内容不变，仅刷新来源锚点
_VALUE_FIELDS = ("content", "memory_type", "sub_type", "importance", "epistemic_status")


class RecoveryError(RuntimeError):
    """恢复被拒；code 与内核 refusal 字符串同源，便于审计。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _load_memory(conn, memory_id: int):
    return conn.execute(text(
        "SELECT id,user_id,character_id,content,memory_type,sub_type,importance,epistemic_status "
        "FROM memories WHERE id=:m"), {"m": int(memory_id)}).mappings().first()


def _load_tombstone(conn, memory_id: int):
    return conn.execute(text(
        "SELECT * FROM memory_tombstones WHERE memory_id=:m"),
        {"m": int(memory_id)}).mappings().first()


def _reauthorize_sync(conn, *, memory_id: int, owner_user_id: int,
                      character_id: int, event_at: datetime) -> dict:
    mem = _load_memory(conn, memory_id)
    if mem is None:
        raise RecoveryError("memory_not_found")
    if int(mem["user_id"]) != int(owner_user_id) \
            or int(mem["character_id"]) != int(character_id):
        raise RecoveryError("memory_scope_mismatch")
    tomb = _load_tombstone(conn, memory_id)
    if tomb is None:
        raise RecoveryError("not_tombstoned")
    if int(tomb["owner_user_id"]) != int(owner_user_id) \
            or int(tomb["character_id"]) != int(character_id):
        raise RecoveryError("memory_scope_mismatch")
    if tomb["state"] == "reallowed_active":
        # 幂等：已经恢复过，不重复写、不新发 grant
        return {"memory_id": int(memory_id), "state": "reallowed_active",
                "idempotent": True}
    seq = conn.execute(text("SELECT seq FROM source_seq WHERE id=1")).scalar_one()
    token = se.reallow_memory(
        conn, memory_id=int(memory_id), owner_user_id=int(owner_user_id),
        character_id=int(character_id), confirm_seq=int(seq))
    epoch = int(tomb["epoch"]) + 1
    uid = se.issue_source_event(
        conn, kind=KIND, source_row_id=int(memory_id),
        owner_user_id=int(owner_user_id), character_id=int(character_id),
        event_at=event_at, content=f"reauthorize:{int(memory_id)}:{epoch}")
    values = {k: mem[k] for k in _VALUE_FIELDS if mem[k] is not None}
    result = se.write_authoritative_memory(
        conn, event_uid=uid, memory_id=int(memory_id),
        owner_user_id=int(owner_user_id), character_id=int(character_id),
        actor_kind=se.SUPPORTED_ACTOR, actor_user_id=int(owner_user_id),
        action="update", idempotency_key=f"reauthorize:{int(memory_id)}:{epoch}",
        memory_values=values, grant_token=token)
    if result != "accepted":
        raise RecoveryError(result)
    return {"memory_id": int(memory_id), "source_event_uid": uid,
            "state": "reallowed_active", "epoch": epoch}


async def reauthorize_memory(db, *, memory_id: int, owner_user_id: int) -> dict:
    """在线一段式原子恢复；db 为 AsyncSession，成功后由调用方 commit（失败须 rollback）。"""
    if not AGENT_FLAGS.get("memory_source_proof_v1", False):
        raise RecoveryError("source_proof_disabled")
    mem = (await db.execute(text(
        "SELECT user_id,character_id FROM memories WHERE id=:m"),
        {"m": int(memory_id)})).mappings().first()
    if mem is None or int(mem["user_id"]) != int(owner_user_id):
        raise RecoveryError("memory_not_found")
    # 时间纪律棘轮（test_time_discipline）：不写裸 aware；se._utc 接受带偏移的 ISO 串
    event_at = now_naive_utc().isoformat() + "+00:00"
    try:
        return await db.run_sync(lambda sync_db: _reauthorize_sync(
            sync_db.connection(), memory_id=int(memory_id),
            owner_user_id=int(owner_user_id),
            character_id=int(mem["character_id"]), event_at=event_at))
    except se.KernelError as exc:
        raise RecoveryError(str(exc) or "recovery_refused") from exc
