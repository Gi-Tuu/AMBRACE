# -*- coding: utf-8 -*-
"""B1-② 记忆链条建链器测试（2026-09-04，方案 §17）。

覆盖：建链（自建 root / 继承父链 / 时间窗 / 阈值 / 类型画像不建链 / 幂等 / 链长上限）、
沿链读取与扩展、反哺注入去重降权、RECALL_SHARED 捞链、flag 关零行为、/chain 接口契约。
参照 tests/test_memory_chain.py：临时 SQLite 文件库 + monkeypatch 各模块 async_session_factory，
不触碰 backend/data 与真实 Chroma（vector_store.search_memories 以假邻居注入）。
"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.db.vector_store as _vs
import app.memory.chain_builder as cb
from app.db import database as db_mod
from app.models.memory import Memory

_NOW = datetime.now(timezone.utc).replace(tzinfo=None)


async def _noop(*a, **k):
    return None


@pytest.fixture()
def cdb(monkeypatch):
    """临时 SQLite 文件库：把 chain_builder / api 归属校验 / vector_store 等都指向临时库。"""
    tmp = tempfile.mkdtemp(prefix="chain_builder_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(cb, "async_session_factory", factory)
    monkeypatch.setattr(db_mod, "async_session_factory", factory)  # API/_get_owned_memory 走临时库
    monkeypatch.setattr(_vs, "search_memories", _fake_search([]))  # 默认无近邻
    yield factory
    asyncio.run(engine.dispose())


def _fake_search(neighbors: list[dict]):
    """构造 vector_store.search_memories 假实现：返回给定邻居（dict 含 id/distance）。"""
    async def _search(character_id, query_embedding, limit=5):
        return neighbors
    return _search


async def _seed(factory, **kw):
    async with factory() as db:
        m = Memory(**kw)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m


async def _get(factory, mid):
    async with factory() as db:
        return await db.get(Memory, mid)


def _flag_on(monkeypatch, key, value=True):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, key, value)


# ---------------- 纯函数/常量 ----------------

def test_constants():
    """关键常量符合方案：只对 event/insight 建链、阈值 0.82、14 天窗、链长上限 12。"""
    assert cb.CHAINABLE_TYPES == {"event", "insight"}
    assert cb.PARENT_SIM_THRESHOLD == 0.82
    assert cb.CHAIN_WINDOW_DAYS == 14
    assert cb.MAX_CHAIN_NODES == 12


def test_parent_score():
    """父节点评分：裸相似度 + 同类型 +0.05 + 关系/情绪互挂 +0.03（纯函数）。"""
    assert cb.parent_score(0.90, "event", None, "event", None) == pytest.approx(0.95)
    assert cb.parent_score(0.90, "insight", None, "event", None) == pytest.approx(0.90)
    assert cb.parent_score(0.90, "event", "relationship", "event", "emotion") == pytest.approx(0.98)
    assert cb.parent_score(0.90, "event", "relationship", "event", "location") == pytest.approx(0.95)


def test_chainable():
    """是否可建链：event/insight 且未归档且非 stale/superseded；user_info/preference 不建链。"""
    assert cb._chainable(Memory(memory_type="event", is_archived=False, status="active"))
    assert cb._chainable(Memory(memory_type="insight", is_archived=False, status="active"))
    assert not cb._chainable(Memory(memory_type="user_info", is_archived=False, status="active"))
    assert not cb._chainable(Memory(memory_type="preference", is_archived=False, status="active"))
    assert not cb._chainable(Memory(memory_type="event", is_archived=True, status="active"))
    assert not cb._chainable(Memory(memory_type="event", is_archived=False, status="stale"))
    assert not cb._chainable(Memory(memory_type="event", is_archived=False, status="superseded"))


# ---------------- 建链行为 ----------------

def test_无近邻_自建root(cdb, monkeypatch):
    _flag_on(monkeypatch, "memory_chain_builder")

    async def _main():
        m = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                        content="一起去了趟海边", importance=60.0, sub_type="relationship")
        await cb.link_new_memory(m.id, [0.1, 0.2, 0.3])
        return await _get(cdb, m.id)

    row = asyncio.run(_main())
    assert row.chain_id is not None
    assert row.parent_id is None
    assert row.node_type == "root"


def test_继承父链_branch(cdb, monkeypatch):
    _flag_on(monkeypatch, "memory_chain_builder")

    async def _main():
        parent = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                             content="海边计划", importance=60.0, sub_type="relationship",
                             created_at=_NOW - timedelta(days=1), chain_id="c1", node_type="root")
        monkeypatch.setattr(_vs, "search_memories", _fake_search([{"id": parent.id, "distance": 0.10}]))
        new = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                          content="海边计划出发", importance=60.0, sub_type="relationship",
                          created_at=_NOW)
        await cb.link_new_memory(new.id, [0.2, 0.3, 0.4])
        return await _get(cdb, new.id), parent

    row, parent = asyncio.run(_main())
    assert row.chain_id == parent.chain_id == "c1"
    assert row.parent_id == parent.id
    assert row.node_type == "branch"


def test_近邻超14天_新开root(cdb, monkeypatch):
    _flag_on(monkeypatch, "memory_chain_builder")

    async def _main():
        parent = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                             content="陈年旧事", importance=60.0,
                             created_at=_NOW - timedelta(days=20), chain_id="old", node_type="root")
        monkeypatch.setattr(_vs, "search_memories", _fake_search([{"id": parent.id, "distance": 0.05}]))
        new = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                          content="近来一事", importance=60.0, created_at=_NOW)
        await cb.link_new_memory(new.id, [0.1, 0.2])
        return await _get(cdb, new.id)

    row = asyncio.run(_main())
    assert row.chain_id is not None
    assert row.chain_id != "old"
    assert row.parent_id is None
    assert row.node_type == "root"


def test_相似度低于阈值_不挂(cdb, monkeypatch):
    _flag_on(monkeypatch, "memory_chain_builder")

    async def _main():
        parent = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                             content="旅行回忆", importance=60.0,
                             created_at=_NOW - timedelta(days=1), chain_id="c9", node_type="root")
        # 距离 0.20 → sim=0.80 < 0.82：即使最相似也不挂
        monkeypatch.setattr(_vs, "search_memories", _fake_search([{"id": parent.id, "distance": 0.20}]))
        new = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                          content="另一位旅行回忆", importance=60.0, created_at=_NOW)
        await cb.link_new_memory(new.id, [0.1, 0.2])
        return await _get(cdb, new.id)

    row = asyncio.run(_main())
    assert row.chain_id is not None
    assert row.chain_id != "c9"
    assert row.parent_id is None
    assert row.node_type == "root"


def test_user_info不建链_and_幂等(cdb, monkeypatch):
    _flag_on(monkeypatch, "memory_chain_builder")

    async def _main():
        # user_info 不建链
        u = await _seed(cdb, user_id=1, character_id=1, memory_type="user_info",
                        content="用户喜欢美式", importance=50.0)
        await cb.link_new_memory(u.id, [0.1, 0.2])
        u2 = await _get(cdb, u.id)
        # 已挂链的 event 不重复挂（幂等）
        p = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                        content="已挂链", importance=60.0, chain_id="c-fixed", node_type="root")
        await cb.link_new_memory(p.id, [0.1, 0.2])
        p2 = await _get(cdb, p.id)
        return u2, p2

    u2, p2 = asyncio.run(_main())
    assert u2.chain_id is None and u2.parent_id is None
    assert p2.chain_id == "c-fixed" and p2.node_type == "root"


def test_链长上限12_另开root(cdb, monkeypatch):
    _flag_on(monkeypatch, "memory_chain_builder")

    async def _main():
        parent = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                             content="链主", importance=60.0, chain_id="c-long", node_type="root",
                             created_at=_NOW - timedelta(days=1))
        for i in range(11):  # 使链上共 12 个节点
            await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                        content=f"链节点{i}", importance=60.0, chain_id="c-long", node_type="branch",
                        created_at=_NOW - timedelta(days=1))
        monkeypatch.setattr(_vs, "search_memories", _fake_search([{"id": parent.id, "distance": 0.08}]))
        new = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                          content="新事件", importance=60.0, created_at=_NOW)
        await cb.link_new_memory(new.id, [0.1, 0.2])
        return await _get(cdb, new.id)

    row = asyncio.run(_main())
    assert row.parent_id is None
    assert row.node_type == "root"
    assert row.chain_id != "c-long"


def test_flag关_link_noop(cdb, monkeypatch):
    """memory_chain_builder 默认关：link_new_memory 不写任何链字段（零行为）。"""
    # 不开启 flag（默认 False）

    async def _main():
        m = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                        content="不该被挂链", importance=60.0)
        await cb.link_new_memory(m.id, [0.1, 0.2])
        return await _get(cdb, m.id)

    row = asyncio.run(_main())
    assert row.chain_id is None
    assert row.parent_id is None
    assert row.node_type is None


# ---------------- 沿链读取/扩展 ----------------

async def _make_chain(cdb):
    """造一条 c-1 链：root + 子1 + 子2（按时间升序）。返回 (root, c1, c2)。"""
    root = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                       content="一起看展", importance=60.0, chain_id="c-1", node_type="root",
                       created_at=_NOW - timedelta(days=5))
    c1 = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                     content="看展被作品打动", importance=60.0, chain_id="c-1", node_type="branch",
                     parent_id=root.id, created_at=_NOW - timedelta(days=4))
    c2 = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                     content="约好下次再去看展", importance=60.0, chain_id="c-1", node_type="branch",
                     parent_id=c1.id, created_at=_NOW - timedelta(days=3))
    return root, c1, c2


def test_get_chain_nodes_时间升序(cdb):
    async def _main():
        root, c1, c2 = await _make_chain(cdb)
        nodes = await cb.get_chain_nodes(c1.id)
        # 孤立点：返回仅自身
        solo = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                           content="孤点", importance=60.0)
        solo_nodes = await cb.get_chain_nodes(solo.id)
        return [n.id for n in nodes], [n.id for n in solo_nodes]

    ids, solo_ids = asyncio.run(_main())
    assert ids == sorted(ids)  # 时间升序
    assert len(ids) == 3
    assert solo_ids is not None and len(solo_ids) == 1


def test_expand_along_chain(cdb):
    async def _main():
        root, c1, c2 = await _make_chain(cdb)
        # 命中 c1：父=root + 最近子=c2
        ids = await cb.expand_along_chain(c1.id)
        # max_extra=1 只取父
        ids1 = await cb.expand_along_chain(c1.id, max_extra=1)
        return ids, ids1, root.id, c2.id

    ids, ids1, root_id, c2_id = asyncio.run(_main())
    assert set(ids) == {root_id, c2_id}
    assert len(ids1) == 1 and ids1[0] == root_id


def test_maybe_expand_chain_flag关_原样(cdb, monkeypatch):
    """memory_chain_expand 默认关：maybe_expand_chain 原样返回（零行为）。"""
    picked = [{"id": 1, "content": "a", "ttype": "x"}]

    async def _main():
        return await cb.maybe_expand_chain(1, picked)

    out = asyncio.run(_main())
    assert out == picked  # 新列表、内容相等
    assert out is not picked  # 不污染入参引用


def test_maybe_expand_chain_flag开_扩充降权(cdb, monkeypatch):
    """memory_chain_expand 开：沿链补 ≤2 相邻节点、去重、降权 0.9、内容前缀「（同一件事）」。"""
    _flag_on(monkeypatch, "memory_chain_expand")

    async def _main():
        root, c1, c2 = await _make_chain(cdb)
        picked = [{"id": c1.id, "content": "看展被作品打动", "type": "event",
                   "importance": 60.0, "created_at": c1.created_at, "epistemic_status": "FACT"}]
        # 命中 c1：expand_along_chain 返回 [root, c2]，均未在 picked 中
        out = await cb.maybe_expand_chain(1, picked)
        extra = [m for m in out if m["id"] != c1.id]
        return out, extra

    out, extra = asyncio.run(_main())
    assert len(out) == 3
    assert len(extra) == 2
    for m in extra:
        assert m["content"].startswith("（同一件事）")
        assert m["importance"] == 60.0 * cb.CHAIN_EXPAND_DOWNWEIGHT


def test_maybe_expand_chain_不重复自身(cdb, monkeypatch):
    """扩展节点与已命中节点去重：已命中不重复追加。"""
    _flag_on(monkeypatch, "memory_chain_expand")

    async def _main():
        root, c1, c2 = await _make_chain(cdb)
        picked = [{"id": root.id, "content": "一起看展", "type": "event",
                   "importance": 60.0, "created_at": root.created_at, "epistemic_status": "FACT"}]
        out = await cb.maybe_expand_chain(1, picked)
        return out, root.id

    out, root_id = asyncio.run(_main())
    ids = [m["id"] for m in out]
    assert ids.count(root_id) == 1  # 自身不重复


# ---------------- RECALL_SHARED 捞链 ----------------

def test_pick_recall_chain_优先关系链_窗口过滤(cdb):
    async def _main():
        # 关系链 c-rel（3-60 天内，≥2 节点）应被优先选中
        r = await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                        content="一起旅行", importance=60.0, sub_type="relationship",
                        chain_id="c-rel", node_type="root", created_at=_NOW - timedelta(days=10))
        await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                    content="旅途趣事", importance=60.0, sub_type="relationship",
                    chain_id="c-rel", node_type="branch", parent_id=r.id,
                    created_at=_NOW - timedelta(days=9))
        # 太近（<3 天）的链不应被捞（留给正常承接）
        await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                    content="刚发生", importance=60.0, chain_id="c-fresh", node_type="root",
                    created_at=_NOW - timedelta(days=1))
        await _seed(cdb, user_id=1, character_id=1, memory_type="event",
                    content="刚发生2", importance=50.0, chain_id="c-fresh", node_type="branch",
                    created_at=_NOW - timedelta(days=1))
        # 无关系链时退而取任一链
        txt = await cb.pick_recall_chain(1)
        return txt

    txt = asyncio.run(_main())
    assert txt is not None
    assert "一起旅行" in txt  # 有起承的链时间线


def test_pick_recall_chain_无链_None(cdb):
    async def _main():
        return await cb.pick_recall_chain(1)

    assert asyncio.run(_main()) is None


# ---------------- API 契约 ----------------

def test_chain_api_返回时间线_404(cdb, monkeypatch):
    from fastapi import HTTPException

    from app.api.memories import get_memory_chain

    async def _main():
        root, c1, c2 = await _make_chain(cdb)
        resp = await get_memory_chain(memory_id=c1.id, user_id=1, lang="zh")
        # 非本人 404
        try:
            await get_memory_chain(memory_id=c1.id, user_id=999, lang="zh")
            not_found = None
        except HTTPException as ei:
            not_found = ei.status_code
        return resp, not_found

    resp, not_found = asyncio.run(_main())
    assert resp["status"] == "ok"
    chain = resp["chain"]
    assert {x["id"] for x in chain} == {x["id"] for x in chain}  # 时间升序字段存在
    assert len(chain) == 3
    current = [x for x in chain if x["is_current"]]
    assert len(current) == 1
    assert not_found == 404
