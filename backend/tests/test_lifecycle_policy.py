# -*- coding: utf-8 -*-
"""A4 批 1（T3）P0-P2：事实生命周期策略表（纯函数）+ 干跑观测（只读）。

纪律（对齐《批 1 设计草案 v1》）：
- P0/P1/P2 **零行为**：本批只加载策略表、只算不作用、只记 INFO；不筛选、不改状态、不写库；
- 真库用例走 _dbclone 私有临时库（页级克隆），不连生产库；用例前后对全表做快照比对；
- 开关 fact_lifecycle_policy 默认关，关时不跑扫描（逐字节旧行为）。
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop
from app.memory import lifecycle_policy as pol
from app.memory.constants import S_BY_TYPE
from app.memory.maintenance_schedule import scan_lifecycle_policy
from app.utils.timeutil import now_naive_utc


# ── P0：策略表完整性 ──


def test_every_kind_has_policy_and_route_and_decay_are_valid():
    """每个 fact_kind 都有策略；decay_profile 走既有衰减档；recall_route 在枚举内。"""
    assert pol.all_kinds(), "策略表不能为空"
    for kind in pol.all_kinds():
        p = pol.policy_for(kind)
        assert p is not None
        assert p.decay_profile == pol.DECAY_INHERIT or p.decay_profile in S_BY_TYPE
        assert p.recall_route in pol.ROUTES
        if p.ttl_days is not None:
            assert isinstance(p.ttl_days, int) and p.ttl_days > 0


def test_all_user_fact_slots_are_registered():
    """user_facts 的每个可变槽都必须在策略表里有 user_attr.<slot> 条目。"""
    from app.memory.user_facts import MUTABLE_SLOTS

    missing = [s for s in MUTABLE_SLOTS if "user_attr." + s not in pol.POLICY]
    assert not missing, "策略表缺槽：%s" % missing


def test_slot_ttl_matches_existing_constants_and_covers_all_slots():
    """槽 TTL 与既有 VOLATILE_FACT_TTL_DAYS 一致；缺口径者显式 None。"""
    from app.memory.user_facts import MUTABLE_SLOTS, VOLATILE_FACT_TTL_DAYS

    for slot, days in VOLATILE_FACT_TTL_DAYS.items():
        assert pol.slot_ttl_days(slot) == days
    for slot in MUTABLE_SLOTS:
        assert pol.slot_ttl_days(slot) == VOLATILE_FACT_TTL_DAYS.get(slot)


def test_only_slot_kinds_carry_supersede_key():
    """批 1 只开放「用户属性槽」参与同槽取代：非槽类一律 supersede_key is None。"""
    for kind, p in pol.POLICY.items():
        if kind.startswith("user_attr."):
            assert p.supersede_key and p.supersede_key.startswith("user.")
        else:
            assert p.supersede_key is None


# ── P0：类型映射 ──


def test_resolve_fact_kind_slot_and_fallback():
    assert pol.resolve_fact_kind(slot="location") == "user_attr.location"
    assert pol.resolve_fact_kind(slot="not_a_slot") == "transient"
    assert pol.resolve_fact_kind(slot="location", memory_type="event") == "user_attr.location"


def test_resolve_fact_kind_from_memory_metadata():
    """复用读取侧现算口径；认不出的一律落在已登记 kind 内。"""
    assert pol.resolve_fact_kind(memory_type="event", sub_type="plan") == "plan"
    assert pol.resolve_fact_kind(memory_type="user_info", is_core=True) == "identity"
    assert pol.resolve_fact_kind(memory_type="preference") == "preference"
    for kwargs in ({"memory_type": "insight"}, {"memory_type": "event", "text": "用户上周去了学校"},
                   {"memory_type": "user_info", "text": "用户喜欢吃辣"}, {}):
        assert pol.resolve_fact_kind(**kwargs) in pol.POLICY


# ── P0：失效判定 ──


def test_is_expired_priority_and_boundaries():
    now = datetime(2026, 9, 26, 12, 0, 0)
    assert pol.is_expired("identity", superseded=True, now=now) is True
    assert pol.is_expired("plan", valid_to=now, now=now) is True
    assert pol.is_expired("plan", valid_to=now + timedelta(days=1), now=now) is False
    assert pol.is_expired("user_attr.location", created_at=now - timedelta(days=29), now=now) is False
    assert pol.is_expired("user_attr.location", created_at=now - timedelta(days=30), now=now) is True
    assert pol.is_expired("user_attr.relationship", created_at=now - timedelta(days=9999), now=now) is False
    assert pol.is_expired("user_attr.location", created_at=None, now=now) is False
    assert pol.is_expired("user_attr.location", created_at=now, now=None) is False
    assert pol.is_expired("no_such_kind", created_at=now, now=now) is False


# ── P0：同槽取代候选（纯函数；P3 才接线） ──


def test_normalize_and_same_value_are_conservative():
    assert pol.normalize_value(" 北京，") == pol.normalize_value("北京")
    assert pol.same_value("北京", "北京") is True
    assert pol.same_value("北京", "在北京") is True
    assert pol.same_value("北京", "长沙") is False
    assert pol.same_value("", "北京") is False


def test_plan_slot_replacements_split_and_conservative_rules():
    """按「槽值」口径分流：值不同 ⇒ 取代候选；值同/判不出/空 ⇒ 强化（保守，不取代）。"""
    plan = pol.plan_slot_replacements([(11, "长沙"), (12, "北京")], "北京市")
    assert plan["supersede"] == [11]
    assert plan["reinforce"] == [12]                                  # 「北京」⊂「北京市」
    assert pol.plan_slot_replacements([(13, "")], "北京市") == {"supersede": [], "reinforce": [13]}
    assert pol.plan_slot_replacements([(11, "长沙")], "  ") == {"supersede": [], "reinforce": [11]}
    assert pol.plan_slot_replacements([], "北京") == {"supersede": [], "reinforce": []}


def test_same_value_scope_is_slot_values_not_sentences():
    """口径钉死：句子级文本不保证判同（P3 只拿短槽值进来比），避免有人误用到正文上。"""
    assert pol.same_value("北京", "去北京了") is True                 # 短值与短语仍可判同
    assert pol.same_value("用户在长沙", "我现在在北京") is False      # 整句形态不判同 ⇒ 走「值不同」侧
    assert "槽值" in pol.same_value.__doc__


def test_observation_line_skips_zero_counts():
    line = pol.observation_line(sampled=3, by_kind={"identity": 2, "plan": 0}, expired={"identity": 1})
    assert "sampled=3" in line and "identity=2" in line and "plan=" not in line
    assert "expired[identity=1]" in line


def test_table_snapshot_is_read_only_copy():
    snap = pol.table_snapshot()
    assert len(snap) == len(pol.POLICY)
    snap[0]["ttl_days"] = 99999
    assert pol.table_snapshot()[0]["ttl_days"] != 99999


# ── 开关与目录 ──


def test_flag_default_off_and_catalog_entry_present():
    assert loop.AGENT_FLAGS.get(pol.FLAG_KEY) is False, "新开关必须默认关"
    from app.application.flag_catalog import FLAG_CATALOG

    entry = FLAG_CATALOG.get(pol.FLAG_KEY)
    assert entry is not None, "新开关必须在目录里登记（否则会红）"
    assert entry["visible"] is False
    assert entry["title_zh"].strip() and entry["title_en"].strip()


# ── P1+P2：干跑扫描（只读） ──


@pytest.fixture
def mem_db(tmp_path):
    """私有记忆库（user 1 + char 1），返回会话工厂。"""
    engine = clone_engine(os.path.join(str(tmp_path), "b1_memories.db"))
    factory = make_session_factory(engine)
    _sessions: list = []

    def _make(*args, **kwargs):
        s = factory(*args, **kwargs)
        _sessions.append(s)
        return s

    async def _init():
        from app.models.character import AICharacter
        from app.models.user import User
        async with _make() as db:
            db.add(User(id=1, username="u1", nickname="用户"))
            db.add(AICharacter(id=1, user_id=1, name="轩", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    yield _make

    async def _teardown():
        for s in _sessions:
            try:
                await s.close()
            except Exception:
                pass
        await engine.dispose()

    asyncio.run(_teardown())


def _add(factory, *, mid, mtype, content, created_at=None, status="active", valid_to=None,
         is_core=False):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            db.add(Memory(id=mid, user_id=1, character_id=1, memory_type=mtype,
                          content=content, status=status, scope="private", is_core=is_core,
                          created_at=created_at or now_naive_utc(), valid_to=valid_to))
            await db.commit()

    asyncio.run(_run())


def _snapshot(factory):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            rows = (await db.execute(
                select(Memory.id, Memory.status, Memory.memory_type, Memory.content,
                       Memory.updated_at, Memory.valid_to, Memory.superseded_by)
                .order_by(Memory.id)
            )).fetchall()
            return [tuple(r) for r in rows]

    return asyncio.run(_run())


def test_scan_skipped_when_flag_off(mem_db):
    """开关关（默认）⇒ 扫描直接返回 None（连查询都不做）。"""
    loop.AGENT_FLAGS[pol.FLAG_KEY] = False
    _add(mem_db, mid=101, mtype="user_info", content="用户在长沙")
    assert asyncio.run(scan_lifecycle_policy(session_factory=mem_db)) is None


def test_scan_counts_kinds_and_expired_when_on(mem_db):
    """开关开 ⇒ 返回分布与「按 TTL/valid_to 判失效」条数（只读）。"""
    loop.AGENT_FLAGS[pol.FLAG_KEY] = True
    now = now_naive_utc()
    _add(mem_db, mid=201, mtype="event", content="用户上周去了学校",
         created_at=now - timedelta(days=3))
    _add(mem_db, mid=202, mtype="plan", content="用户明天交材料",
         created_at=now - timedelta(days=1), valid_to=now - timedelta(hours=1))
    out = asyncio.run(scan_lifecycle_policy(session_factory=mem_db, now=now))
    assert out is not None and out["sampled"] == 2
    assert sum(out["by_kind"].values()) == 2
    assert sum(out["expired"].values()) == 1
    assert "sampled=2" in out["line"]


def test_scan_is_read_only_and_ignores_non_active(mem_db):
    """扫描前后全表逐列一致；非 active（已取代）不参与抽样。"""
    loop.AGENT_FLAGS[pol.FLAG_KEY] = True
    _add(mem_db, mid=301, mtype="user_info", content="用户在长沙")
    _add(mem_db, mid=302, mtype="user_info", content="用户在北京", status="superseded")
    before = _snapshot(mem_db)
    out = asyncio.run(scan_lifecycle_policy(session_factory=mem_db))
    assert out is not None and out["sampled"] == 1
    assert _snapshot(mem_db) == before


def test_scan_failure_is_isolated_and_logged(mem_db, caplog):
    """异常隔离：查询炸了也不抛（不影响维护拍子），返回 None 并记 WARNING。"""
    loop.AGENT_FLAGS[pol.FLAG_KEY] = True

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    with caplog.at_level(logging.WARNING, logger="memory.maintenance"):
        assert asyncio.run(scan_lifecycle_policy(session_factory=_boom)) is None
    assert any("Lifecycle policy scan failed" in r.message for r in caplog.records)


def test_scan_logs_info_line(mem_db, caplog):
    """命中抽样时打一条 INFO（含 sampled 与分布）。"""
    loop.AGENT_FLAGS[pol.FLAG_KEY] = True
    _add(mem_db, mid=401, mtype="preference", content="用户喜欢吃辣")
    with caplog.at_level(logging.INFO, logger="memory.maintenance"):
        asyncio.run(scan_lifecycle_policy(session_factory=mem_db))
    lines = [r.getMessage() for r in caplog.records if "Lifecycle policy dry-run" in r.message]
    assert len(lines) == 1 and "sampled=1" in lines[0]


# ── P3 配套：槽层「旧值镜像」口径与只读观测 ──


def test_slot_memory_stale_plan_matches_existing_anchor_rule():
    """与三层承接者同口径：old_value 前 6 字文本锚点；空锚点 ⇒ 一律不标。"""
    rows = [(1, "用户住在示例城"), (2, "用户住在示例市"), (3, "我觉得用户很坚强")]
    plan = pol.plan_slot_memory_stale(rows, "示例城")
    assert plan["stale"] == [1]
    assert sorted(plan["keep"]) == [2, 3]
    assert pol.plan_slot_memory_stale(rows, "   ") == {"stale": [], "keep": [1, 2, 3]}
    assert pol.plan_slot_memory_stale([], "示例城") == {"stale": [], "keep": []}
    assert pol.SLOT_MEMORY_STALE_ANCHOR_CHARS == 6
    assert pol.SLOT_MEMORY_STALE_MEMORY_TYPE == "user_info"
    assert pol.SLOT_MEMORY_STALE_STATUS == "stale"


def test_slot_memory_stale_implementors_are_registered():
    """三层承接者写进策略表（单一事实源）：写时 / 激活时 / 每日。"""
    assert len(pol.SLOT_MEMORY_STALE_IMPLEMENTORS) == 3
    joined = " ".join(pol.SLOT_MEMORY_STALE_IMPLEMENTORS)
    for name in ("stale_character_slot_memory", "align_character_to_user_facts",
                 "sweep_all_characters_alignment"):
        assert name in joined


def test_slot_layer_observation_line():
    line = pol.slot_layer_observation_line(facts=7, by_slot={"location": 2, "job": 1},
                                           with_prev=5, expired=0, stale_candidates=3)
    for frag in ("facts=7", "location=2", "with_prev=5", "stale_candidates=3"):
        assert frag in line


@pytest.mark.slow
def test_scan_reports_slot_layer_and_stale_candidates(mem_db):
    """干跑第二段：user_facts 现状 + 「旧值镜像」候选（只读；insight 不误伤）。"""
    from app.models.user import GlobalUserFact

    loop.AGENT_FLAGS[pol.FLAG_KEY] = True

    async def _seed():
        async with mem_db() as db:
            db.add(GlobalUserFact(user_id=1, slot="location", value="示例市",
                                  previous_value="示例城", source="chat", confidence=1.0))
            await db.commit()

    asyncio.run(_seed())
    _add(mem_db, mid=501, mtype="user_info", content="用户住在示例城")
    _add(mem_db, mid=502, mtype="insight", content="用户住在示例城")
    before = _snapshot(mem_db)
    out = asyncio.run(scan_lifecycle_policy(session_factory=mem_db))
    assert out is not None
    assert out["slots"]["facts"] == 1 and out["slots"]["with_prev"] == 1
    assert out["slots"]["stale_candidates"] == 1        # 只 user_info 命中
    assert "stale_candidates=1" in out["line"]
    assert _snapshot(mem_db) == before                  # 只读：记忆表逐列不变
