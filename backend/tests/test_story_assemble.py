# -*- coding: utf-8 -*-
"""Ariadne 模块 C：沿链半故事化组装单测（纯函数 + section 空实现退化等价 + F-1 端到端成块）。"""
import asyncio
import os
import tempfile
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.memory.story_assemble import assemble_story_lines, get_chain_index_for_hits


def _m(id_, content, created_at, chain_id=None):
    return {"id": id_, "content": content, "created_at": created_at, "chain_id": chain_id}


@pytest.fixture()
def sa_db(monkeypatch):
    """临时库：create_all 全模型 + 把 story_assemble 的异步工厂指向临时工厂。

    F-1 端到端：get_chain_index_for_hits 经 ``app.db.database.async_session_factory`` 取链，
    故 patch 该接缝（函数内延迟 import 取到 patch 值），与既有 user_facts/chain_builder 测试一致。
    """
    tmp = tempfile.mkdtemp(prefix="story_assemble_")
    engine = create_async_engine(f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _seed_user_and_char(factory):
    from app.models.user import User
    from app.models.character import AICharacter

    async def _run():
        async with factory() as db:
            u = User(id=1, username="tester", nickname="测试")
            c = AICharacter(user_id=1, name="角色A")
            db.add_all([u, c])
            await db.commit()
            await db.refresh(c)
            return c.id
    return asyncio.run(_run())


def _seed_chain(factory, *, character_id, chain_id, nodes):
    """造一条链。nodes 为 [(content, created_at, memory_type, status), ...]，返回 [(id, ...), ...]。"""
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            objs = []
            for content, created_at, mtype, status in nodes:
                objs.append(Memory(
                    user_id=1, character_id=character_id, memory_type=mtype,
                    content=content, importance=60, created_at=created_at,
                    chain_id=chain_id, status=status,
                ))
            db.add_all(objs)
            await db.commit()
            for o in objs:
                await db.refresh(o)
            return [o.id for o in objs]
    return asyncio.run(_run())


def _flag_on(monkeypatch, **extra):
    from app.agent import loop
    monkeypatch.setattr(loop, "AGENT_FLAGS", {**loop.AGENT_FLAGS, **extra})



def test_两节点成链_时间升序_前缀与连接词():
    chain = [
        _m(1, "第一次一起去看海", "2025-07-01 10:00:00", "c1"),
        _m(2, "后来又去了青岛吃烧烤", "2025-07-20 18:00:00", "c1"),
    ]
    out = assemble_story_lines([_m(2, "后来又去了青岛吃烧烤", "2025-07-20 18:00:00", "c1")],
                               {2: chain})
    assert len(out) == 2
    assert out[0].startswith("┌ [2025-07-01]") and "第一次一起去看海" in out[0]
    assert out[1].startswith("└") and any(w in out[1] for w in ("之后", "后来", "紧接着"))
    assert "2025-07-20" in out[1]


def test_同链成员不重复平铺():
    c1 = _m(1, "事件甲", "2025-01-01", "c1")
    c2 = _m(2, "事件乙", "2025-02-01", "c1")
    idx = {1: [c1, c2], 2: [c1, c2]}
    out = assemble_story_lines([c1, c2], idx)
    assert len(out) == 2  # 一条链只成块一次
    assert sum(1 for ln in out if "事件甲" in ln) == 1
    assert sum(1 for ln in out if "事件乙" in ln) == 1


def test_孤立点走单行_链成员混排():
    chain = [_m(1, "链上节点", "2025-01-01", "c1"), _m(2, "链上后继", "2025-03-01", "c1")]
    orphan = _m(9, "孤立记忆点", "2025-06-01")
    out = assemble_story_lines([orphan, _m(1, "链上节点", "2025-01-01", "c1")], {1: chain})
    assert out[0].startswith("- [2025-06-01] 孤立记忆点")
    assert out[1].startswith("┌ [2025-01-01] 链上节点")


def test_单节点链不成块_内容截断():
    single = _m(1, "长" * 200, "2025-01-01", "c1")
    out = assemble_story_lines([single], {1: [single]})
    # len(chain)==1 不满足 ≥2 → 孤立点路径，150 字截断
    assert out[0].startswith("- [2025-01-01]")
    assert len(out[0]) < len("- [2025-01-01] ") + 160


def test_链内截断120():
    chain = [_m(1, "长" * 200, "2025-01-01", "c1"), _m(2, "尾节点", "2025-02-01", "c1")]
    out = assemble_story_lines([chain[0]], {1: chain})
    assert len(out[0]) < len("┌ [2025-01-01] ") + 130


def test_空index_全部孤立路径():
    m1 = _m(1, "内容一", "2025-01-01")
    out = assemble_story_lines([m1], {})
    assert out == ["- [2025-01-01] 内容一"]


def test_get_chain_index_for_hits_flag关_等价空退化(sa_db, monkeypatch):
    """F-1：建链器 flag（memory_chain_builder）关 → 返回 {}，section 走原路径逐字节等价退化。

    即便临时库里存在真实链数据，flag 关也一律返回 {}（不读库，零行为变化）。
    """
    char_id = _seed_user_and_char(sa_db)
    n1, n2 = _seed_chain(
        sa_db, character_id=char_id, chain_id="c1",
        nodes=[
            ("第一次一起去看海", datetime(2025, 7, 1, 10, 0, 0), "event", "active"),
            ("后来又去了青岛吃烧烤", datetime(2025, 7, 20, 18, 0, 0), "event", "active"),
        ],
    )
    assert asyncio.run(get_chain_index_for_hits([n2])) == {}


def test_get_chain_index_for_hits_建链器开_命中在链_返回索引且每链不超过4(sa_db, monkeypatch):
    """F-1 端到端：建链器 flag 开 + 2 节点链 + 命中其一 → index 非空，且每链 ≤4。

    索引含自身按时间升序；节点 dict 必须带 chain_id（成块判据）。
    """
    char_id = _seed_user_and_char(sa_db)
    n1, n2 = _seed_chain(
        sa_db, character_id=char_id, chain_id="c1",
        nodes=[
            ("第一次一起去看海", datetime(2025, 7, 1, 10, 0, 0), "event", "active"),
            ("后来又去了青岛吃烧烤", datetime(2025, 7, 20, 18, 0, 0), "event", "active"),
        ],
    )
    _flag_on(monkeypatch, memory_chain_builder=True)
    idx = asyncio.run(get_chain_index_for_hits([n2]))
    assert idx, "命中在链节点应返回非空索引"
    assert n2 in idx
    chain = idx[n2]
    assert len(chain) == 2
    assert len(chain) <= 4  # 每链 ≤4（PER_CHAIN 硬裁剪）
    assert [c["id"] for c in chain] == [n1, n2]  # 含自身、时间升序
    assert all(c["chain_id"] == "c1" for c in chain)
    assert all("content" in c and "created_at" in c for c in chain)


def test_get_chain_index_for_hits_建链器开_单链超过4只取4(sa_db, monkeypatch):
    """F-1：每链节点数超 4 时硬裁剪为前 4（时间升序），不把整链灌爆上下文。"""
    char_id = _seed_user_and_char(sa_db)
    nodes = [(f"节点{i}", datetime(2025, 7, i, 10, 0, 0), "event", "active") for i in range(1, 6)]
    ids = _seed_chain(sa_db, character_id=char_id, chain_id="c1", nodes=nodes)
    _flag_on(monkeypatch, memory_chain_builder=True)
    idx = asyncio.run(get_chain_index_for_hits([ids[-1]]))
    assert len(idx[ids[-1]]) == 4


def test_get_chain_index_for_hits_命中非链节点_无索引(sa_db, monkeypatch):
    """F-1：只取 chain_id IS NOT NULL 的节点——无链（chain_id=NULL）命中返回 {}。"""
    char_id = _seed_user_and_char(sa_db)
    (orphan_id,) = _seed_chain(
        sa_db, character_id=char_id, chain_id=None,
        nodes=[("孤立记忆点", datetime(2025, 6, 1, 10, 0, 0), "event", "active")],
    )
    _flag_on(monkeypatch, memory_chain_builder=True)
    assert asyncio.run(get_chain_index_for_hits([orphan_id])) == {}


def test_section_双开_沿链成块_含块前缀(sa_db, monkeypatch):
    """F-1 端到端（section 层）：memory_story_assemble + memory_chain_builder 双开 →
    检索结果带 chain_id（index 反查补齐），assemble_story_lines 产出含 ┌/└ 的成块前缀。

    造 2 节点链、命中后一节点，输出应按时间升序合成一个小块（┌ 首节点 + └ 连接词后继）。
    """
    from app.agent.context import section_memories as sm

    char_id = _seed_user_and_char(sa_db)
    n1, n2 = _seed_chain(
        sa_db, character_id=char_id, chain_id="c1",
        nodes=[
            ("我们第一次去看海", datetime(2025, 7, 1, 10, 0, 0), "event", "active"),
            ("后来又去了青岛吃烧烤", datetime(2025, 7, 20, 18, 0, 0), "event", "active"),
        ],
    )
    monkeypatch.setattr(sm, "_mark_memories_injected", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_filter_recently_injected", lambda cid, r: list(r))
    _flag_on(monkeypatch, memory_story_assemble=True, memory_chain_builder=True)
    retrieved = [{"id": n2, "content": "后来又去了青岛吃烧烤", "created_at": "2025-07-20 18:00:00",
                  "importance": 60.0, "memory_type": "event"}]
    out = asyncio.run(
        sm.memories_section({"character_id": char_id, "retrieved_memories": retrieved}, {})
    ).split("\n")
    assert any(l.startswith("┌") for l in out), out
    assert any(l.startswith("└") for l in out), out
    assert out[0].startswith("┌ [2025-07-01]") and "我们第一次去看海" in out[0]
    assert "2025-07-20" in out[1]


def test_section_flag关_与旧路径逐字节一致(sa_db, monkeypatch):
    """F-1：建链器落地后，flag 关仍走原路径（format_memory_line 单行），与旧文件行为逐字节一致。"""
    from app.agent.context import section_memories as sm

    char_id = _seed_user_and_char(sa_db)
    n1, n2 = _seed_chain(
        sa_db, character_id=char_id, chain_id="c1",
        nodes=[
            ("我们第一次去看海", datetime(2025, 7, 1, 10, 0, 0), "event", "active"),
            ("后来又去了青岛吃烧烤", datetime(2025, 7, 20, 18, 0, 0), "event", "active"),
        ],
    )
    monkeypatch.setattr(sm, "_mark_memories_injected", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_filter_recently_injected", lambda cid, r: list(r))
    _flag_on(monkeypatch, memory_story_assemble=False)  # 关 → 走旧路径（即使库里已有真实链）
    retrieved = [{"id": n2, "content": "后来又去了青岛吃烧烤", "created_at": "2025-07-20 18:00:00",
                  "importance": 60.0, "memory_type": "event"}]
    out = asyncio.run(
        sm.memories_section({"character_id": char_id, "retrieved_memories": retrieved}, {})
    )
    assert out == "- [记录于 2025-07-20] 后来又去了青岛吃烧烤"
    assert "┌" not in out and "└" not in out


def test_section_空实现退化_与flag关逐字节等价(monkeypatch):
    """flag 开但 chain_index 为空（建链器未落地）→ memories_section 输出与 flag 关完全一致。"""
    from app.agent import loop
    from app.agent.context import section_memories as sm

    retrieved = [{"id": 1, "content": "记忆甲", "created_at": "2025-01-01 10:00:00",
                  "importance": 60.0, "memory_type": "event"}]
    monkeypatch.setattr(sm, "_mark_memories_injected", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_filter_recently_injected", lambda cid, r: list(r))

    async def _empty_index(ids):
        return {}

    monkeypatch.setattr("app.memory.story_assemble.get_chain_index_for_hits", _empty_index)

    state = {"character_id": 1, "retrieved_memories": retrieved}

    monkeypatch.setattr(loop, "AGENT_FLAGS", {**loop.AGENT_FLAGS, "memory_story_assemble": False})
    off = asyncio.run(sm.memories_section(state, {}))
    monkeypatch.setattr(loop, "AGENT_FLAGS", {**loop.AGENT_FLAGS, "memory_story_assemble": True})
    on_empty = asyncio.run(sm.memories_section(state, {}))
    assert off == on_empty and "记忆甲" in off
