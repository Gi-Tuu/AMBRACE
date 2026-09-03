# -*- coding: utf-8 -*-
"""§20 跨角色用户事实层：user_facts + cross_char_sync + [USER NOW] 分区测试（2026-09-04）。

覆盖：
- classify_slot：可变槽归槽 / 一次性事件不误归；
- upsert_user_fact：单值槽、previous_value 记录、(user_id, slot) 唯一、同值幂等；
- stale_character_slot_memory：旧值文本命中 → stale；新值/insight 不误伤；双通道向量 no-op；
- align / sweep：跨角色对齐幂等、覆盖不活跃角色；
- build_user_now_text / [USER NOW] 分区：flag 关→空、flag 开→注入。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.memory import Memory
from app.models.user import GlobalUserFact, User


@pytest.fixture()
def uf_db(monkeypatch):
    """临时库：create_all 全模型 + 把 user_facts / cross_char_sync 的异步工厂指向临时工厂。"""
    tmp = tempfile.mkdtemp(prefix="user_facts_")
    engine = create_async_engine(f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    import app.memory.user_facts as uf
    import app.memory.cross_char_sync as ccs
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(uf, "async_session_factory", factory)
    monkeypatch.setattr(ccs, "async_session_factory", factory)

    async def _noop(*a, **k):
        return None
    import app.db.vector_store as vs
    monkeypatch.setattr(vs, "mark_memory_vector_status", _noop)
    yield factory
    asyncio.run(engine.dispose())


def _seed_user(factory, user_id: int = 1):
    async def _run():
        async with factory() as db:
            u = User(id=user_id, username=f"tester{user_id}", nickname="测试")
            db.add(u)
            await db.commit()
    asyncio.run(_run())


def _seed_char(factory, name: str, user_id: int = 1):
    from app.models.character import AICharacter
    async def _run():
        async with factory() as db:
            c = AICharacter(user_id=user_id, name=name)
            db.add(c)
            await db.commit()
            await db.refresh(c)
            return c.id
    return asyncio.run(_run())


def _seed_memory(factory, *, character_id, content, memory_type="user_info", sub_type=None, status="active"):
    async def _run():
        async with factory() as db:
            m = Memory(user_id=1, character_id=character_id, memory_type=memory_type,
                       content=content, sub_type=sub_type, importance=40, status=status)
            db.add(m)
            await db.commit()
            await db.refresh(m)
            return m.id
    return asyncio.run(_run())


# ── classify_slot ──

def test_classify_slot():
    from app.memory.user_facts import classify_slot
    assert classify_slot("我从长沙回湛江了") == "location"
    assert classify_slot("我正在准备考研") == "goal_state"
    assert classify_slot("我离职了") == "job"
    assert classify_slot("用户今天去公园散步") is None  # 一次性事件不误归
    assert classify_slot("我们聊了会天") is None
    assert classify_slot("") is None
    assert classify_slot(None) is None


def test_classify_slot_location_regex_tightened():
    """F-4：location 归槽正则收紧（宁紧勿松）。

    - 纯趋向词「回到/回来/回了」须带地点宾语；「回到正题/我回来了」不再误归；
    - 否定/非地点宾语黑名单：正题/话题/问题/从前/以前/过去/状态/心情/梦里/记忆；
    - 「从…(到|回)」要求右端是地点词，避免「从失败里走出来」误命中；
    - 同时补不回归的旧正例（城市名/搬回/公司等已明确的地点宾语仍归槽）。
    """
    from app.memory.user_facts import classify_slot
    # 反例（此前会误归槽）
    assert classify_slot("回到正题") is None
    assert classify_slot("我们回到话题") is None
    assert classify_slot("我回来了") is None
    assert classify_slot("回到过去") is None
    assert classify_slot("从失败里走出来") is None
    # 正例（地点宾语明确）
    assert classify_slot("我回到东莞了") == "location"
    assert classify_slot("我从长沙回湛江了") == "location"
    assert classify_slot("用户这个月搬回了老家") == "location"
    assert classify_slot("我回到了四川老家") == "location"


# ── upsert_user_fact ──

def test_upsert_user_fact_records_previous(uf_db):
    from app.memory.user_facts import upsert_user_fact, get_active_user_facts
    factory = uf_db
    _seed_user(factory, 1)
    change = asyncio.run(upsert_user_fact(1, "location", "长沙", source="gps"))
    assert change == (None, "长沙")
    # 同值幂等
    assert asyncio.run(upsert_user_fact(1, "location", "长沙", source="gps")) is None
    # 改值 → 记录 previous_value
    change2 = asyncio.run(upsert_user_fact(1, "location", "湛江", source="chat"))
    assert change2 == ("长沙", "湛江")
    rows = asyncio.run(get_active_user_facts(1))
    assert len(rows) == 1
    assert rows[0].value == "湛江"
    assert rows[0].previous_value == "长沙"
    assert rows[0].source == "chat"
    # 多槽互不影响
    asyncio.run(upsert_user_fact(1, "job", "程序员"))
    assert len(asyncio.run(get_active_user_facts(1))) == 2


def test_upsert_user_fact_unique_slot(uf_db):
    from app.memory.user_facts import upsert_user_fact, get_active_user_facts
    _seed_user(uf_db, 1)
    asyncio.run(upsert_user_fact(1, "location", "a"))
    asyncio.run(upsert_user_fact(1, "location", "b"))
    rows = asyncio.run(get_active_user_facts(1))
    assert len(rows) == 1
    assert rows[0].value == "b"
    assert rows[0].previous_value == "a"


def test_upsert_user_fact_refreshes_valid_from(uf_db):
    """F-2：同槽二次 upsert 后 valid_from 前进（当前值生效起点，与"更新于"文案一致）。

    更新分支此前未刷 valid_from，导致 build_user_now_text 的"更新于"停留在首次建档日。
    断言用严格 `>`：若未刷新（bug 存在），二次值的时间仍等于首次建档 → 用例失败。
    """
    from app.memory.user_facts import upsert_user_fact, get_active_user_facts
    _seed_user(uf_db, 1)
    asyncio.run(upsert_user_fact(1, "location", "长沙", source="gps"))
    first = asyncio.run(get_active_user_facts(1))[0]
    vf_first = first.valid_from
    assert vf_first is not None
    # 第二次 upsert（改值）→ valid_from 必须前进（严格晚于首次建档）
    asyncio.run(upsert_user_fact(1, "location", "湛江", source="chat"))
    second = asyncio.run(get_active_user_facts(1))[0]
    assert second.value == "湛江"
    assert second.valid_from is not None
    assert second.valid_from > vf_first


def test_upsert_user_fact_empty_value_no_op(uf_db):
    from app.memory.user_facts import upsert_user_fact, get_active_user_facts
    _seed_user(uf_db, 1)
    assert asyncio.run(upsert_user_fact(1, "location", "")) is None
    assert asyncio.run(upsert_user_fact(1, "", "x")) is None
    assert asyncio.run(get_active_user_facts(1)) == []


# ── stale_character_slot_memory ──

def test_stale_character_slot_memory(uf_db):
    from app.memory.cross_char_sync import stale_character_slot_memory
    factory = uf_db
    _seed_user(factory, 1)
    oid = _seed_memory(factory, character_id=100, content="用户住在长沙", sub_type="location")
    nid = _seed_memory(factory, character_id=100, content="用户住在湛江", sub_type="location")
    iid = _seed_memory(factory, character_id=100, content="我觉得用户很坚强", memory_type="insight", sub_type="relationship")
    n = asyncio.run(stale_character_slot_memory(100, "location", "长沙"))

    async def _check():
        async with factory() as db:
            old = await db.get(Memory, oid)
            new = await db.get(Memory, nid)
            ins = await db.get(Memory, iid)
            return old.status, new.status, ins.status
    # 只命中旧值（内容含「长沙」）；新值与 insight 不误伤
    assert n == 1
    assert asyncio.run(_check()) == ("stale", "active", "active")


def test_stale_character_slot_memory_matches_extracted_history(uf_db):
    from app.memory.cross_char_sync import stale_character_slot_memory
    factory = uf_db
    _seed_user(factory, 1)
    # 历史自由文本（sub_type=extracted）含旧城市名可被命中
    eid = _seed_memory(factory, character_id=7, content="用户生活在长沙", sub_type="extracted")
    n = asyncio.run(stale_character_slot_memory(7, "location", "长沙"))
    assert n == 1
    async def _st():
        async with factory() as db:
            return (await db.get(Memory, eid)).status
    assert asyncio.run(_st()) == "stale"


# ── align / sweep ──

def test_align_character_idempotent(uf_db):
    from app.memory.user_facts import upsert_user_fact
    from app.memory.cross_char_sync import align_character_to_user_facts
    factory = uf_db
    _seed_user(factory, 1)
    asyncio.run(upsert_user_fact(1, "location", "长沙", source="gps"))
    asyncio.run(upsert_user_fact(1, "location", "湛江", source="gps"))  # previous_value=长沙
    mid = _seed_memory(factory, character_id=5, content="用户住在长沙", sub_type="location")
    rep1 = asyncio.run(align_character_to_user_facts(5, 1))
    assert rep1.get("location") == 1
    async def _st():
        async with factory() as db:
            return (await db.get(Memory, mid)).status
    assert asyncio.run(_st()) == "stale"
    # 幂等：已 stale 的不再命中 → 0
    rep2 = asyncio.run(align_character_to_user_facts(5, 1))
    assert rep2.get("location") == 0


def test_sweep_all_characters_alignment(uf_db):
    from app.memory.user_facts import upsert_user_fact
    from app.memory.cross_char_sync import sweep_all_characters_alignment
    factory = uf_db
    _seed_user(factory, 1)
    c1 = _seed_char(factory, "A")
    _seed_char(factory, "B")
    asyncio.run(upsert_user_fact(1, "location", "长沙", source="gps"))
    asyncio.run(upsert_user_fact(1, "location", "湛江", source="gps"))
    mid = _seed_memory(factory, character_id=c1, content="用户住在长沙", sub_type="location")
    n = asyncio.run(sweep_all_characters_alignment(1))
    assert n == 2  # 处理全部角色
    async def _st():
        async with factory() as db:
            return (await db.get(Memory, mid)).status
    assert asyncio.run(_st()) == "stale"


# ── build_user_now_text / [USER NOW] ──

def test_build_user_now_text(uf_db):
    from app.memory.user_facts import upsert_user_fact, build_user_now_text
    _seed_user(uf_db, 1)
    assert asyncio.run(build_user_now_text(1)) == "无"
    asyncio.run(upsert_user_fact(1, "location", "湛江", source="gps"))
    text = asyncio.run(build_user_now_text(1))
    assert "位置/城市" in text
    assert "湛江" in text
    assert "更新于" in text


def test_user_now_section_flag_gate(uf_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS as _af
    from app.memory.user_facts import upsert_user_fact, build_user_now_text
    from app.agent.context.section_user_now import user_now_section
    _seed_user(uf_db, 1)
    # flag 关 → 空（零行为变化）；即使已有事实
    asyncio.run(upsert_user_fact(1, "location", "湛江"))
    monkeypatch.setitem(_af, "global_user_facts", False)
    assert asyncio.run(user_now_section({"user_id": 1}, {})) == []
    # flag 开 → 注入一个 system 块，含权威标题与事实
    monkeypatch.setitem(_af, "global_user_facts", True)
    blocks = asyncio.run(user_now_section({"user_id": 1}, {}))
    assert len(blocks) == 1
    assert "【用户最新状态" in blocks[0]
    assert "湛江" in blocks[0]
