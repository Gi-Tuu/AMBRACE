"""Evidence-scoped write replay keys; similarity never grants mutation rights."""
import hashlib
import json

from sqlalchemy import select
from app.models.memory import MemorySourceEvent, MemoryWriteReceipt, MemoryTombstone
from sqlalchemy.exc import IntegrityError

from app.models.memory import Memory


def replay_key(memory) -> str | None:
    # A text match without a source event is not proof of the same episode.
    if memory.source_id is None or not memory.source:
        return None
    fields = ("user_id", "character_id", "source", "source_id", "group_id", "scope",
              "speaker_type", "speaker_id", "epistemic_status", "memory_type", "sub_type",
              "content", "node_type", "parent_id", "chain_id", "related_memory_id")
    identity = {name: getattr(memory, name, None) for name in fields}
    identity["derived_from_ids"] = sorted(set(json.loads(memory.derived_from_ids or "[]")))
    if getattr(memory, "source_event_uid", None):
        identity["source_event_uid"] = memory.source_event_uid
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(("memory-evidence-v1:" + encoded).encode()).hexdigest()




async def _ensure_replay_authority(db, memory):
    """重放/唯一冲突回查前复核当前来源、墓碑与权威 receipt；撤销后不得成功重放。"""
    from app.memory import source_binding
    await source_binding.guard_extraction_write(db)
    if not source_binding.enabled() or memory.source != "chat" or memory.source_id is None:
        return
    binding = await source_binding.resolve_chat_binding(
        db, user_id=int(memory.user_id), character_id=int(memory.character_id),
        source=memory.source, source_id=int(memory.source_id))
    if binding is None:
        raise RuntimeError("source_binding_missing")
    tomb = (await db.execute(select(MemoryTombstone).where(
        MemoryTombstone.memory_id == memory.id))).scalar_one_or_none()
    if tomb is not None:
        # 重放是读取既有权威结果，不是再次写入：任何墓碑状态都必须保守拒绝；
        # 先核归属，未知 state 也拒绝，避免异常/损坏数据授权旧结果。
        if int(tomb.owner_user_id) != int(memory.user_id) \
                or int(tomb.character_id) != int(memory.character_id):
            raise RuntimeError("source_memory_tombstone_scope")
        if tomb.state not in ("closed", "reallowed", "reallowed_active"):
            raise RuntimeError("source_memory_tombstone_state")
        src = (await db.execute(select(MemorySourceEvent).where(
            MemorySourceEvent.event_uid == binding.event_uid))).scalar_one_or_none()
        if src is None or int(src.seq) <= int(tomb.revoked_seq) \
                or int(src.seq) <= int(tomb.confirm_seq):
            raise RuntimeError("source_memory_tombstoned")
        raise RuntimeError("source_memory_tombstoned")
    receipt = (await db.execute(select(MemoryWriteReceipt).where(
        MemoryWriteReceipt.authoritative == True,
        MemoryWriteReceipt.memory_id == memory.id,
        MemoryWriteReceipt.source_event_uid == binding.event_uid))).scalar_one_or_none()
    if receipt is None:
        raise RuntimeError("source_binding_legacy_replay")


async def persist_candidate(db, memory, *, skip_dedup=False, commit=True):
    """Return (row, created). A replay has no reinforcement or promotion effects.

    The unique key also serializes concurrent retries. Legacy rows keep NULL keys;
    no source history is invented by backfilling text matches.
    """
    from app.memory import source_binding
    await source_binding.guard_extraction_write(db)
    binding = None
    if source_binding.enabled() and memory.source == "chat":
        if memory.source_id is None:
            raise RuntimeError("source_binding_missing")
        binding = await source_binding.resolve_chat_binding(
            db, user_id=int(memory.user_id), character_id=int(memory.character_id),
            source=memory.source, source_id=int(memory.source_id))
        if binding is None:
            raise RuntimeError("source_binding_missing")
    if binding is not None:
        memory.source_event_uid = binding.event_uid
    memory.write_key = None if skip_dedup else replay_key(memory)
    if memory.write_key:
        old = (await db.execute(select(Memory).where(Memory.write_key == memory.write_key))).scalar_one_or_none()
        if old is not None:
            if binding is not None:
                from app.models.memory import MemoryWriteReceipt
                receipt = (await db.execute(select(MemoryWriteReceipt).where(
                    MemoryWriteReceipt.authoritative == True,
                    MemoryWriteReceipt.memory_id == old.id,
                    MemoryWriteReceipt.source_event_uid == binding.event_uid))).scalar_one_or_none()
                if receipt is None:
                    raise RuntimeError("source_binding_legacy_replay")
                await _ensure_replay_authority(db, old)
            return old, False
    db.add(memory)
    try:
        await db.flush()
        if binding is not None:
            memory.source_event_uid = binding.event_uid
            bound = await source_binding.bind_memory(db, memory, binding, action="create")
            if bound not in ("accepted", "duplicate"):
                await db.rollback()
                raise RuntimeError("source_binding_refused:" + bound)
            await db.commit()
        elif commit:
            await db.commit()
    except IntegrityError:
        key = memory.write_key
        await db.rollback()
        if key:
            old = (await db.execute(select(Memory).where(Memory.write_key == key))).scalar_one_or_none()
            if old is not None:
                if binding is not None:
                    await _ensure_replay_authority(db, old)
                return old, False
        raise
    await db.refresh(memory)
    return memory, True
