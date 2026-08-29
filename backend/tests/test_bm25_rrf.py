# -*- coding: utf-8 -*-
"""BM25 混合检索深化测试（2026-08-23）：RRF 融合 + 索引持久化。

- RRF 纯函数：公式（Σ 1/(k+rank)）、多路累加、k 可配置、fuse 排序（多路共现优先）；
- RRF 集成（service 双路）：对『语义近但词不近』『词命中但语义弱』两类查询，RRF 融合后的
  排序优于纯合并（纯合并高重要度会埋没真正相关记忆，RRF 用两路 rank 共现把它抬升）；
- RRF 异常退化为纯合并（relevance_bonus 空），不影响主链路；
- 索引持久化：落盘 → 重启（清空内存缓存）直接加载不再懒构建；invalidate 删除缓存文件；
  版本/参数不匹配或损坏文件静默回退懒构建。

项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库（不触碰 backend/data）；
持久化根通过 bm25._persist_root 隔离到临时目录（避免写生产缓存）。
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.memory.rrf as rrf
import app.memory.bm25_index as bm25
import app.memory.service as memsvc
from app.models.memory import Memory


async def _noop(*a, **k):
    return None


@pytest.fixture()
def mem_db(monkeypatch):
    """临时 SQLite 文件库 + 持久化根隔离到临时目录（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix="bm25_rrf_test_")
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
    bm25._persist_root = Path(tmp)
    bm25.clear_cache()
    yield factory, Path(tmp)
    bm25.clear_cache()          # 此时 persist_root 仍指向临时目录，清内存+清盘
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


def _make_mocks(monkeypatch, memories, dense_ranked, sparse_ranked):
    """按给定 rank 顺序打桩：dense 路返回 dense_ranked（ids，相关性降序），sparse 路返回 sparse_ranked。"""
    async def _fake_embed(c):
        return [0.1, 0.2]

    async def _fake_vector(character_id, query_embedding, limit=5):
        out = []
        for mid in dense_ranked:
            m = memories[mid]
            out.append({"id": m.id, "content": m.content, "type": m.memory_type,
                        "importance": float(m.importance or 0)})
        return out

    async def _fake_bm25(character_id, query, top_k=5):
        return [(mid, float(len(sparse_ranked) - i)) for i, mid in enumerate(sparse_ranked)]

    monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
    monkeypatch.setattr(memsvc, "vector_search", _fake_vector)
    monkeypatch.setattr(memsvc, "bm25_search", _fake_bm25)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **k: None)
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", lambda *a, **k: [])


# ---------------- 1) RRF 纯函数 ----------------

def test_rrf_公式与多路累加和k配置():
    """RRF 分 = Σ 1/(k+rank)，rank 从 1 起；同一 id 多路出现则累加；k 可配置且影响灵敏度。"""
    scores = rrf.reciprocal_rank_fusion([[1, 2, 3], [2, 3, 4]], k=60)
    assert abs(scores[1] - 1 / 61) < 1e-12
    assert abs(scores[2] - (1 / 62 + 1 / 61)) < 1e-12
    assert abs(scores[3] - (1 / 63 + 1 / 62)) < 1e-12
    assert abs(scores[4] - 1 / 63) < 1e-12
    assert scores[2] > scores[1] > scores[4]          # 多路高排位共现分数更高
    # k 可配置：k 越小对 top-rank 越敏感
    assert rrf.reciprocal_rank_fusion([[1, 2]], k=10)[1] > rrf.reciprocal_rank_fusion([[1, 2]], k=60)[1]


def test_rrf_fuse_多路共现比单路rank1优先():
    """B 在两路都排第 1（共现），A 仅在单路 rank1 —— 融合后 B 应排第一。"""
    order = rrf.fuse([["A", "B", "C"], ["B", "C"]], k=60)
    assert order[0] == "B"
    assert order.index("A") > order.index("B")   # A（仅单路）排在共现的 B 之后


def test_rrf_normalized_bonus_空集返回空():
    assert rrf.normalized_bonus({}) == {}
    assert rrf.normalized_bonus({}) == {}


# ---------------- 2) RRF 集成：两类查询优于纯合并 ----------------

def test_RRF_语义近但词不近_优于纯合并(mem_db, monkeypatch):
    """语义近（dense rank1=近、但词匹配弱 sparse rank2）记忆 S，被高重要度「词命中但语义弱」的
    W（sparse rank1、dense 无）埋没；RRF 用两路 rank 共现把 S 抬回第一。"""
    factory, _ = mem_db

    async def _main():
        S = await _seed(factory, **_base_kw(content="用户喜欢画水彩"))
        W = await _seed(factory, **_base_kw(content="用户想要一张地图", memory_type="event", importance=55.0))
        memories = {S.id: S, W.id: W}
        _make_mocks(monkeypatch, memories, dense_ranked=[S.id], sparse_ranked=[W.id, S.id])
        # ① 启用 RRF
        hits = await memsvc.search_memories(character_id=1, query="想画画", limit=5)
        rrf_ids = [h["id"] for h in hits]
        # ② 禁用 RRF（打挂）→ 纯合并
        monkeypatch.setattr(memsvc, "_rrf", None)
        pure = await memsvc.search_memories(character_id=1, query="想画画", limit=5)
        pure_ids = [h["id"] for h in pure]
        return S.id, W.id, rrf_ids, pure_ids

    s_id, w_id, rrf_ids, pure_ids = asyncio.run(_main())
    assert rrf_ids[0] == s_id          # RRF 把语义近的 S 抬到第一
    assert pure_ids[0] == w_id         # 纯合并按 importance，W(55) > S(45) 把 W 排第一
    assert rrf_ids != pure_ids          # 排序确实不同


def test_RRF_词命中但语义弱_优于纯合并(mem_db, monkeypatch):
    """高重要度但仅词命中、语义弱（dense 无）的 W，无 RRF 时会排第一；RRF 让两路都相关的 R 胜出。"""
    factory, _ = mem_db

    async def _main():
        R = await _seed(factory, **_base_kw(content="用户每天坐地铁通勤上班", memory_type="event"))
        W = await _seed(factory, **_base_kw(content="用户喜欢地铁站旁的咖啡", importance=55.0))
        memories = {R.id: R, W.id: W}
        _make_mocks(monkeypatch, memories, dense_ranked=[R.id], sparse_ranked=[R.id, W.id])
        hits = await memsvc.search_memories(character_id=1, query="地铁", limit=5)
        rrf_ids = [h["id"] for h in hits]
        monkeypatch.setattr(memsvc, "_rrf", None)
        pure = await memsvc.search_memories(character_id=1, query="地铁", limit=5)
        pure_ids = [h["id"] for h in pure]
        return R.id, W.id, rrf_ids, pure_ids

    r_id, w_id, rrf_ids, pure_ids = asyncio.run(_main())
    assert rrf_ids[0] == r_id           # RRF 两路共现的 R 胜出
    assert pure_ids[0] == w_id          # 纯合并按 importance，W(55) > R(40) 排第一
    assert rrf_ids != pure_ids


def test_RRF异常_退化为纯合并(mem_db, monkeypatch):
    """RRF 计算异常：relevance_bonus 静默为空，退化为纯合并（importance 主导），不影响主链路。"""
    factory, _ = mem_db

    async def _main():
        S = await _seed(factory, **_base_kw(content="用户喜欢画水彩"))
        W = await _seed(factory, **_base_kw(content="用户要去北京开会", memory_type="event", importance=60.0))
        memories = {S.id: S, W.id: W}
        _make_mocks(monkeypatch, memories, dense_ranked=[S.id], sparse_ranked=[W.id])
        monkeypatch.setattr(memsvc, "_rrf", None)   # 打挂 RRF
        hits = await memsvc.search_memories(character_id=1, query="画", limit=5)
        return [h["id"] for h in hits], S.id, W.id

    ids, s_id, w_id = asyncio.run(_main())
    assert ids == [w_id, s_id]   # 仅依赖 importance：W(60) 前、S(40) 后


# ---------------- 3) 索引持久化 ----------------

def test_索引持久化_重启直接加载不再懒构建(mem_db, monkeypatch):
    """建索引落盘；清空内存缓存模拟重启后，直接由盘加载（不调 _build_index），仍能命中。"""
    factory, tmp = mem_db

    async def _main():
        await _seed(factory, **_base_kw(content="用户喜欢画水彩"))
        await bm25.search(1, "画", top_k=5)          # 建索引 + 落盘
        path = tmp / "1.json"
        assert path.exists(), "角色索引应已落盘"
        bm25._cache.clear()                           # 模拟重启：仅清进程内缓存，保留落盘
        called = []

        async def _no_build(cid):
            called.append(cid)
            return None
        monkeypatch.setattr(bm25, "_build_index", _no_build)
        hits = await bm25.search(1, "画", top_k=5)    # 应直接从盘加载
        return hits, called, path

    hits, called, path = asyncio.run(_main())
    assert called == []                               # 未走懒构建
    assert any(mid for mid, _s in hits)               # 仍能命中
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == bm25._INDEX_VERSION


def test_索引持久化_invalidate删除缓存文件(mem_db):
    """invalidate 清内存缓存并删除落盘文件（记忆写入/改/删后调用）。"""
    factory, tmp = mem_db

    async def _main():
        await _seed(factory, **_base_kw(content="用户喜欢画水彩"))
        await bm25.search(1, "画", top_k=5)
        path = tmp / "1.json"
        assert path.exists()
        bm25.invalidate(1)
        return path

    path = asyncio.run(_main())
    assert not path.exists()


def test_索引持久化_版本不匹配_静默回退懒构建(mem_db):
    """落盘版本戳/参数指纹不匹配 → 视为无效，静默回退懒构建并覆盖为最新版本。"""
    factory, tmp = mem_db

    async def _main():
        await _seed(factory, **_base_kw(content="用户喜欢画水彩"))
        path = tmp / "1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": bm25._INDEX_VERSION + 1,
            "jieba_version": "0.0",
            "tokenizer": {"fingerprint": "stale"},
            "memory_ids": [999],
            "tok_docs": [],
        }, ensure_ascii=False), encoding="utf-8")
        bm25._cache.clear()
        hits = await bm25.search(1, "画", top_k=5)    # 版本不匹配 → 回退懒构建
        data = json.loads(path.read_text(encoding="utf-8"))
        return hits, data

    hits, data = asyncio.run(_main())
    assert any(mid for mid, _s in hits)               # 懒构建成功
    assert data["version"] == bm25._INDEX_VERSION     # 已用最新版本覆盖


def test_索引持久化_损坏文件_静默回退懒构建(mem_db):
    """落盘文件损坏（非 JSON）→ 静默回退懒构建，不抛异常。"""
    factory, tmp = mem_db

    async def _main():
        await _seed(factory, **_base_kw(content="用户喜欢画水彩"))
        path = tmp / "1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        bm25._cache.clear()
        hits = await bm25.search(1, "画", top_k=5)
        return hits

    hits = asyncio.run(_main())
    assert any(mid for mid, _s in hits)               # 虽文件损坏，仍懒构建命中
