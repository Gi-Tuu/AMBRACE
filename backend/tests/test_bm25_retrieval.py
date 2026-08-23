# -*- coding: utf-8 -*-
"""BM25 混合检索测试（2026-08-23 检索增强：jieba + rank-bm25）：

- 中文近似词命中：查询「想画画」能召回含「画水彩」的记忆（jieba 精确模式会漏，需
  cut_for_search + 字符 1-2 元 n-gram 补「画」词元）；
- 索引懒构建仅索引活跃记忆（is_archived=0 且 delete_at is null）；
- 索引缓存与失效（LRU + invalidate 后懒重建）；
- 混合检索：dense（向量）+ sparse（BM25）合并去重，route 标记 hybrid / sparse，
  hit_count=多路合并去重后的候选数；
- BM25 路异常不影响向量主链路（静默返回 []）。

项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库（不触碰 backend/data）；
bm25_index 懒构建复用 memsvc.async_session_factory，故只 monkeypatch memsvc 即可隔离临时库。
"""
import asyncio
import datetime
import json
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.memory.bm25_index as bm25
import app.memory.service as memsvc
from app.models.memory import Memory


async def _noop(*a, **k):
    return None


@pytest.fixture()
def mem_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch memsvc.async_session_factory（不触碰 backend/data）。
    bm25_index 的懒构建复用 memsvc.async_session_factory，故此处 patch 即隔离临时库。"""
    tmp = tempfile.mkdtemp(prefix="bm25_retrieval_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "delete_memory_vector", _noop)
    bm25._persist_root = Path(tmp)   # 2026-08-23 深化：索引持久化隔离到临时目录，不污染生产缓存
    bm25.clear_cache()
    yield factory
    bm25.clear_cache()          # persist_root 仍指向临时目录，清内存+清盘
    bm25._persist_root = None
    asyncio.run(engine.dispose())


async def _seed(factory, **kw):
    async with factory() as db:
        m = Memory(**kw)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m


def _base_kw(**over):
    kw = dict(user_id=1, character_id=1, memory_type="hobby", importance=40.0)
    kw.update(over)
    return kw


# ---------------- 1) 中文近似词命中 ----------------

def test_中文近似词命中_想画画召回画水彩(mem_db, monkeypatch):
    """查询「想画画」能召回含「画水彩」的记忆（jieba 精确模式会漏，靠 n-gram 补「画」词元）。"""
    async def _main():
        target = await _seed(mem_db, **_base_kw(content="用户喜欢画水彩"))
        other1 = await _seed(mem_db, **_base_kw(content="用户今天去公园跑步", memory_type="event"))
        other2 = await _seed(mem_db, **_base_kw(content="用户喜欢喝美式咖啡", memory_type="user_info"))
        other3 = await _seed(mem_db, **_base_kw(content="用户明天有商务会议", memory_type="event"))
        hits = await bm25.search(1, "想画画", top_k=5)
        return hits, target.id, {other1.id, other2.id, other3.id}

    hits, target_id, others = asyncio.run(_main())
    ids = [mid for mid, _s in hits]
    assert target_id in ids                # 「画水彩」被「想画画」召回
    assert ids[0] == target_id             # 唯一含「画」词元的记忆排第一
    assert not (set(ids) & others)         # 无关记忆（公园/咖啡/会议）不误召回


def test_中文近似词_短词单字命中(mem_db, monkeypatch):
    """单字查询「画」直接命中含「画」的记忆（短词精确召回）。"""
    async def _main():
        target = await _seed(mem_db, **_base_kw(content="用户喜欢画水彩"))
        other = await _seed(mem_db, **_base_kw(content="用户喜欢跑步", memory_type="event"))
        hits = await bm25.search(1, "画", top_k=5)
        return hits, target.id, other.id

    hits, target_id, other_id = asyncio.run(_main())
    ids = [mid for mid, _s in hits]
    assert ids[0] == target_id
    assert other_id not in ids


# ---------------- 2) 索引懒构建仅活跃记忆 ----------------

def test_索引懒构建_仅活跃记忆(mem_db, monkeypatch):
    """active 入索引；is_archived=1 与 delete_at 非空 的记忆不参与 BM25。"""
    async def _main():
        active = await _seed(mem_db, **_base_kw(content="用户喜欢画水彩"))
        archived = await _seed(mem_db, **_base_kw(content="用户喜欢画油画", is_archived=True))
        doomed = await _seed(mem_db, **_base_kw(
            content="用户喜欢国画", delete_at=datetime.datetime(2020, 1, 1)))
        hits = await bm25.search(1, "画", top_k=10)
        return {mid for mid, _s in hits}, active.id, archived.id, doomed.id

    ids, a_id, arch_id, doom_id = asyncio.run(_main())
    assert a_id in ids
    assert arch_id not in ids
    assert doom_id not in ids


# ---------------- 3) 索引缓存与失效 ----------------

def test_索引缓存与失效_懒重建(mem_db, monkeypatch):
    """首次检索建索引；命中缓存不重建；invalidate 后清空、再次检索懒重建。"""
    async def _main():
        await _seed(mem_db, **_base_kw(content="用户喜欢画水彩"))
        await bm25.search(1, "画", top_k=5)
        assert bm25.cache_size() == 1

        entry1 = bm25._get_cached(1)                 # 缓存触达，built_at 不变
        await bm25.search(1, "画画", top_k=5)
        entry2 = bm25._get_cached(1)
        assert entry1 is entry2                       # 第二次检索复用缓存（未重建）

        # 写入新记忆 + invalidate → 缓存清空，下次检索懒重建（built_at 更新、cache_size 恢复）
        await _seed(mem_db, **_base_kw(content="用户喜欢画油画"))
        bm25.invalidate(1)
        assert bm25.cache_size() == 0
        hits = await bm25.search(1, "油画", top_k=5)
        assert bm25.cache_size() == 1
        assert any(mid for mid, _s in hits)           # 新记忆已入索引
        return [mid for mid, _s in hits]

    ids = asyncio.run(_main())
    assert ids, "新写入记忆应能被 BM25 召回"


def test_索引缓存_容量上限淘汰(mem_db, monkeypatch):
    """超过缓存上限时按 LRU 淘汰最久未用角色。"""
    async def _main():
        # 预置 21 个角色各有 1 条记忆（真实构建为 BM25Okapi，非空即可）
        for cid in range(1, 22):
            await _seed(mem_db, **_base_kw(character_id=cid, content=f"角色{cid}的独特记忆关键词{cid}"))
        await _seed(mem_db, **_base_kw(character_id=1, content="用户喜欢画水彩"))
        for cid in range(1, 22):
            await bm25.search(cid, f"关键词{cid}", top_k=1)
        assert bm25.cache_size() <= bm25._BM25_CACHE_MAX   # 上限 ≈20，不超容量上限
        # 角色 1（最久未用）应被淘汰，但角色 21（最近）仍在
        # 重新触达角色 1 会 LRU 触达（放回队尾）；此处仅断言超上限时被裁到 ≤ 上限
        assert bm25.cache_size() <= 20

    asyncio.run(_main())


# ---------------- 4) 混合检索：dense + sparse 合并，route 标记 ----------------

def test_混合检索_返回含BM25命中且route标hybrid(mem_db, monkeypatch):
    """dense（向量）召回 b，sparse（BM25）召回 a → 合并去重返回两者，route=hybrid，
    hit_count=多路合并去重后的候选数。"""
    async def _main():
        a = await _seed(mem_db, **_base_kw(content="用户喜欢画水彩"))
        b = await _seed(mem_db, **_base_kw(content="用户下周要去北京开会", memory_type="event", importance=60.0))

        async def _fake_embed(c):
            return [0.1, 0.2]

        async def _fake_vector(character_id, query_embedding, limit=5):
            return [{"id": b.id, "content": b.content, "type": b.memory_type, "importance": b.importance}]

        captured = {}

        def _fake_trace(**kw):
            captured.update(kw)

        monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
        monkeypatch.setattr(memsvc, "vector_search", _fake_vector)
        monkeypatch.setattr("app.agent.trace.enqueue_task_log", _fake_trace)

        hits = await memsvc.search_memories(character_id=1, query="想画画", limit=5)
        return hits, a.id, b.id, captured

    hits, a_id, b_id, captured = asyncio.run(_main())
    ids = [h["id"] for h in hits]
    assert a_id in ids                      # BM25 命中的记忆进入结果
    assert b_id in ids                      # 向量路结果保留
    assert captured["route"] == "hybrid"
    steps = json.loads(captured["steps_json"])
    assert steps["hit_count"] == len({a_id, b_id})   # 候选数 = 多路合并去重后


def test_BM25独自召回_route标sparse(mem_db, monkeypatch):
    """向量路故障（dense 空）、BM25 命中 → 结果来自 sparse，route=sparse，不触发 LIKE 兜底。"""
    async def _main():
        a = await _seed(mem_db, **_base_kw(content="用户喜欢画水彩"))

        async def _boom_embed(c):
            raise RuntimeError("embed down")

        captured = {}

        def _fake_trace(**kw):
            captured.update(kw)

        monkeypatch.setattr(memsvc, "text_embedding", _boom_embed)
        monkeypatch.setattr(memsvc, "vector_search", _boom_embed)
        monkeypatch.setattr("app.agent.trace.enqueue_task_log", _fake_trace)

        hits = await memsvc.search_memories(character_id=1, query="想画画", limit=5)
        return hits, a.id, captured

    hits, a_id, captured = asyncio.run(_main())
    assert [h["id"] for h in hits] == [a_id]
    assert captured["route"] == "sparse"


# ---------------- 5) BM25 路异常不影响向量主链路 ----------------

def test_BM25路异常_不影响向量主链路(mem_db, monkeypatch):
    """service 的 bm25_search 抛异常：sparse 路静默返回 []，dense（向量）主链路仍出结果。"""
    async def _main():
        b = await _seed(mem_db, **_base_kw(content="用户要去北京开会", memory_type="event", importance=60.0))

        async def _fake_embed(c):
            return [0.1, 0.2]

        async def _fake_vector(character_id, query_embedding, limit=5):
            return [{"id": b.id, "content": b.content, "type": b.memory_type, "importance": b.importance}]

        async def _boom_bm25(character_id, query, top_k=5):
            raise RuntimeError("bm25 down")

        monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
        monkeypatch.setattr(memsvc, "vector_search", _fake_vector)
        monkeypatch.setattr(memsvc, "bm25_search", _boom_bm25)   # 让 service 引用的 bm25_search 抛异常

        hits = await memsvc.search_memories(character_id=1, query="北京", limit=5)
        return hits, b.id

    hits, b_id = asyncio.run(_main())
    assert [h["id"] for h in hits] == [b_id]    # 向量主链路不受影响


def test_BM25索引构建异常_静默返回空(mem_db, monkeypatch):
    """索引构建/检索异常时 bm25.search 静默返回 []（不抛、不影响主链路）。"""
    async def _main():
        # 制造 DB 读取异常：把 memsvc.async_session_factory 替换为同步抛错的函数
        def _boom_factory():
            raise RuntimeError("db down")
        monkeypatch.setattr(memsvc, "async_session_factory", _boom_factory)
        bm25.clear_cache()   # 清掉缓存确保走懒构建
        hits = await bm25.search(1, "画", top_k=5)
        return hits

    hits = asyncio.run(_main())
    assert hits == []
