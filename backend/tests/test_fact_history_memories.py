# -*- coding: utf-8 -*-
"""批 0-6 · 事实「修正历史」只读端点测试（零新表，复用既有取代链 + 回执表）。

覆盖：
- 单条修正：旧值/新值/时间/来源取自取代链，字段齐；
- 同一事实两次修正 → 时间序正确（seq 1 旧 → seq 2 新，changed_at 升序）；
- 真实写入侧产物可读：走 supersede_memory 生成的链，端点能读出（本单不改写入语义）；
- 无前身 → corrections 为空数组（200 不报错）；
- 越权 / 不存在 → 404；前身行跨租户 → 不进序列（不外泄）；
- 原因来自回执：命中回执给 reason，无回执 reason=null（不编造）；valid_to 缺失时兜回执时间；
- actor 恒为 null（取代链与回执都不记操作者）；
- 内容改写（version+1）旧正文无留痕 → 只给次数、不臆造版本条目；
- 深度上限 truncated、防环不死循环；只读不写库；路由注册。

纪律：临时库走 tests/_dbclone（不连生产库）；项目未装 pytest-asyncio，统一 asyncio.run 同步执行。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.api import memories as mem_api
from app.db import database as db_mod
from app.models.memory import Memory, MemoryWriteReceipt

pytestmark = pytest.mark.slow

T0 = datetime(2026, 9, 20, 10, 0, 0)  # naive UTC（与库里存储口径一致）


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时库 + 会话工厂 late binding patch（api 经 db_mod 取工厂）。"""
    engine = clone_engine(tmp_path / "fact_hist.db")
    factory = make_session_factory(engine)

    async def _seed_parents():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="fh_u1", nickname="历史用户"))
            db.add(User(id=2, username="fh_u2", nickname="外人用户"))
            db.add(AICharacter(id=101, user_id=1, name="历史角色"))
            db.add(AICharacter(id=202, user_id=2, name="外人角色"))
            await db.commit()

    asyncio.run(_seed_parents())
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr("app.memory.supersede.async_session_factory", factory)
    yield {"factory": factory}
    asyncio.run(engine.dispose())


def _seed(factory, *, user_id=1, character_id=101, content="用户在长沙",
          status="active", superseded_by=None, valid_to=None, created_at=T0,
          version=0, source="chat", **kw):
    """按取代链字段直接落行（读层只看这些列，构造链路无需走写入侧）。"""
    async def _run():
        async with factory() as db:
            m = Memory(user_id=user_id, character_id=character_id, memory_type="user_info",
                       content=content, status=status, superseded_by=superseded_by,
                       valid_to=valid_to, created_at=created_at, version=version,
                       source=source, importance=50.0, **kw)
            db.add(m)
            await db.commit()
            return m.id
    return asyncio.run(_run())


def _seed_receipt(factory, memory_id, *, reason="用户明确改口", action="supersede",
                  created_at=T0, character_id=101):
    async def _run():
        async with factory() as db:
            db.add(MemoryWriteReceipt(character_id=character_id, memory_id=memory_id,
                                      action=action, reason=reason,
                                      detail_json="{}", created_at=created_at))
            await db.commit()
    return asyncio.run(_run())


def _hist(factory, memory_id, scope_ids):
    """直读层（便于按 scope 造越权场景）。"""
    from app.memory.facts import build_fact_history

    async def _run():
        async with factory() as db:
            return await build_fact_history(db, memory_id, scope_ids)
    return asyncio.run(_run())


def _api(factory, memory_id, *, user_id=1, lang="zh"):
    return asyncio.run(mem_api.get_memory_fact_history(memory_id=memory_id, user_id=user_id, lang=lang))


def _err(factory, memory_id, *, user_id=1):
    with pytest.raises(HTTPException) as ei:
        _api(factory, memory_id, user_id=user_id)
    return ei.value


def _chain(env, texts=("用户在长沙", "用户在湛江", "用户回长沙了")):
    """造一条 n 版取代链：版 i 被版 i+1 取代，末版 active；返回 (ids, 各次修正时间)。"""
    f = env["factory"]
    times = [T0 + timedelta(days=i + 1) for i in range(len(texts) - 1)]
    ids = [_seed(f, content=text, created_at=T0) for text in texts]
    for i, old_id in enumerate(ids[:-1]):
        async def _link(oid=old_id, nid=ids[i + 1], t=times[i]):
            async with f() as db:
                row = (await db.execute(select(Memory).where(Memory.id == oid))).scalar_one()
                row.status, row.superseded_by, row.valid_to = "superseded", nid, t
                await db.commit()
        asyncio.run(_link())
    return ids, times


# ───────────────────────── 主体：取代链 → 修正序列 ─────────────────────────

def test_单条修正_旧值新值时间来源齐全(env):
    ids, times = _chain(env, ("用户在长沙", "用户在湛江"))
    r = _api(env["factory"], ids[1])
    assert r["status"] == "ok" and r["memory_id"] == ids[1]
    assert r["fact"]["id"] == ids[1] and r["fact"]["content"] == "用户在湛江"
    assert r["fact"]["status"] == "active"
    assert len(r["corrections"]) == 1
    c = r["corrections"][0]
    assert (c["old_id"], c["new_id"]) == (ids[0], ids[1])
    assert (c["old_value"], c["new_value"]) == ("用户在长沙", "用户在湛江")
    assert c["changed_at"] == times[0].isoformat()
    assert c["changed_at_from"] == "valid_to"
    assert (c["old_source"], c["new_source"]) == ("chat", "chat")
    assert c["seq"] == 1


def test_同一事实两次修正_时间序正确(env):
    """硬验收：三版链（长沙→湛江→回长沙）按时间升序，seq 递增，中间值两头都出现。"""
    ids, times = _chain(env, ("用户在长沙", "用户在湛江", "用户回长沙了"))
    r = _api(env["factory"], ids[2])
    corr = r["corrections"]
    assert [c["seq"] for c in corr] == [1, 2]
    assert [c["changed_at"] for c in corr] == sorted(c["changed_at"] for c in corr)
    assert corr[0]["changed_at"] == times[0].isoformat()
    assert corr[1]["changed_at"] == times[1].isoformat()
    assert (corr[0]["old_value"], corr[0]["new_value"]) == ("用户在长沙", "用户在湛江")
    assert (corr[1]["old_value"], corr[1]["new_value"]) == ("用户在湛江", "用户回长沙了")
    assert corr[1]["old_id"] == ids[1] and corr[1]["new_id"] == ids[2]
    assert r["truncated"] is False


def test_真实取代链可读_走写入侧入口(env, monkeypatch):
    """读取层要能读出 supersede_memory 的真实产物（本单一行写入逻辑都不动）。"""
    from app.agent.loop import AGENT_FLAGS
    from app.db import vector_store as vs
    from app.memory import bm25_index as bm25
    from app.memory.supersede import supersede_memory

    # 双通道副作用隔离：不打真实 ChromaDB / BM25（读层用例不需要向量面）
    class _FakeCol:
        def get(self, *a, **k):
            return {"ids": [], "embeddings": [], "metadatas": [], "documents": []}

        def update(self, *a, **k):
            return None

        def upsert(self, *a, **k):
            return None

        def delete(self, *a, **k):
            return None
    fake = _FakeCol()

    async def _get_col(*a, **k):
        return fake
    monkeypatch.setattr(vs, "get_or_create_collection", _get_col)
    monkeypatch.setattr(bm25, "invalidate", lambda *a, **k: None)
    monkeypatch.setitem(AGENT_FLAGS, "memory_write_receipt", False)

    f = env["factory"]
    old = _seed(f, content="用户在长沙")
    new = _seed(f, content="用户在湛江")
    assert asyncio.run(supersede_memory(old, new_id=new, reason="用户改口")) is True
    r = _api(f, new)
    assert len(r["corrections"]) == 1
    c = r["corrections"][0]
    assert (c["old_id"], c["new_value"]) == (old, "用户在湛江")
    assert c["changed_at"]  # valid_to 由写入侧置入，读层直接拿到
    assert c["reason"] is None  # 回执 flag 关 → 无留痕，不编造原因


def test_无前身_空数组不报错(env):
    mid = _seed(env["factory"], content="用户不吃香菜")
    r = _api(env["factory"], mid)
    assert r["corrections"] == [] and r["truncated"] is False
    assert r["fact"]["id"] == mid


def test_多条前身汇聚_按时间升序(env):
    """一个新版可能同时吃掉多条旧版（淘汰/合并），序列按时间排而非按链层排。"""
    f = env["factory"]
    new = _seed(f, content="合并后的权威值")
    a = _seed(f, content="旧值A", status="superseded", superseded_by=new, valid_to=T0 + timedelta(hours=5))
    b = _seed(f, content="旧值B", status="superseded", superseded_by=new, valid_to=T0 + timedelta(hours=1))
    corr = _api(f, new)["corrections"]
    assert [c["old_value"] for c in corr] == ["旧值B", "旧值A"]
    assert [c["old_id"] for c in corr] == [b, a]


# ───────────────────────── 鉴权与租户边界 ─────────────────────────

def test_越权_404(env):
    ids, _ = _chain(env)
    err = _err(env["factory"], ids[1], user_id=2)
    assert err.status_code == 404


def test_不存在的id_404(env):
    assert _err(env["factory"], 999999).status_code == 404


def test_前身行跨租户_不进序列不外泄(env):
    """数据异常（跨家庭指向）时读层不得把外人旧值带出来。"""
    f = env["factory"]
    mine = _seed(f, content="我的当前值")
    theirs = _seed(f, user_id=2, character_id=202, content="外人的旧值",
                   status="superseded", superseded_by=mine)
    r = _api(f, mine)
    assert r["corrections"] == []
    assert theirs and "外人的旧值" not in str(r)
    assert len(_hist(f, mine, [1, 2])["corrections"]) == 1  # 白名单放开才可见（口径确实是 scope 驱动）


# ───────────────────────── 原因 / 不编造 ─────────────────────────

def test_回执命中_给出原因(env):
    f = env["factory"]
    new = _seed(f, content="用户在湛江")
    old = _seed(f, content="用户在长沙", status="superseded", superseded_by=new, valid_to=T0)
    _seed_receipt(f, old, reason="用户明确改口：搬城市")
    c = _api(f, new)["corrections"][0]
    assert c["reason"] == "用户明确改口：搬城市"


def test_无回执_原因为null不编造(env):
    f = env["factory"]
    new = _seed(f, content="用户在湛江")
    old = _seed(f, content="用户在长沙", status="superseded", superseded_by=new, valid_to=T0)
    _seed_receipt(f, old, reason="别的动作不该串用", action="merge")
    assert _api(f, new)["corrections"][0]["reason"] is None


def test_valid_to缺失_兜回执时间并标注出处(env):
    """旧行没写 valid_to（异常数据）时不猜时间：退到回执 created_at 并显式标出处。"""
    f = env["factory"]
    new = _seed(f, content="用户在湛江")
    old = _seed(f, content="用户在长沙", status="superseded", superseded_by=new, valid_to=None)
    t = T0 + timedelta(days=3)
    _seed_receipt(f, old, created_at=t)
    c = _api(f, new)["corrections"][0]
    assert c["changed_at"] == t.isoformat()
    assert c["changed_at_from"] == "receipt_created_at"


def test_时间无法确定_changed_at为null不编造(env):
    f = env["factory"]
    new = _seed(f, content="用户在湛江")
    _seed(f, content="用户在长沙", status="superseded", superseded_by=new, valid_to=None)
    c = _api(f, new)["corrections"][0]
    assert c["changed_at"] is None and c["changed_at_from"] is None


def test_actor恒为null(env):
    """取代链与回执表都不记操作者（回执无 user_id 列）→ 明确 null，不臆造「谁改的」。"""
    f = env["factory"]
    new = _seed(f, content="用户在湛江")
    old = _seed(f, content="用户在长沙", status="superseded", superseded_by=new,
                valid_to=T0, speaker_type="user")
    _seed_receipt(f, old)
    corr = _api(f, new)["corrections"]
    assert [c["actor"] for c in corr] == [None]
    assert corr[0]["reason"] == "用户明确改口"


def test_内容改写只给次数不进序列(env):
    """PATCH content 使 version+1 但旧正文被原地覆盖（无留痕）→ 只报次数，不编造版本条目。"""
    mid = _seed(env["factory"], content="改过两次的内容", version=2)
    r = _api(env["factory"], mid)
    assert r["fact"]["version"] == 2
    assert r["corrections"] == []


# ───────────────────────── 边界：深度上限 / 防环 / 只读 ─────────────────────────

def test_深度上限_truncated(env, monkeypatch):
    monkeypatch.setattr("app.memory.facts.FACT_HISTORY_MAX_DEPTH", 3)
    ids, _ = _chain(env, ("v1", "v2", "v3", "v4", "v5", "v6"))
    r = _api(env["factory"], ids[5])
    assert r["truncated"] is True
    assert len(r["corrections"]) == 3
    assert [c["new_id"] for c in r["corrections"]][-1] == ids[5]  # 当前值一侧恒在结果内


def test_防环不死循环(env):
    """异常数据（互为取代）不得把只读查询变成死循环。"""
    f = env["factory"]
    a = _seed(f, content="环上A")
    b = _seed(f, content="环上B")
    async def _link():
        async with f() as db:
            ra = (await db.execute(select(Memory).where(Memory.id == a))).scalar_one()
            rb = (await db.execute(select(Memory).where(Memory.id == b))).scalar_one()
            ra.status, ra.superseded_by = "superseded", b
            rb.status, rb.superseded_by = "superseded", a
            await db.commit()
    asyncio.run(_link())
    corr = _api(f, a)["corrections"]
    assert len(corr) == 1 and corr[0]["old_id"] == b


def test_只读不写库(env):
    """跑一遍端点不得新增/改动任何行（含回执表）。"""
    ids, _ = _chain(env)
    f = env["factory"]

    async def _counts():
        async with f() as db:
            return (len((await db.execute(select(Memory))).scalars().all()),
                    len((await db.execute(select(MemoryWriteReceipt))).scalars().all()))
    before = asyncio.run(_counts())
    _api(f, ids[2])
    _api(f, ids[0])
    assert asyncio.run(_counts()) == before


def test_路由注册为GET(env):
    paths = [(r.path, getattr(r, "methods", None)) for r in mem_api.router.routes]
    hit = [p for p in paths if p[0] == "/api/v1/memories/{memory_id}/history"]
    assert hit and "GET" in hit[0][1]
