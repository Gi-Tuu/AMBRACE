# -*- coding: utf-8 -*-
"""#70 方案C：记忆取代链（M1）+ 级联失效（M2）+ 双通道过滤 + 冷归档/purge 测试。

覆盖方案 7.2 中方案 C 相关用例（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；
临时 SQLite 文件库 + monkeypatch，与现有记忆测试同法）：
- test_supersede_excludes_old
- test_cascade_stale_depth2
- test_stale_downweight
- test_flag_off_byte_identical
- test_dual_channel
- test_save_memory_derived_from_ids
- test_archive_cold_purge
- test_purge_hot_memory
- test_add_upsert_always_write_status
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.memory import Memory


async def _noop(*a, **k):
    return None


@pytest.fixture()
def c_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch 相关模块的 async_session_factory（不触碰 backend/data）。

    用假 Chroma collection 隔离向量通道（不触碰真实 ChromaDB），使测试只验证 SQLite 状态过滤 +
    supersede 级联 + 双通道 where/metadata 标记逻辑。
    """
    import app.db.database as db_mod
    import app.memory.service as memsvc
    import app.memory.supersede as sup
    import app.memory.dedup as memdedup
    import app.memory.core as core
    import app.memory.meaning as meaning_mod
    import app.memory.decay as decay
    import app.memory.ai_rating as ai_rating
    import app.memory.summary as summary
    import app.memory.reliability as reliability
    import app.db.vector_store as vs
    import app.memory.bm25_index as bm25
    import app.agent.trace as trace
    import app.events as events

    tmp = tempfile.mkdtemp(prefix="memory_supersede_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    for m in (memsvc, sup, memdedup, core, meaning_mod, decay, ai_rating, summary, reliability):
        monkeypatch.setattr(m, "async_session_factory", factory)
    monkeypatch.setattr(db_mod, "async_session_factory", factory)

    # 假 Chroma collection：避开真实 ChromaDB（不读 backend/data）
    class _FakeCollection:
        def __init__(self):
            self.updates = []
        def get(self, ids=None, include=None, where=None, limit=None, **kw):
            return {"ids": list(ids or []), "embeddings": [], "metadatas": [], "documents": []}
        def add(self, **kw):
            return None
        def upsert(self, **kw):
            return None
        def query(self, **kw):
            return {"ids": [], "documents": [], "metadatas": [], "distances": []}
        def update(self, **kw):
            self.updates.append(kw)
        def delete(self, **kw):
            return None
    fake_col = _FakeCollection()
    async def _get_col():
        return fake_col
    monkeypatch.setattr(vs, "get_or_create_collection", _get_col)

    # 同步副作用隔离（bm25.invalidate / publish / enqueue_task_log 都是同步调用）
    monkeypatch.setattr(bm25, "invalidate", lambda *a, **k: None)
    monkeypatch.setattr(memsvc, "bm25_invalidate", lambda *a, **k: None)
    monkeypatch.setattr(events, "publish", lambda *a, **k: None)
    monkeypatch.setattr(trace, "enqueue_task_log", lambda *a, **k: None)
    # 异步副作用隔离（add/delete 向量、后台去重/意义）
    monkeypatch.setattr(memsvc, "add_memory", _noop)
    monkeypatch.setattr(memsvc, "delete_memory_vector", _noop)
    monkeypatch.setattr(memdedup, "_schedule_dedup", _noop)
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)
    monkeypatch.setattr(core, "maybe_promote_core", _noop)

    yield factory, engine
    asyncio.run(engine.dispose())


def _set_flag(monkeypatch, value: bool):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "memory_supersede", value)


async def _seed(factory, **kw):
    async with factory() as db:
        m = Memory(**kw)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m


async def _get_status(factory, mid):
    async with factory() as db:
        row = await db.get(Memory, mid)
        return row.status if row else None


# ---------------- 1) supersede 后检索不再返回旧记忆 ----------------

def test_supersede_excludes_old(c_db, monkeypatch):
    factory, _ = c_db
    _set_flag(monkeypatch, True)
    import app.memory.service as memsvc

    async def _no_vector(*a, **k):
        return []
    async def _no_bm25(*a, **k):
        return []
    monkeypatch.setattr(memsvc, "vector_search", _no_vector)
    monkeypatch.setattr(memsvc, "bm25_search", _no_bm25)

    async def _main():
        old = await _seed(factory, user_id=1, character_id=1, memory_type="preference",
                          content="用户喜欢喝美式咖啡", importance=40)
        new = await _seed(factory, user_id=1, character_id=1, memory_type="preference",
                          content="用户喜欢喝红茶", importance=40)
        from app.memory.supersede import supersede_memory
        from app.memory.service import search_memories
        ok = await supersede_memory(old.id, new.id, reason="test")
        assert ok is True
        hits = await search_memories(character_id=1, query="用户喜欢", limit=10)
        ids = {h["id"] for h in hits}
        return ids, old.id, new.id, await _get_status(factory, old.id), await _get_status(factory, new.id)

    ids, old_id, new_id, old_st, new_st = asyncio.run(_main())
    assert old_st == "superseded"
    assert new_st == "active"
    assert old_id not in ids     # 旧记忆被排除
    assert new_id in ids         # 新记忆可见


# ---------------- 2) 级联 stale（depth=2，第 3 层不再扩散） ----------------

def test_cascade_stale_depth2(c_db, monkeypatch):
    factory, _ = c_db
    _set_flag(monkeypatch, True)

    async def _main():
        cid = 7
        m101 = await _seed(factory, user_id=1, character_id=cid, memory_type="preference",
                           content="用户喜欢咖啡", importance=40)
        m103 = await _seed(factory, user_id=1, character_id=cid, memory_type="preference",
                           content="用户早上喝咖啡提神", importance=40, derived_from_ids=f"[{m101.id}]")
        m104 = await _seed(factory, user_id=1, character_id=cid, memory_type="preference",
                           content="用户咖啡因敏感", importance=40, derived_from_ids=f"[{m103.id}]")
        m105 = await _seed(factory, user_id=1, character_id=cid, memory_type="preference",
                           content="用户需要控制咖啡饮用量", importance=40, derived_from_ids=f"[{m104.id}]")
        from app.memory.supersede import supersede_memory
        ok = await supersede_memory(m101.id, None, reason="改口")
        s101, s103, s104, s105 = (
            await _get_status(factory, m101.id), await _get_status(factory, m103.id),
            await _get_status(factory, m104.id), await _get_status(factory, m105.id),
        )
        return ok, (s101, s103, s104, s105)

    ok, (s101, s103, s104, s105) = asyncio.run(_main())
    assert ok is True
    assert s101 == "superseded"
    assert s103 == "stale"   # 第 1 层
    assert s104 == "stale"   # 第 2 层
    assert s105 == "active"  # 第 3 层不再扩散（depth=2）


# ---------------- 3) stale 命中降权 0.5，排序落到同分 active 之后 ----------------

def test_stale_downweight(c_db, monkeypatch):
    factory, _ = c_db
    _set_flag(monkeypatch, True)

    async def _main():
        from app.utils.timeutil import now_naive_utc
        now = now_naive_utc()
        active = await _seed(factory, user_id=1, character_id=3, memory_type="preference",
                             content="用户喜欢喝红茶", importance=40, status="active", created_at=now)
        stale = await _seed(factory, user_id=1, character_id=3, memory_type="preference",
                            content="用户喜欢喝奶茶", importance=40, status="stale", created_at=now)
        from app.memory.service import _rerank
        ordered, debug = await _rerank(
            [{"id": active.id}, {"id": stale.id}], 3, return_debug=True,
        )
        return ordered, debug, active.id, stale.id

    ordered, debug, active_id, stale_id = asyncio.run(_main())
    # 排序：active 在前，stale 在后
    assert [r["id"] for r in ordered] == [active_id, stale_id]
    acts = debug["rerank_top"]
    assert [t["id"] for t in acts] == [active_id, stale_id]
    active_score = next(t["score"] for t in acts if t["id"] == active_id)
    stale_score = next(t["score"] for t in acts if t["id"] == stale_id)
    assert stale_score == pytest.approx(active_score * 0.5, rel=1e-3)


# ---------------- 4) flag 关：字节一致回归保护 ----------------

def test_flag_off_byte_identical(c_db, monkeypatch):
    """flag 关：状态谓词不生效（永真），查询/检索与旧链路行为一致。"""
    factory, _ = c_db
    _set_flag(monkeypatch, False)  # 默认关

    async def _main():
        # 断言「行为等价」而非「SQL 字符串相等」：追加永真谓词后编译文本会多一个常量谓词
        # （SQLite 渲染为 AND 1=1 一类），但结果集不变（flag 关 = 无状态过滤）。
        sup_mem = await _seed(factory, user_id=1, character_id=9, memory_type="preference",
                              content="用户喜欢薄荷糖", importance=40, status="superseded")
        from app.memory.service import search_memories, list_memories
        async def _no_vector(*a, **k):
            return []
        async def _no_bm25(*a, **k):
            return []
        import app.memory.service as memsvc
        monkeypatch.setattr(memsvc, "vector_search", _no_vector)
        monkeypatch.setattr(memsvc, "bm25_search", _no_bm25)
        hits = await search_memories(character_id=9, query="薄荷糖", limit=10)
        listed, total = await list_memories(user_id=1, character_id=9)
        return {h["id"] for h in hits}, {m["id"] for m in listed}, sup_mem.id

    hit_ids, listed_ids, sup_id = asyncio.run(_main())
    assert sup_id in hit_ids     # superseded 在 flag 关时仍被检索（旧行为）
    assert sup_id in listed_ids  # superseded 在 flag 关时仍出现在列表


# ---------------- 5) 双通道：SQLite 过滤 + Chroma where/metadata 标记 ----------------

def test_dual_channel(c_db, monkeypatch):
    factory, _ = c_db
    _set_flag(monkeypatch, True)
    import app.db.vector_store as vs

    # _char_where：flag 开 → 带 status 的 $and；关 → 旧 {"character_id"}
    assert vs._char_where(5, supersede_on=True) == {
        "$and": [{"character_id": 5}, {"status": {"$in": ["active", "stale"]}}],
    }
    assert vs._char_where(5, supersede_on=False) == {"character_id": 5}

    async def _main():
        # mark_memory_vector_status：只改 status（合并旧 metadata），不动向量
        class _FakeCol:
            def __init__(self):
                self.updates = []
            def get(self, ids=None, include=None, where=None):
                return {"ids": list(ids or []), "metadatas": [{"memory_id": 1, "character_id": 1, "status": "active"}]}
            def update(self, ids=None, metadatas=None):
                self.updates.append((list(ids), list(metadatas)))
        fake = _FakeCol()
        async def _get_col():
            return fake
        monkeypatch.setattr(vs, "get_or_create_collection", _get_col)
        await vs.mark_memory_vector_status(1, "superseded")
        return fake.updates

    updates = asyncio.run(_main())
    assert len(updates) == 1
    ids, metas = updates[0]
    assert ids == ["1"]
    assert metas[0]["status"] == "superseded"   # metadata 被标记
    assert metas[0]["character_id"] == 1        # 旧 metadata 保留


# ---------------- 6) save_memory derived_from_ids 落库 + merge 自动 ∪ ----------------

def test_save_memory_derived_from_ids(c_db, monkeypatch):
    factory, _ = c_db
    _set_flag(monkeypatch, False)
    import app.memory.service as memsvc
    import app.db.vector_store as vs
    import app.memory.meaning as meaning_mod
    import app.memory.core as core

    async def _fake_embed(c):
        return [0.1, 0.2]
    monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
    # 避开向量语义查重（走字符/24h 合并）
    async def _no_similar(*a, **k):
        return None
    monkeypatch.setattr(memsvc, "find_similar_memory", _no_similar)
    monkeypatch.setattr(vs, "add_memory", _noop)
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)
    monkeypatch.setattr(core, "maybe_promote_core", _noop)

    async def _main():
        # 1) 显式传 derived_from_ids → 落库 JSON
        m0 = await memsvc.save_memory(
            user_id=1, character_id=2, memory_type="insight",
            content="用户重视仪式感", importance=3, skip_dedup=True,
            derived_from_ids=[101, 102],
        )
        async with factory() as db:
            row0 = await db.get(Memory, m0.id)
        df0 = row0.derived_from_ids
        # 2) merge 路径：写入与第一条高度相似 → 保留旧记忆；derived_from_ids 并入调用方
        #    声明来源（不含自身 id，OBS-2 修自环）。
        m1 = await memsvc.save_memory(
            user_id=1, character_id=2, memory_type="preference",
            content="用户喜欢喝美式咖啡", importance=3, skip_dedup=True,
        )
        kept = await memsvc.save_memory(
            user_id=1, character_id=2, memory_type="preference",
            content="用户喜欢喝美式咖啡，早晨必喝", importance=3,
            derived_from_ids=[999],
        )
        async with factory() as db:
            row1 = await db.get(Memory, kept.id)
        df1 = row1.derived_from_ids
        return df0, df1, m1.id, kept.id

    df0, df1, m1_id, kept_id = asyncio.run(_main())
    import json
    assert json.loads(df0) == [101, 102]          # 显式 derived_from_ids 落库
    assert m1_id == kept_id                       # 合并保留旧记忆（未新增）
    assert kept_id not in json.loads(df1)         # 不再并入自身 id（OBS-2 去自环）
    assert 999 in json.loads(df1)                 # merge 并入调用方声明来源 derived_from_ids


# ---------------- 7) 冷归档迁出 + purge 连归档物理删 ----------------

def test_archive_cold_purge(c_db, monkeypatch):
    factory, _ = c_db
    _set_flag(monkeypatch, True)
    import app.memory.supersede as sup
    from app.models.memory import MemoryArchive
    from datetime import timedelta
    from app.utils.timeutil import now_naive_utc

    async def _main():
        old_now = now_naive_utc() - timedelta(days=2)
        m = await _seed(factory, user_id=1, character_id=4, memory_type="preference",
                        content="用户喜欢薄荷糖", importance=40,
                        status="superseded", valid_to=old_now)
        mid = m.id
        # 冷归档（days=0：valid_to < now 即迁入）
        moved = await sup.archive_cold_superseded(days=0)
        async with factory() as db:
            hot = await db.get(Memory, mid)
            arc = (await db.execute(select(MemoryArchive).where(MemoryArchive.memory_id == mid))).scalars().all()
        arc_ids = [a.memory_id for a in arc]
        await sup.purge_memory(mid)
        async with factory() as db:
            arc2 = (await db.execute(select(MemoryArchive).where(MemoryArchive.memory_id == mid))).scalars().all()
        return moved, hot, arc_ids, [a.memory_id for a in arc2]

    moved, hot, arc_ids, arc2_ids = asyncio.run(_main())
    assert moved == 1              # 冷归档迁出 1 条
    assert hot is None             # 热行已删除
    assert len(arc_ids) == 1       # 归档表有 1 条冷归档（memory_id 指向被归档记忆）
    assert arc2_ids == []          # purge 连冷归档一起物理删


# ---------------- 8) 不经过冷归档，直接 purge 一条仍在热表的 active 记忆 ----------------

def test_purge_hot_memory(c_db, monkeypatch):
    factory, _ = c_db
    _set_flag(monkeypatch, True)
    import app.memory.supersede as sup
    from app.models.memory import MemoryArchive
    import app.db.vector_store as vs

    deleted = []
    async def _fake_del(mid, *a, **k):
        deleted.append(mid)
    monkeypatch.setattr(vs, "delete_memory_vector", _fake_del)

    async def _main():
        m = await _seed(factory, user_id=1, character_id=6, memory_type="preference",
                        content="用户喜欢冷萃咖啡", importance=40, status="active")
        mid = m.id
        ok = await sup.purge_memory(mid)
        async with factory() as db:
            row = await db.get(Memory, mid)
            arc = (await db.execute(select(MemoryArchive).where(MemoryArchive.memory_id == mid))).scalars().all()
        return ok, row, [a.memory_id for a in arc], deleted, mid

    ok, row, arc_ids, deleted, mid = asyncio.run(_main())
    assert ok is True                       # 返回「热行曾存在」
    assert row is None                      # 主表热行被物理删除（非 is_archived 软删）
    assert arc_ids == []                    # 归档表无残留
    assert deleted == [mid]                 # 向量删除被调用


# ---------------- 9) BUG-2：add/upsert 默认 status=active 也写 status 键 ----------------

def test_add_upsert_always_write_status(c_db, monkeypatch):
    factory, _ = c_db
    captured = []

    class _FakeCol:
        def add(self, **kw):
            captured.append(("add", kw.get("metadatas")[0]))

        def upsert(self, **kw):
            captured.append(("upsert", kw.get("metadatas")[0]))

    import app.db.vector_store as vs
    fake = _FakeCol()
    async def _get_col():
        return fake
    monkeypatch.setattr(vs, "get_or_create_collection", _get_col)

    async def _main():
        await vs.add_memory(1, 1, "preference", "内容", [0.1, 0.2])
        await vs.upsert_memory_vector(2, 1, "preference", "内容", [0.1, 0.2])

    asyncio.run(_main())
    assert len(captured) == 2
    assert captured[0][1]["status"] == "active"   # add 默认 active 也写 status 键
    assert captured[1][1]["status"] == "active"   # upsert 默认 active 也写 status 键
