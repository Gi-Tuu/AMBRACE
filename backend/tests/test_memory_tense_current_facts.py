# -*- coding: utf-8 -*-
"""记忆时态缺陷族·第一批（2026-09-17）回归用例。

覆盖交接文件任务 1–3 的可验证结论：
- 任务1：位置/易变现状不再一律判「恒久」（三态 + 完成信号）；
- 任务1配套：stale/superseded/expired 行在 format_memory_line 必带［往事/已过时］前缀；
- 任务2：现状面（current_facts_status_clause）取不到 stale，怀旧面（_retrievable_status_clause）仍可取到；
  新 flag current_facts_active_only 默认开、关掉即回退旧行为；向量/BM25 的 status 参数；
- 任务3：moment/diary 等「天然已发生来源」含计划词 → episodic（不再误判 plan）。

用项目既有「临时 SQLite 文件库 + monkeypatch service.async_session_factory」夹具法，不触碰 backend/data。
"""
import asyncio

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.memory.format import format_memory_line
from app.memory.tense import classify_tense
from app.models.memory import Memory

# 重量级/集成型用例（每例起一次临时库），与既有记忆测试同档。
pytestmark = pytest.mark.slow


@pytest.fixture()
def tf_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库：只 monkeypatch app.memory.service.async_session_factory（隔离临时库）。"""
    import app.memory.service as memsvc

    engine = clone_engine(tmp_path / "tf.db")
    factory = make_session_factory(engine)

    async def _seed_parents():
        # _dbclone 默认开 FK（生产同款 PRAGMA）：memories 的 user_id / character_id 需父行先存在
        # （本文件账号口径：1；角色：66/67/77）
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="tense_u1", nickname="时态用户"))
            for cid in (66, 67, 77):
                db.add(AICharacter(id=cid, user_id=1, name=f"时态角色{cid}"))
            await db.commit()

    asyncio.run(_seed_parents())
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


async def _seed(factory, **kw):
    async with factory() as db:
        m = Memory(**kw)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m.id, m.status


def _loc(content: str, status: str = "active") -> dict:
    return {"memory_type": "user_info", "sub_type": "location", "title": "",
            "content": content, "status": status}


# ────────────────────────── 任务1：位置/易变现状三态 ──────────────────────────

def test_location_tense_three_states():
    """位置记忆：stale「从长沙回来」非 enduring；在途 transient；稳定态 enduring；完成信号 episodic。"""
    # 改造前基线：这两条都恒为 enduring（旧顺序被 user_info 粗判吞掉）
    assert classify_tense(_loc("用户和sam从长沙回来", "stale")) != "enduring"
    assert classify_tense(_loc("用户正在长沙出差", "active")) != "enduring"
    # 在途 = 瞬时现状（走 world_facts TTL，复习不主动提）
    assert classify_tense(_loc("用户正在长沙出差", "active")) == "transient"
    # 稳定态标记 → 恒久（常驻地/家乡/定居）
    assert classify_tense(_loc("用户常驻湛江", "active")) == "enduring"
    assert classify_tense(_loc("用户老家在湛江", "active")) == "enduring"
    # 明确完成信号 → 已发生的往事
    assert classify_tense(_loc("用户从长沙回来了", "active")) == "episodic"
    # 其余易变子类（trip/mood/current_state…）同口径
    assert classify_tense({"memory_type": "status", "sub_type": "trip", "content": "在去长沙路上"}) == "transient"
    assert classify_tense({"memory_type": "status", "sub_type": "mood", "content": "心情不错"}) == "transient"


# ────────────────────────── 任务3：天然已发生来源 ──────────────────────────

def test_happened_source_with_plan_words_is_episodic():
    """moment/diary/群聊共享/life_event/game_summary 含计划词 → episodic，而非 plan。"""
    plan_words = "明天打算出发去旅行，攻略都做好了"
    for mtype in ("moment", "diary", "life_event", "game_summary", "summary"):
        got = classify_tense({"memory_type": mtype, "sub_type": mtype,
                              "title": "", "content": plan_words, "status": "active"})
        assert got == "episodic", f"{mtype} 应判 episodic，实得 {got}"
    # sub_type 维度（memory_type 非 happened 时也拦）
    assert classify_tense({"memory_type": "event", "sub_type": "group_shared",
                           "content": "下周要去长沙出差"}) == "episodic"
    assert classify_tense({"memory_type": "event", "sub_type": "moment",
                           "content": "准备去露营"}) == "episodic"
    # 生产库实证（id=10405/10409/10413 家庭群同一条发言，sub_type 实为 "group"，含「旅行」→ 曾全判 plan）
    assert classify_tense({"memory_type": "event", "sub_type": "group",
                           "content": "顺便把长沙特产的事记你账上，欠我一场云旅行呢"}) == "episodic"
    # 洞察（朋友圈复盘）含计划词 → 往事（报告 §P2-1「朋友圈 insight/moment 大量误判」）
    assert classify_tense({"memory_type": "insight",
                           "content": "用户计划下周去长沙旅行"}) == "episodic"
    # 显式 sub_type=plan 仍在最前面拦截，不被本批覆盖
    assert classify_tense({"memory_type": "user_info", "sub_type": "plan",
                           "content": "明天去长沙"}) == "plan"


def test_format_memory_line_uses_happened_hint():
    """注入行对天然已发生来源统一打［往事］（含计划词也不再错标［计划］）。"""
    line = format_memory_line({"content": "明天打算出发去旅行", "memory_type": "diary"})
    assert "［往事］" in line and "［计划］" not in line


# ────────────────────────── 任务2：现状面 / 怀旧面拆口径 ──────────────────────────

def test_current_facts_clause_excludes_stale_and_retrievable_keeps_it(tf_db):
    """现状面恒 active（取不到 stale）；怀旧面（默认 memory_supersede 关=永真）仍可取到 stale。"""
    from app.memory.service import _retrievable_status_clause, current_facts_status_clause

    async def _main():
        async with tf_db() as db:
            db.add(Memory(user_id=1, character_id=66, memory_type="user_info", sub_type="location",
                          content="用户正在长沙出差", importance=40, status="active"))
            db.add(Memory(user_id=1, character_id=66, memory_type="user_info", sub_type="location",
                          content="用户和sam从长沙回来", importance=40, status="stale"))
            await db.commit()
        async with tf_db() as db:
            cur = (await db.execute(select(Memory).where(current_facts_status_clause()))).scalars().all()
            old = (await db.execute(select(Memory).where(_retrievable_status_clause()))).scalars().all()
        return {m.content for m in cur}, {m.content for m in old}

    cur, old = asyncio.run(_main())
    assert cur == {"用户正在长沙出差"}                                  # 现状面：stale 一律不取
    assert old == {"用户正在长沙出差", "用户和sam从长沙回来"}            # 怀旧面：stale 仍可见


def test_current_facts_flag_on_by_default_and_off_rolls_back(tf_db, monkeypatch):
    """current_facts_active_only 默认开=恒 active（含 superseded）；置 False=回退旧行为（永真）。"""
    from app.agent.loop import AGENT_FLAGS
    from app.memory.service import current_facts_status_clause

    async def _statuses():
        async with tf_db() as db:
            rows = (await db.execute(select(Memory).where(current_facts_status_clause()))).scalars().all()
        return sorted(m.status for m in rows)

    async def _seed_two():
        async with tf_db() as db:
            db.add(Memory(user_id=1, character_id=67, memory_type="user_info",
                          content="现行湛江", importance=40, status="active"))
            db.add(Memory(user_id=1, character_id=67, memory_type="user_info",
                          content="旧长沙", importance=40, status="superseded"))
            await db.commit()

    assert AGENT_FLAGS.get("current_facts_active_only") is True          # 默认开（已登记进 AGENT_FLAGS）
    asyncio.run(_seed_two())
    assert asyncio.run(_statuses()) == ["active"]                        # 开：仅现行
    monkeypatch.setitem(AGENT_FLAGS, "current_facts_active_only", False)
    assert asyncio.run(_statuses()) == ["active", "superseded"]          # 关：一键回退旧行为


def test_format_memory_line_forces_past_prefix_for_invalidated_rows():
    """stale/superseded/expired 行即使被怀旧面召回，也必须带［往事/已过时］前缀。"""
    from datetime import datetime
    for st in ("stale", "superseded", "expired"):
        line = format_memory_line({"content": "用户和sam从长沙回来",
                                   "created_at": datetime(2026, 8, 25), "status": st})
        assert "［往事/已过时］" in line, st
    # active 行不强制该前缀（稳定态结束仍按自身时态标签）
    ok = format_memory_line({"content": "用户常驻湛江", "created_at": datetime(2026, 8, 25),
                             "status": "active", "memory_type": "user_info", "sub_type": "location"})
    assert "［往事/已过时］" not in ok


def test_vector_char_where_status_param():
    """向量 where：现状面传 status='active'；怀旧面不传维持旧行为（active+stale / 裸角色过滤）。"""
    from app.db.vector_store import _char_where
    assert _char_where(5, False, "active") == {
        "$and": [{"character_id": 5}, {"status": {"$in": ["active"]}}],
    }
    assert _char_where(5, False) == {"character_id": 5}
    assert _char_where(5, True) == {
        "$and": [{"character_id": 5}, {"status": {"$in": ["active", "stale"]}}],
    }


def test_bm25_status_param_scopes_to_active(tf_db):
    """BM25 稀疏路：现状面传 status='active' 时不召回 stale；默认（怀旧面）两者都召回。"""
    import app.memory.bm25_index as bm25

    async def _main():
        active_id, _ = await _seed(tf_db, user_id=1, character_id=77, memory_type="event",
                                   content="用户喜欢画水彩", status="active", importance=40)
        stale_id, _ = await _seed(tf_db, user_id=1, character_id=77, memory_type="event",
                                  content="用户喜欢画油画", status="stale", importance=40)
        all_hits = await bm25.search(77, "画", top_k=10)
        cur_hits = await bm25.search(77, "画", top_k=10, status="active")
        return active_id, stale_id, {mid for mid, _ in all_hits}, {mid for mid, _ in cur_hits}

    active_id, stale_id, all_ids, cur_ids = asyncio.run(_main())
    assert {active_id, stale_id} <= all_ids      # 怀旧/默认面：stale 仍可召回
    assert active_id in cur_ids                  # 现状面：现行可见
    assert stale_id not in cur_ids               # 现状面：stale 不可见
