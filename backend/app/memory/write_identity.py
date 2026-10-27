"""Evidence-scoped write replay keys; similarity never grants mutation rights."""
import hashlib
import json

from sqlalchemy import select
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
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(("memory-evidence-v1:" + encoded).encode()).hexdigest()


async def persist_candidate(db, memory, *, skip_dedup=False):
    """Return (row, created). A replay has no reinforcement or promotion effects.

    The unique key also serializes concurrent retries. Legacy rows keep NULL keys;
    no source history is invented by backfilling text matches.
    """
    memory.write_key = None if skip_dedup else replay_key(memory)
    if memory.write_key:
        old = (await db.execute(select(Memory).where(Memory.write_key == memory.write_key))).scalar_one_or_none()
        if old is not None:
            return old, False
    db.add(memory)
    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        key = memory.write_key
        await db.rollback()
        if key:
            old = (await db.execute(select(Memory).where(Memory.write_key == key))).scalar_one_or_none()
            if old is not None:
                return old, False
        raise
    await db.refresh(memory)
    return memory, True
