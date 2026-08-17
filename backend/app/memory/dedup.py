"""记忆去重与融合：写路径向量查重兜底 + 全量向量去重 + 半重复 LLM 融合"""
import asyncio
import time

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.utils.logger import get_logger
from app.db.vector_store import get_all_vectors_by_character
from app.memory.constants import (
    VECTOR_DEDUP_THRESHOLD, DEDUP_MIN_INTERVAL,
)

_logger = get_logger("memory.dedup")


_dedup_last_run: dict[int, float] = {}
_dedup_locks: dict[int, asyncio.Lock] = {}



async def _schedule_dedup(character_id: int):
    """节流调度去重（写记忆时调用，避免每次写都触发全量 O(n^2) 比较）"""
    now = time.time()
    if now - _dedup_last_run.get(character_id, 0) < DEDUP_MIN_INTERVAL:
        return
    _dedup_last_run[character_id] = now
    lock = _dedup_locks.setdefault(character_id, asyncio.Lock())
    if lock.locked():
        return
    async with lock:
        try:
            await deduplicate_memories(character_id)
        except Exception as e:
            _logger.warning("Dedup failed for char=%d: %s", character_id, e)

async def deduplicate_memories(character_id: int, threshold: float = VECTOR_DEDUP_THRESHOLD) -> int:
    """去除该角色的重复记忆，返回删除数量。

    优先语义去重：取该角色全部向量，numpy 两两余弦相似度（to_thread 防阻塞事件循环），
    相似度 >= threshold（默认 0.9）视为重复，保留置顶/重要性高的一条；
    无向量的记忆之间回退字符级 SequenceMatcher（0.72）。
    """
    from app.memory.service import delete_memory
    async with async_session_factory() as db:
        result = await db.execute(
            select(Memory)
            .where(Memory.character_id == character_id, Memory.is_archived == False)
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
        )
        memories = result.scalars().all()

    if len(memories) < 2:
        return 0

    by_id = {m.id: m for m in memories}
    to_delete = set()

    # ---- 1) 语义去重：向量两两余弦相似度（精确全量，替代原 O(n^2) 字符比较）----
    vectors = await get_all_vectors_by_character(character_id)
    vec_ids = [m.id for m in memories if m.id in vectors]

    def _find_vector_pairs(mem_ids: list[int], thr: float) -> list[tuple[int, int, float]]:
        import numpy as np
        ids = list(mem_ids)
        X = np.array([vectors[i] for i in ids], dtype=np.float32)
        norms = np.linalg.norm(X, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        X = X / norms
        pairs = []
        n = len(ids)
        for i in range(n - 1):
            sims = X[i + 1:] @ X[i]
            hits = np.nonzero(sims >= thr)[0]
            for k in hits:
                pairs.append((ids[i], ids[i + 1 + int(k)], float(sims[int(k)])))
        pairs.sort(key=lambda p: p[2], reverse=True)
        return pairs

    if len(vec_ids) >= 2:
        pairs = await asyncio.to_thread(_find_vector_pairs, list(vec_ids), threshold)
        alive = set(vec_ids)
        for a, b, _sim in pairs:
            if a not in alive or b not in alive:
                continue
            ma, mb = by_id[a], by_id[b]
            if ma.is_pinned and mb.is_pinned:
                continue
            if ma.is_pinned:
                lower = b
            elif mb.is_pinned:
                lower = a
            else:
                lower = b if float(ma.importance or 0.0) >= float(mb.importance or 0.0) else a
            alive.discard(lower)
            to_delete.add(lower)

    # ---- 2) 字符级回退：仅对无向量的记忆两两比较（数量少，成本可控）----
    no_vec = [m for m in memories if m.id not in vectors]
    if len(no_vec) >= 2:
        from difflib import SequenceMatcher
        alive2 = set(m.id for m in no_vec)
        for i in range(len(no_vec)):
            ma = no_vec[i]
            if ma.id not in alive2:
                continue
            for j in range(i + 1, len(no_vec)):
                mb = no_vec[j]
                if mb.id not in alive2:
                    continue
                ca = (ma.content or "")[:100]
                cb = (mb.content or "")[:100]
                if len(ca) < 5 or len(cb) < 5:
                    continue
                if SequenceMatcher(None, ca, cb).ratio() < 0.72:
                    continue
                if ma.is_pinned and mb.is_pinned:
                    continue
                if ma.is_pinned:
                    lower = mb.id
                elif mb.is_pinned:
                    lower = ma.id
                else:
                    lower = mb.id if float(ma.importance or 0.0) >= float(mb.importance or 0.0) else ma.id
                alive2.discard(lower)
                to_delete.add(lower)

    # ---- 执行删除（软删除 + 清向量）----
    deleted = 0
    for mem_id in to_delete:
        try:
            if await delete_memory(mem_id):
                deleted += 1
        except Exception:
            pass

    if deleted:
        _logger.info("Deduplicated %d memories for character %d", deleted, character_id)
    return deleted
