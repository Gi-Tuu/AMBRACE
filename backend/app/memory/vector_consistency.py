# -*- coding: utf-8 -*-
"""P1-2（2026-11-11 架构治理）：向量内容一致性对账。

背景：`vector_sync_watch.coverage()` 只做 **ID 集合差**——同 ID、不同内容（写入侧向量刷新失败、
或补建与改写交错）永远发现不了。本模块在写入侧把 `content_sha256` 记进向量 metadata，
对账侧用「库内 content 哈希 ≠ 向量 metadata 哈希」识别漂移，并复用补建通道重嵌。

flag ``vector_content_check_v1``（默认关）：关 ⇒ tick 行为逐字不变（只补 missing）；
开 ⇒ missing 之外再报/修 content drift。旧向量没有该 metadata 键 ⇒ 视为「未知」不报（不回溯误伤）。
"""
from __future__ import annotations

import hashlib

from app.flags.agent_flags import AGENT_FLAGS
from app.utils.logger import get_logger

FLAG = "vector_content_check_v1"
META_KEY = "content_sha256"

_logger = get_logger("memory.vector_consistency")


def enabled() -> bool:
    return bool(AGENT_FLAGS.get(FLAG, False))


def content_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def stale_among(meta_hashes: dict[int, str], db_contents: dict[int, str]) -> list[int]:
    """纯函数：给出「metadata 记的哈希 ≠ 当前库内 content 哈希」的 id（缺失库内行/无哈希均跳过）。"""
    out: list[int] = []
    for mid, old_hash in meta_hashes.items():
        content = db_contents.get(mid)
        if content is None or not old_hash:
            continue
        if content_sha256(content) != old_hash:
            out.append(int(mid))
    return sorted(out)


async def collect_stale(limit: int = 200) -> list[int]:
    """读向量 metadata 与库内 content，返回 content drift 的 id（最多 limit 条）。"""
    from sqlalchemy import select

    from app.db.database import async_session_factory
    from app.db.vector_store import get_or_create_collection
    from app.models.memory import Memory

    try:
        collection = await get_or_create_collection()
        got = await __import__("asyncio").to_thread(
            collection.get, include=["metadatas"])
    except Exception as e:  # noqa: BLE001 —— 向量库异常不向调度冒泡
        _logger.warning("vector consistency read failed: %s: %s", e.__class__.__name__, e)
        return []
    ids = got.get("ids") or []
    metas = got.get("metadatas") or []
    meta_hashes: dict[int, str] = {}
    for raw_id, meta in zip(ids, metas):
        try:
            mid = int(raw_id)
        except (TypeError, ValueError):
            continue
        h = (meta or {}).get(META_KEY) if isinstance(meta, dict) else None
        if isinstance(h, str) and h:
            meta_hashes[mid] = h
    if not meta_hashes:
        return []
    async with async_session_factory() as db:
        rows = (await db.execute(select(Memory.id, Memory.content).where(
            Memory.id.in_(list(meta_hashes.keys()))))).all()
    db_contents = {int(r[0]): (r[1] or "") for r in rows}
    stale = stale_among(meta_hashes, db_contents)
    return stale[:limit]


async def repair_content_drift(limit: int = 50) -> dict:
    """对 content drift 的 id 重嵌并 upsert（单条失败继续；返回统计）。"""
    from sqlalchemy import select

    from app.db.database import async_session_factory
    from app.db.vector_store import upsert_memory_vector
    from app.memory.embedding import text_embedding
    from app.models.memory import Memory

    ids = await collect_stale(limit=limit)
    fixed = failed = 0
    if not ids:
        return {"candidates": 0, "fixed": 0, "failed": 0}
    async with async_session_factory() as db:
        rows = (await db.execute(select(Memory).where(Memory.id.in_(ids)))).scalars().all()
    for m in rows:
        try:
            text = m.content or m.title or ""
            emb = await text_embedding(text)
            await upsert_memory_vector(
                memory_id=m.id, character_id=m.character_id, memory_type=m.memory_type,
                content=text, embedding=emb, importance=int(m.importance or 0),
                status=m.status, user_id=m.user_id,
            )
            fixed += 1
        except Exception as e:  # noqa: BLE001 —— 单条失败不阻塞
            failed += 1
            _logger.warning("vector content repair failed memory_id=%s: %s: %s",
                            m.id, e.__class__.__name__, e)
    _logger.info("vector content repair: candidates=%s fixed=%s failed=%s",
                 len(ids), fixed, failed)
    return {"candidates": len(ids), "fixed": fixed, "failed": failed}
