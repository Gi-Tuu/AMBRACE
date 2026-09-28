# -*- coding: utf-8 -*-
"""批 0-14 · 按需「查」记忆只读端点测试（GET /api/v1/memories/lookup）。

覆盖：
- 命中：返回命中原文片段 + 时间（created_at ISO）+ 角色归属；
- 未命中：200 + memories 空数组（不报错）；
- 条数：limit 可传、硬顶 20 夹取、limit<=0 视作未传回落默认 5；
- 鉴权：指定他人角色 → 404；不存在的角色 → 404；空 query → 400；
- 可见范围：不传 character_id 时跨本账号多个角色合并；跨家庭记忆一条都不外泄；
- 复用既有检索链：端点只调 app.memory.retrieve.search_memories（不改排序/不新建召回），
  跨角色按「各角色内部名次」轮转合并，长文本按 200 字截断；
- 只读：跑完端点 memories / 回执表逐字节不变；
- 路由注册：/lookup 必须在 /{memory_id} 之前（否则被路径参数路由吃掉）。

纪律：临时库走 tests/_dbclone（不连生产库）；项目未装 pytest-asyncio，统一 asyncio.run 同步执行；
真实链用例把 dense/sparse 两路打桩成空 ⇒ 走检索链内部的 LIKE 关键词兜底（仍然零新实现）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

import app.memory.service as memsvc
from app.api import memories as mem_api
from app.db import database as db_mod
from app.models.memory import Memory, MemoryWriteReceipt

pytestmark = pytest.mark.slow

T0 = datetime(2026, 9, 20, 10, 0, 0)   # naive UTC（与库内存储口径一致）


def _noop_trace(**kw):
    """检索链出口 trace 打桩（enqueue_task_log 是同步调用，非 async）。"""
    return None


async def _boom(*a, **k):
    raise RuntimeError("embedding down（用例刻意打桩，逼检索链走关键词兜底）")


async def _no_sparse(*a, **k):
    return []


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时库 + 两处会话工厂 patch（api 走 db_mod，检索链走 memsvc）+ 向量/BM25/trace 打桩。"""
    engine = clone_engine(tmp_path / "lookup.db")
    factory = make_session_factory(engine)

    async def _seed_parents():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="lk_u1", nickname="查用户"))
            db.add(User(id=2, username="lk_u2", nickname="外人用户"))
            db.add(AICharacter(id=101, user_id=1, name="查角色A"))
            db.add(AICharacter(id=102, user_id=1, name="查角色B"))
            db.add(AICharacter(id=202, user_id=2, name="外人角色"))
            await db.commit()

    asyncio.run(_seed_parents())
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    # 检索链两路打桩：向量路抛异常（内部静默退化）+ BM25 路空 ⇒ 走链内 LIKE 关键词兜底
    monkeypatch.setattr(memsvc, "text_embedding", _boom)
    monkeypatch.setattr(memsvc, "vector_search", _boom)
    monkeypatch.setattr(memsvc, "bm25_search", _no_sparse)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", _noop_trace)
    yield {"factory": factory}
    asyncio.run(engine.dispose())


def _seed(factory, *, user_id=1, character_id=101, content="用户喜欢画水彩",
          memory_type="user_info", importance=50.0, created_at=T0, **kw) -> int:
    async def _run():
        async with factory() as db:
            m = Memory(user_id=user_id, character_id=character_id, memory_type=memory_type,
                       content=content, importance=importance, created_at=created_at,
                       source="chat", status="active", **kw)
            db.add(m)
            await db.commit()
            return m.id
    return asyncio.run(_run())


def _api(factory, query="水彩", **kw):
    """直调端点函数（与 test_fact_history_memories 同口径，不经 TestClient）。"""
    return asyncio.run(mem_api.lookup_memories(query=query, user_id=kw.pop("user_id", 1), lang="zh", **kw))


def _err(query="水彩", **kw):
    with pytest.raises(HTTPException) as ei:
        _api(None, query=query, **kw)
    return ei.value


def _ids(resp):
    return [m["id"] for m in resp["memories"]]


# ───────────────────────── 命中 / 未命中 ─────────────────────────

def test_命中_带原文片段与时间(env):
    mid = _seed(env["factory"], content="用户喜欢画水彩，周末常去美术馆", created_at=T0)
    r = _api(env["factory"], query="水彩")
    assert r["status"] == "ok" and r["query"] == "水彩"
    assert r["total"] == 1 and _ids(r) == [mid]
    hit = r["memories"][0]
    assert hit["character_id"] == 101
    assert hit["memory_type"] == "user_info"
    assert hit["snippet"] == "用户喜欢画水彩，周末常去美术馆"
    assert hit["truncated"] is False
    assert hit["created_at"] == T0.isoformat()
    assert hit["importance"] == 50.0


def test_未命中_空数组不报错(env):
    _seed(env["factory"], content="用户喜欢画水彩")
    r = _api(env["factory"], query="紫色气球漂流瓶")
    assert r["status"] == "ok" and r["memories"] == [] and r["total"] == 0


def test_归档记忆不返回(env):
    """检索链口径（is_archived 剔除）在端点上原样生效，不在这里另开一套过滤。"""
    _seed(env["factory"], content="已归档的水彩课", is_archived=True)
    assert _api(env["factory"], query="水彩")["memories"] == []


# ───────────────────────── 条数：可传 + 硬顶 ─────────────────────────

def test_条数硬顶_limit超20夹到20(env):
    f = env["factory"]
    for i in range(25):
        _seed(f, content=f"用户喜欢画水彩{i}", importance=50.0 + i, created_at=T0 + timedelta(minutes=i))
    r = _api(f, query="水彩", limit=100)
    assert r["limit"] == mem_api.LOOKUP_LIMIT_MAX == 20   # 回的是生效值，不是请求值
    assert r["total"] == 20 and len(r["memories"]) == 20


def test_条数下限_limit非正_视作未传回落默认(env):
    f = env["factory"]
    for i in range(3):
        _seed(f, content=f"用户喜欢画水彩{i}", importance=50.0 + i)
    for bad in (0, -5):
        r = _api(f, query="水彩", limit=bad)
        assert r["limit"] == mem_api.LOOKUP_LIMIT_DEFAULT == 5
        assert r["total"] == 3 and len(r["memories"]) <= r["limit"]   # 3 条候选不足默认值


# ───────────────────────── 鉴权 / 可见范围 ─────────────────────────

def test_越权_指定他人角色_404(env):
    _seed(env["factory"], user_id=2, character_id=202, content="外人也在画水彩")
    assert _err(query="水彩", character_id=202).status_code == 404


def test_不存在的角色_404_与越权同观感(env):
    assert _err(query="水彩", character_id=999999).status_code == 404


def test_空查询_400(env):
    assert _err(query="   ").status_code == 400


def test_指定角色_只返回该角色(env):
    f = env["factory"]
    mine = _seed(f, character_id=101, content="我的水彩课")
    other = _seed(f, character_id=102, content="另一个角色的水彩课")
    r = _api(f, query="水彩", character_id=101)
    assert r["characters_searched"] == 1
    assert _ids(r) == [mine] and other not in _ids(r)


def test_不指定角色_跨本账号多角色合并(env):
    f = env["factory"]
    a = _seed(f, character_id=101, content="A 的水彩课")
    b = _seed(f, character_id=102, content="B 的水彩课")
    r = _api(f, query="水彩")
    assert r["characters_searched"] == 2
    assert sorted(_ids(r)) == sorted([a, b])
    assert {m["character_id"] for m in r["memories"]} == {101, 102}


def test_跨家庭_他人记忆一条都不外泄(env):
    f = env["factory"]
    mine = _seed(f, character_id=101, content="我画水彩")
    theirs = _seed(f, user_id=2, character_id=202, content="外人画水彩")
    r = _api(f, query="水彩", user_id=1)
    assert _ids(r) == [mine] and theirs not in _ids(r)
    assert "外人画水彩" not in str(r)
    r2 = _api(f, query="水彩", user_id=2)          # 反向：外人只看得到自己的
    assert _ids(r2) == [theirs] and r2["characters_searched"] == 1


# ───────────────────────── 复用既有检索链（不新建实现） ─────────────────────────

def test_复用检索链_按名次轮转合并且片段截断(env, monkeypatch):
    """端点必须原样调用 app.memory.retrieve.search_memories，跨角色只按「内部名次」轮转合并。"""
    calls: list[dict] = []

    def _row(i, content):
        return {"id": i, "content": content, "type": "event", "importance": 10.0, "created_at": T0}

    async def _fake_search(character_id=None, query=None, limit=None, user_id=None, **kw):
        calls.append({"character_id": character_id, "query": query, "limit": limit, "user_id": user_id})
        if character_id == 101:
            return [_row(1001, "甲" * 260), _row(1002, "甲的第二名")]   # 头名超长 ⇒ 截断
        return [_row(2001, "乙的头名")]

    monkeypatch.setattr("app.memory.retrieve.search_memories", _fake_search)
    f = env["factory"]
    _seed(f, character_id=101, content="占位A")   # 让枚举能扫到这两个角色
    _seed(f, character_id=102, content="占位B")

    r = _api(f, query="  用户最近画了什么  ", limit=7)
    # 每个可见角色各调一次（角色 id 升序、稳定可测），问句已 strip、limit 透传生效值
    assert [c["character_id"] for c in calls] == [101, 102]
    assert {c["query"] for c in calls} == {"用户最近画了什么"}
    assert {c["limit"] for c in calls} == {7}
    assert {c["user_id"] for c in calls} == {1}
    # 名次 0 先各进一条，再名次 1 ⇒ 1001 / 2001 / 1002（不是按重要度、也不是按角色分块）
    assert _ids(r) == [1001, 2001, 1002]
    assert r["memories"][0]["snippet"] == "甲" * mem_api.LOOKUP_SNIPPET_MAX
    assert r["memories"][0]["truncated"] is True
    assert r["memories"][1]["truncated"] is False


def test_检索链单角色故障_不整单失败(env, monkeypatch):
    """一个角色的检索抛异常 ⇒ 该角色出空，另一个角色的命中照常返回（不 500）。"""
    async def _flaky(character_id=None, **kw):
        if character_id == 101:
            raise RuntimeError("chroma down")
        return [{"id": 7788, "content": "还活着的水彩记忆", "type": "event",
                 "importance": 30.0, "created_at": T0}]

    monkeypatch.setattr("app.memory.retrieve.search_memories", _flaky)
    f = env["factory"]
    _seed(f, character_id=101, content="占位A")
    _seed(f, character_id=102, content="占位B")
    r = _api(f, query="水彩")
    assert _ids(r) == [7788]


# ───────────────────────── 只读 / 路由 ─────────────────────────

def test_只读_记忆与回执表逐字节不变(env):
    f = env["factory"]
    _seed(f, content="用户喜欢画水彩")

    async def _snapshot():
        async with f() as db:
            rows = (await db.execute(select(Memory).order_by(Memory.id))).scalars().all()
            recs = (await db.execute(select(MemoryWriteReceipt))).scalars().all()
            return ([(m.id, m.content, m.importance, m.is_archived, m.status, m.updated_at) for m in rows],
                    len(recs))

    before = asyncio.run(_snapshot())
    _api(f, query="水彩")
    _api(f, query="水彩", character_id=101, limit=3)
    assert asyncio.run(_snapshot()) == before


def test_路由注册为GET且先于路径参数路由(env):
    paths = [(r.path, getattr(r, "methods", None)) for r in mem_api.router.routes]
    hit = [p for p in paths if p[0] == "/api/v1/memories/lookup"]
    assert hit and "GET" in hit[0][1]
    order = [p[0] for p in paths]
    assert order.index("/api/v1/memories/lookup") < order.index("/api/v1/memories/{memory_id}")
