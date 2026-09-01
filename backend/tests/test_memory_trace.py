# -*- coding: utf-8 -*-
"""#70 方案B：检索轨迹可观察（Memory Injection Viewer）测试（2026-08-30）。

覆盖方案 B 的落地验收点（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；
临时 SQLite 文件库 + monkeypatch，与现有记忆测试同法）：
- ``test_rerank_return_debug_default_false``：return_debug 默认 False 返回 list（零行为变化，清理 _score）。
- ``test_rerank_return_debug_true_fields``：True 返回 (list, debug)，debug 含 db_pool + rerank_top 且字段正确。
- ``test_rerank_debug_volume_limit``：rerank_top 超过 10 条时钳制到 ≤10（体积上限）。
- ``test_search_memories_flag_on_trace_debug``：flag 开 → trace 写扩充 debug（query/派生/各路/RRF/rerank/返回/延迟）。
- ``test_search_memories_flag_off_trace_old_schema``：flag 关 → trace 与现状一致（仅 query/queries/hit_ids/hit_count/returned=int）。
- ``test_search_memories_flag_off_behavior_identical``：flag 关/开检索结果逐项一致（回归保护）。
- ``test_search_memories_returns_list_not_tuple``：flag 开时 search_memories 仍返回 list。
- ``test_debug_volume_limits``：dense/sparse ≤5、rrf_top ≤10、returned ≤limit、preview ≤60、steps_json ≤8000。
- ``test_memory_trace_api_ownership``：非本人角色 404（归属校验）。
- ``test_memory_trace_api_limit_clamp``：limit 钳制 1-50。
- ``test_memory_trace_api_bad_json``：steps 解析失败降级 {}（坏 JSON）。
"""
import asyncio
import json
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

import app.memory.service as memsvc
from app.agent.loop import AGENT_FLAGS
from app.api import characters as characters_api  # F5-b：router 壳
from app.application import characters as characters_svc  # F5-b：实现迁至 application，patch 须指向定义模块
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.models.agent_task_log import AgentTaskLog
from app.models.character import AICharacter
from app.models.memory import Memory


async def _noop(*a, **k):
    return None


# ---------------- 通用 fixture：临时 SQLite 文件库（不触碰 backend/data） ----------------

@pytest.fixture()
def mem_db(monkeypatch):
    """临时库：monkeypatch 记忆模块的 async_session_factory（_rerank/search_memories 用）。"""
    tmp = tempfile.mkdtemp(prefix="memory_trace_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "delete_memory_vector", _noop)
    yield factory
    asyncio.run(engine.dispose())


async def _seed_mem(factory, **kw):
    async with factory() as db:
        m = Memory(**kw)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m


def _base_kw(**over):
    kw = dict(
        user_id=1, character_id=1, memory_type="event",
        content="x", importance=50.0, is_locked=False,
        strength_days=5.0,
    )
    kw.update(over)
    return kw


async def _fake_embed(c):
    return [0.1, 0.2]


# ---------------- _rerank：return_debug 开关 ----------------

def test_rerank_return_debug_default_false(mem_db):
    """return_debug 默认 False → 返回 list（非 tuple），且清理了临时 _score；字段为 _final 白名单。"""
    async def _main():
        m = await _seed_mem(mem_db, **_base_kw(
            content="用户喜欢美式咖啡", importance=80.0, why_it_matters="咖啡因提神"))
        results = await memsvc._rerank(
            [{"id": m.id, "content": m.content, "type": m.memory_type, "importance": m.importance}],
            character_id=1, return_debug=False,
        )
        return results, m

    results, m = asyncio.run(_main())
    assert isinstance(results, list)
    assert not isinstance(results, tuple)
    assert all("_score" not in r for r in results)        # 清理临时分数字段
    assert results[0]["id"] == m.id
    assert results[0]["why_it_matters"] == "咖啡因提神"    # A 已补 why（供 L0）
    assert results[0]["status"] == "active"


def test_rerank_return_debug_true_fields(mem_db):
    """return_debug=True → 返回 (ordered, debug)；debug 含 db_pool + rerank_top（id/score/importance/has_why/status）。"""
    async def _main():
        m = await _seed_mem(mem_db, **_base_kw(
            content="用户喜欢美式咖啡", importance=80.0, why_it_matters="咖啡因提神"))
        out = await memsvc._rerank(
            [{"id": m.id, "content": m.content, "type": m.memory_type, "importance": m.importance}],
            character_id=1, return_debug=True,
        )
        return out, m

    (ordered, debug), m = asyncio.run(_main())
    assert isinstance(ordered, list)
    assert isinstance(debug, dict)
    assert debug["db_pool"] == 1
    assert isinstance(debug["rerank_top"], list) and len(debug["rerank_top"]) == 1
    item = debug["rerank_top"][0]
    assert set(item.keys()) == {"id", "score", "importance", "has_why", "status"}
    assert item["id"] == m.id
    assert isinstance(item["score"], float)
    assert isinstance(item["importance"], float)
    assert item["has_why"] is True
    assert item["status"] == "active"
    assert all("_score" not in r for r in ordered)       # 清理临时字段


def test_rerank_debug_volume_limit(mem_db):
    """rerank_top 只保留 Top10（体积上限）。"""
    async def _main():
        ids = []
        for i in range(14):
            m = await _seed_mem(mem_db, **_base_kw(
                content=f"记忆{i}", importance=float(50 + i),
                why_it_matters=f"why{i}" if i % 2 == 0 else None))
            ids.append((m.id, i))
        results = [{"id": i, "content": "x", "type": "event", "importance": 50.0} for i, _ in ids]
        _, debug = await memsvc._rerank(results, character_id=1, return_debug=True)
        return debug, ids

    debug, ids = asyncio.run(_main())
    assert len(debug["rerank_top"]) == 10                 # 超 10 条钳制到 10
    assert debug["db_pool"] == 14
    # 分数降序：importance 高在前（base=importance，无其他加分）
    scores = [item["score"] for item in debug["rerank_top"]]
    assert scores == sorted(scores, reverse=True)
    assert all(item["has_why"] in (True, False) for item in debug["rerank_top"])


# ---------------- search_memories：双层助手（先种数据，再单次检索） ----------------

async def _seed_pair(mem_db):
    """种两条记忆：a（55）与 b（60，importance 更高）。"""
    a = await _seed_mem(mem_db, **_base_kw(
        character_id=1, content="用户喜欢画水彩", memory_type="event", importance=55.0))
    b = await _seed_mem(mem_db, **_base_kw(
        character_id=1, content="用户下周要去北京开会", memory_type="event", importance=60.0))
    return a, b


async def _search_once(mem_db, monkeypatch, flag, a, b):
    """在已种数据 a/b 上跑一次 search_memories（双路命中 hybrid），捕获 trace 并返回 (hits, raw, steps)。"""
    async def _fake_vector(character_id, query_embedding, limit=5):
        return [
            {"id": a.id, "content": a.content, "type": a.memory_type, "importance": a.importance},
            {"id": b.id, "content": b.content, "type": b.memory_type, "importance": b.importance},
        ]

    async def _fake_bm25(character_id, query, top_k=5):
        return [(a.id, 1.0)]

    captured = {}

    def _fake_trace(**kw):
        captured.update(kw)

    monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
    monkeypatch.setattr(memsvc, "vector_search", _fake_vector)
    monkeypatch.setattr(memsvc, "bm25_search", _fake_bm25)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", _fake_trace)
    monkeypatch.setitem(AGENT_FLAGS, "memory_trace_debug", flag)

    hits = await memsvc.search_memories(character_id=1, query="想画画", limit=5)
    steps = json.loads(captured["steps_json"])
    return hits, captured["steps_json"], steps


def test_search_memories_flag_on_trace_debug(mem_db, monkeypatch):
    """flag 开：trace 写扩充 debug（query/derived/dense/sparse/rrf/route/candidate/returned/延迟/db_pool/rerank_top）。"""
    a, b = asyncio.run(_seed_pair(mem_db))
    hits, raw, steps = asyncio.run(_search_once(mem_db, monkeypatch, True, a, b))
    assert "query" in steps and steps["query"] == "想画画"
    assert isinstance(steps["derived_queries"], list)
    assert steps["dense_hits"] == [a.id, b.id]            # 向量路命中 id 保留
    assert steps["sparse_hits"] == [a.id]                 # BM25 路命中 id
    assert steps["rrf_top"] and len(steps["rrf_top"]) <= 10
    assert steps["route"] == "hybrid"                     # 双路命中 → hybrid
    assert steps["candidate_count"] >= 1
    assert steps["hit_count"] == steps["candidate_count"]  # 兼容旧读端
    assert isinstance(steps["returned"], list)             # 新 schema：{id, preview} 列表
    assert len(steps["returned"]) == len(hits)
    assert steps["returned"][0]["id"] in (a.id, b.id)
    assert "latency_ms" in steps
    assert steps["db_pool"] >= 1
    assert steps["rerank_top"] and len(steps["rerank_top"]) <= 10
    assert all(set(k) >= {"id", "score", "importance", "has_why", "status"}
               for k in steps["rerank_top"])
    assert len(raw) <= 8000                               # steps_json 硬上限


def test_search_memories_flag_off_trace_old_schema(mem_db, monkeypatch):
    """flag 关：trace 与现状一致（仅 query/queries/hit_ids/hit_count/returned=int，不写扩充 debug）。"""
    a, b = asyncio.run(_seed_pair(mem_db))
    hits, raw, steps = asyncio.run(_search_once(mem_db, monkeypatch, False, a, b))
    assert set(steps.keys()) == {"query", "queries", "hit_ids", "hit_count", "returned"}
    assert isinstance(steps["hit_count"], int)
    assert isinstance(steps["returned"], int)             # 旧 schema：returned 是 int
    assert steps["hit_count"] >= 1
    # 不写扩充 debug（避免数据膨胀）
    for k in ("candidate_count", "dense_hits", "sparse_hits", "rrf_top", "rerank_top", "db_pool", "latency_ms", "route"):
        assert k not in steps


def test_search_memories_flag_off_behavior_identical(mem_db, monkeypatch):
    """flag 关/开：返回的注入结果逐项一致（回归保护，检索/排序不变）。"""
    a, b = asyncio.run(_seed_pair(mem_db))
    hits_on, _, _ = asyncio.run(_search_once(mem_db, monkeypatch, True, a, b))
    hits_off, _, _ = asyncio.run(_search_once(mem_db, monkeypatch, False, a, b))
    assert hits_on == hits_off                             # 检索/排序结果一致（trace 之外）


def test_search_memories_returns_list_not_tuple(mem_db, monkeypatch):
    """flag 开：search_memories 仍返回 list（_rerank(return_debug=True) 的 debug 不外泄到结果）。"""
    a, b = asyncio.run(_seed_pair(mem_db))
    hits, _, _ = asyncio.run(_search_once(mem_db, monkeypatch, True, a, b))
    assert isinstance(hits, list)
    assert all(isinstance(h, dict) for h in hits)


# ---------------- debug 体积上限 ----------------

def test_debug_volume_limits(mem_db, monkeypatch):
    """dense ≤5 / rrf_top ≤10 / returned ≤limit / preview ≤60 / steps_json ≤8000。"""
    async def _main():
        mems = []
        for i in range(12):
            m = await _seed_mem(mem_db, **_base_kw(
                character_id=1, content="用户喜欢内容" * 30, importance=float(50 + i)))
            mems.append(m)

        async def _fake_vector(character_id, query_embedding, limit=5):
            return [{"id": m.id, "content": m.content, "type": m.memory_type, "importance": m.importance}
                    for m in mems]

        async def _fake_bm25(character_id, query, top_k=5):
            return [(m.id, 1.0) for m in mems]

        captured = {}

        def _fake_trace(**kw):
            captured.update(kw)

        monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
        monkeypatch.setattr(memsvc, "vector_search", _fake_vector)
        monkeypatch.setattr(memsvc, "bm25_search", _fake_bm25)
        monkeypatch.setattr("app.agent.trace.enqueue_task_log", _fake_trace)
        monkeypatch.setitem(AGENT_FLAGS, "memory_trace_debug", True)

        await memsvc.search_memories(character_id=1, query="画", limit=5)
        return captured["steps_json"]

    raw = asyncio.run(_main())
    steps = json.loads(raw)
    assert len(steps["dense_hits"]) <= 5                  # 每路 id ≤5
    assert len(steps["sparse_hits"]) <= 5
    assert len(steps["rrf_top"]) <= 10                    # rrf_top ≤10
    assert len(steps["rerank_top"]) <= 10                 # rerank_top ≤10
    assert len(steps["returned"]) <= 5                    # returned ≤limit
    assert all(len(item["preview"]) <= 60 for item in steps["returned"])  # preview ≤60
    assert len(raw) <= 8000                               # steps_json ≤8000 字符


# ---------------- API：GET /{character_id}/memory-trace ----------------

@pytest.fixture()
def trace_db(monkeypatch):
    """临时库：种子一个角色；同时 monkeypatch characters_api.async_session_factory 供端点查询。"""
    tmp = tempfile.mkdtemp(prefix="memory_trace_api_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    async def _seed():
        async with factory() as db:
            db.add(AICharacter(id=7, user_id=1, name="测试", cognitive_loop_enabled=False))
            await db.commit()

    asyncio.run(_seed())
    monkeypatch.setattr(characters_svc, "async_session_factory", factory)
    yield factory, 7
    asyncio.run(engine.dispose())


def _make_client(factory, user_id=1) -> TestClient:
    app = FastAPI()
    app.include_router(characters_api.router)

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _seed_log(factory, character_id, **kw):
    async def _main():
        async with factory() as db:
            db.add(AgentTaskLog(character_id=character_id, user_id=1, **kw))
            await db.commit()
    asyncio.run(_main())


def test_memory_trace_api_ownership(trace_db):
    """非本人角色请求 → 404（归属校验）；本人 → 200。"""
    factory, char_id = trace_db
    _seed_log(factory, char_id, trigger="memory_search", route="hybrid",
              steps_json='{"query":"x","hit_count":2,"returned":1}', latency_ms=5, status="ok")
    r = _make_client(factory, user_id=1).get(f"/api/v1/characters/{char_id}/memory-trace")
    assert r.status_code == 200
    body = r.json()
    assert body["character_id"] == char_id
    assert len(body["traces"]) == 1
    r2 = _make_client(factory, user_id=999).get(f"/api/v1/characters/{char_id}/memory-trace")
    assert r2.status_code == 404


def test_memory_trace_api_limit_clamp(trace_db):
    """limit 钳制 1-50：limit=1 取 1 条；limit=0 → 默认 20；limit=9999 → 下钳到 50。"""
    factory, char_id = trace_db

    async def _seed_many():
        async with factory() as db:
            for i in range(60):
                db.add(AgentTaskLog(character_id=char_id, user_id=1, trigger="memory_search",
                                    route="dense", steps_json=json.dumps({"query": f"q{i}", "hit_count": 1, "returned": 1}),
                                    latency_ms=i, status="ok"))
            await db.commit()

    asyncio.run(_seed_many())
    r1 = _make_client(factory).get(f"/api/v1/characters/{char_id}/memory-trace", params={"limit": 1})
    assert len(r1.json()["traces"]) == 1                 # 钳到 ≥1（精确取 1 条）
    r0 = _make_client(factory).get(f"/api/v1/characters/{char_id}/memory-trace", params={"limit": 0})
    assert len(r0.json()["traces"]) == 20                # 0 → 默认 20（钳在 [1,50]）
    r_big = _make_client(factory).get(f"/api/v1/characters/{char_id}/memory-trace", params={"limit": 9999})
    assert len(r_big.json()["traces"]) == 50             # 9999 → 下钳到 50
    assert r_big.json()["traces"][0]["id"] > r_big.json()["traces"][1]["id"]  # id desc


def test_memory_trace_api_bad_json(trace_db):
    """steps_json 坏 JSON → 降级 {}（不抛错）；非 memory_search 触发不计入。"""
    factory, char_id = trace_db
    _seed_log(factory, char_id, trigger="memory_search", route="dense",
              steps_json="not-json{{{", latency_ms=5, status="ok")
    _seed_log(factory, char_id, trigger="chat", route="direct",
              steps_json='{"query":"chat"}', latency_ms=1, status="ok")  # 非 memory_search
    r = _make_client(factory).get(f"/api/v1/characters/{char_id}/memory-trace")
    assert r.status_code == 200
    traces = r.json()["traces"]
    assert len(traces) == 1                               # 仅 memory_search 计入
    assert traces[0]["steps"] == {}                       # 坏 JSON 降级 {}
    assert traces[0]["route"] == "dense"
