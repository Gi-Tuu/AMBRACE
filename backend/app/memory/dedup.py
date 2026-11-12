"""Bounded, read-only duplicate suggestions. Similarity cannot delete evidence."""
import asyncio
from difflib import SequenceMatcher
import time

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.memory.constants import VECTOR_DEDUP_THRESHOLD, DEDUP_MIN_INTERVAL
from app.utils.logger import get_logger

_logger = get_logger("memory.dedup")
_dedup_last_run: dict[int, float] = {}
_dedup_locks: dict[int, asyncio.Lock] = {}


async def _schedule_dedup(character_id: int):
    now = time.time()
    if now - _dedup_last_run.get(character_id, 0) < DEDUP_MIN_INTERVAL:
        return
    lock = _dedup_locks.setdefault(character_id, asyncio.Lock())
    if lock.locked():
        return
    _dedup_last_run[character_id] = now
    async with lock:
        await deduplicate_memories(character_id)


async def duplicate_candidates(character_id: int, threshold=VECTOR_DEDUP_THRESHOLD) -> list[dict]:
    """Review suggestions over at most 200 current rows and 1000 comparisons.

    These are text candidates, not semantic equivalence decisions. Events, subjects
    and time can differ even at similarity=1; caller must never auto-delete them.
    """
    async with async_session_factory() as db:
        rows = (await db.execute(select(Memory).where(
            Memory.character_id == character_id, Memory.is_archived == False,  # noqa: E712
            Memory.status == "active", Memory.memory_type != "working_state",
        ).order_by(Memory.id.desc()).limit(200))).scalars().all()
    candidates, comparisons = [], 0
    for i, left in enumerate(rows):
        for right in rows[i + 1:]:
            identity = ("user_id", "speaker_type", "speaker_id", "group_id", "scope", "memory_type", "sub_type")
            if any(getattr(left, key) != getattr(right, key) for key in identity):
                continue
            if comparisons >= 1000 or len(candidates) >= 50:
                return candidates
            comparisons += 1
            score = SequenceMatcher(None, left.content[:500], right.content[:500], autojunk=False).ratio()
            if score >= threshold:
                candidates.append({"left_id": left.id, "right_id": right.id, "similarity": score,
                                   "requires_review": True})
    return candidates


async def deduplicate_memories(character_id: int, threshold: float = VECTOR_DEDUP_THRESHOLD) -> int:
    """Compatibility command: report candidates; zero rows are ever removed."""
    candidates = await duplicate_candidates(character_id, threshold)
    if candidates:
        _logger.info("Memory duplicate review candidates: char=%d count=%d removed=0", character_id, len(candidates))
    return 0
