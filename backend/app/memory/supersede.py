"""#70-C：记忆取代链（M1）+ 轻量级联失效（M2）+ 冷归档/物理 purge 后门。

- 只对「用户明确改口」调用 supersede_memory；噪声/衰减记忆仍走物理删除。
- 级联用每角色一次查询 + Python BFS（depth≤2、单次≤50 条封顶），不引 Datalog、不写递归 SQL。
- 所有写操作失败静默/回滚，不阻塞主链路（沿用项目铁律）。
- 双通道一致：SQLite 提交后再同步 Chroma（先库后向量，避免库回滚了向量却没了）。
"""
from __future__ import annotations

import json

from sqlalchemy import delete, select

from app.db.database import async_session_factory
from app.utils.timeutil import now_naive_utc
from app.utils.logger import get_logger

_logger = get_logger("memory.supersede")

ACTIVE, SUPERSEDED, STALE = "active", "superseded", "stale"
CASCADE_DEPTH = 2
CASCADE_MAX_TOUCH = 50


def _parse_ids(raw) -> set[int]:
    try:
        return {int(x) for x in json.loads(raw or "[]")}
    except Exception:
        return set()


async def supersede_memory(old_id: int, new_id: int | None = None, *,
                           reason: str = "", cascade_depth: int = CASCADE_DEPTH) -> bool:
    """把 old 标记为被 new 取代；并沿 derived_from_ids 向下做 1~2 层 stale 级联。

    - 只标记（status=superseded + valid_to + superseded_by），不物理删——保留可追溯；
    - 双通道：SQLite 提交后再同步 Chroma metadata + bm25_invalidate；失败静默返回 False。
    """
    try:
        now = now_naive_utc()
        async with async_session_factory() as db:
            old = await db.get(_memory_cls(), old_id)
            if old is None or old.status == SUPERSEDED:
                return False
            character_id = old.character_id
            old.status = SUPERSEDED
            old.superseded_by = new_id
            old.valid_to = now
            stale_ids = await _cascade_stale(db, character_id, seed_ids=[old_id], depth=cascade_depth)
            await db.commit()
        # 双通道：摘除/降级向量（SQLite 提交后再动 Chroma，避免库回滚了向量却没了）
        await _mark_vectors([old_id] + stale_ids, {old_id: SUPERSEDED})
        await _bm25_invalidate_safe(character_id)
        _logger.info("supersede mem=%s -> %s, stale=%s", old_id, new_id, stale_ids)
        return True
    except Exception as e:
        _logger.warning("supersede_memory failed old=%s: %s", old_id, e)
        return False


async def _cascade_stale(db, character_id: int, seed_ids: list[int], depth: int) -> list[int]:
    """沿 derived_from_ids 做 BFS（每角色一次查询 + Python BFS）：派生自失效记忆的 active 结论标 stale。

    depth ≤ 2、单次触达 ≤ CASCADE_MAX_TOUCH 封顶；不引 Datalog、不写递归 SQL。
    """
    touched: list[int] = []
    frontier = set(seed_ids)
    seen = set(seed_ids)
    rows = (await db.execute(
        select(_memory_cls()).where(
            _memory_cls().character_id == character_id,
            _memory_cls().status == ACTIVE,
        )
    )).scalars().all()
    for _ in range(max(0, depth)):
        if not frontier or len(touched) >= CASCADE_MAX_TOUCH:
            break
        nxt: set[int] = set()
        for m in rows:
            if m.id in seen:
                continue
            if _parse_ids(m.derived_from_ids) & frontier:
                m.status = STALE
                touched.append(m.id)
                seen.add(m.id)
                nxt.add(m.id)
        frontier = nxt
    return touched


async def restore_memory(memory_id: int) -> bool:
    """回滚一条 supersede/stale（调试/误判纠正用）：status 回 active、清 valid_to/superseded_by。

    - 只回滚该条本身，不自动恢复其下游 stale（配合 restore 单独操作）；
    - 双通道同步 Chroma metadata；失败静默返回 False。
    """
    try:
        async with async_session_factory() as db:
            m = await db.get(_memory_cls(), memory_id)
            if m is None:
                return False
            # N-1：回滚时一并清 superseded_by，避免残留指向已取代它的悬空指针
            m.status, m.valid_to, m.superseded_by = ACTIVE, None, None
            await db.commit()
        await _mark_vectors([memory_id], {memory_id: ACTIVE})
        return True
    except Exception as e:
        _logger.warning("restore_memory failed %s: %s", memory_id, e)
        return False


async def purge_memory(memory_id: int) -> bool:
    """物理删除后门（隐私/被遗忘权）：冷归档行 + 向量 + 主表热行全部物理删除。

    - 同事务内先删 memory_archive 归档行，再物理删除主表热行（``db.delete(mem)``，
      而非 ``service.delete_memory`` 的 is_archived 软删）——「物理删/被遗忘权」名实相符；
    - 返回热行是否曾存在（existed）：存在则曾物理删除，不存在说明热行已被冷归档移走；
    - 删除前把参与的织库卡片置 is_stale=True（与 delete_memory 对齐；异常静默）；
    - 提交后删向量 + bm25_index.invalidate(character_id)；任何异常/超时都静默返回 False。
    """
    _cid = None
    try:
        async with async_session_factory() as db:
            await db.execute(
                delete(_archive_cls()).where(_archive_cls().memory_id == memory_id)
            )
            mem = (await db.execute(
                select(_memory_cls()).where(_memory_cls().id == memory_id)
            )).scalar_one_or_none()
            existed = mem is not None
            if existed:
                _cid = mem.character_id
                # 与 delete_memory 对齐：参与的织库卡片置脏，generate 时懒重建
                try:
                    from app.models.memory import WeaveCard, WeaveCardMemory
                    from sqlalchemy import update as _upd
                    await db.execute(
                        _upd(WeaveCard).where(
                            WeaveCard.id.in_(
                                select(WeaveCardMemory.card_id).where(
                                    WeaveCardMemory.memory_id == memory_id
                                )
                            )
                        ).values(is_stale=True)
                    )
                except Exception:
                    pass
                # #70-C R-6：一并删除 weave_card_memory 关联行，避免孤儿指针（SQLite 未开 FK 不崩，
                # 但「被遗忘权物理删」应干净；空卡片由既有懒重建/清理逻辑兜底）
                try:
                    from app.models.memory import WeaveCardMemory as _WCM
                    from sqlalchemy import delete as _sa_delete
                    await db.execute(_sa_delete(_WCM).where(_WCM.memory_id == memory_id))
                except Exception:
                    pass
                await db.delete(mem)  # 物理删热行（而非 is_archived 软删）
            await db.commit()
        try:
            from app.db.vector_store import delete_memory_vector
            await delete_memory_vector(memory_id)
        except Exception:
            pass
        if _cid is not None:
            await _bm25_invalidate_safe(_cid)
        return existed
    except Exception as e:
        _logger.warning("purge_memory failed %s: %s", memory_id, e)
        return False


async def archive_cold_superseded(days: int = 60, batch: int = 200) -> int:
    """C-2 冷归档：superseded 且 valid_to 超过 days 天的迁入 memory_archive、移出热向量。

    - payload = 原行 JSON 快照（_row_to_dict）；迁出后删热行；向量移出（异常静默）；
    - 批处理；成功返回迁出条数（守护/测试用），异常静默返回 0。
    - 注意（#70-C OBS-5）：物理删热行后，``memories.superseded_by`` 反向链与织库
      ``weave_card_memory.memory_id`` 等引用可能悬空（完整载荷已存 memory_archive）。
      当前无 FK RESTRICT、不会崩；日后做「取代链回溯/织库重建」需回查 memory_archive。
    """
    from datetime import timedelta
    cutoff = now_naive_utc() - timedelta(days=days)
    moved = 0
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(_memory_cls()).where(
                    _memory_cls().status == SUPERSEDED,
                    _memory_cls().valid_to.is_not(None),
                    _memory_cls().valid_to < cutoff,
                ).limit(batch)
            )).scalars().all()
            for m in rows:
                db.add(_archive_cls()(
                    memory_id=m.id, user_id=m.user_id, character_id=m.character_id,
                    payload=json.dumps(_row_to_dict(m), ensure_ascii=False, default=str),
                    archived_reason="superseded_cold",
                ))
                # N-2：与 purge（R-6）对齐——置脏参与织库卡片并删关联行，避免 60 天后批量孤儿关联
                try:
                    from app.models.memory import WeaveCard, WeaveCardMemory
                    from sqlalchemy import update as _upd, delete as _del
                    await db.execute(_upd(WeaveCard).where(
                        WeaveCard.id.in_(select(WeaveCardMemory.card_id).where(WeaveCardMemory.memory_id == m.id))
                    ).values(is_stale=True))
                    await db.execute(_del(WeaveCardMemory).where(WeaveCardMemory.memory_id == m.id))
                except Exception:
                    pass
                await db.delete(m)
                moved += 1
            # N-3：提交前收集 ids，避免 expire_on_commit 后取属性（主键虽安全，但更稳妥）
            archived_ids = [m.id for m in rows]
            await db.commit()
        for mid in archived_ids:
            try:
                from app.db.vector_store import delete_memory_vector
                await delete_memory_vector(mid)
            except Exception:
                pass
    except Exception as e:
        _logger.warning("archive_cold_superseded failed: %s", e)
    return moved


# ── 内部小工具（集中延迟 import，避免循环依赖）──
def _memory_cls():
    from app.models.memory import Memory
    return Memory


def _archive_cls():
    from app.models.memory import MemoryArchive
    return MemoryArchive


def _row_to_dict(m) -> dict:
    return {c.name: getattr(m, c.name, None) for c in m.__table__.columns}


async def _mark_vectors(ids, status_map: dict[int, str]) -> None:
    try:
        from app.db.vector_store import mark_memory_vector_status
        for mid in ids:
            await mark_memory_vector_status(mid, status_map.get(mid, STALE))
    except Exception as e:
        _logger.warning("mark vectors failed: %s", e)


async def _bm25_invalidate_safe(character_id) -> None:
    try:
        # bm25_index 导出的是 invalidate（service 里别名 bm25_invalidate）
        from app.memory.bm25_index import invalidate
        invalidate(character_id)
    except Exception:
        pass
