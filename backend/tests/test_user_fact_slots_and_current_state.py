# -*- coding: utf-8 -*-
"""篇 B（2026-09-10）：细粒度事实槽 + C2 新鲜窗/回家收敛 + C3 现状锚点三通道。

覆盖：
- 6 槽 flag 默认全关（含 location）/ 只开 location / 总闸开=全开；
- C2-③ 回家识别（纯函数正反例）+ 位置收敛（DB，含计划语气/非回家/无城市不写）；
- C2-② WorldFact 瞬时谓词新鲜窗（location 72h / mood 12h / status 12h；activity 不受影响）
  与 C2-① 同 predicate 只留最新（含 location/mood）；
- C3 现状锚点：User 已授权城市 / GlobalUserFact 仅启用槽 / 未授权三通道皆空 /
  section 不注入空块 / 复习通道与公共函数等价。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时库用 pytest tmp_path。）
"""
import asyncio
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.memory import WorldFact
from app.models.user import User
from app.utils.timeutil import now_naive_utc

_SLOT_FLAGS = (
    "user_fact_location", "user_fact_job", "user_fact_relationship",
    "user_fact_living", "user_fact_goal_state", "user_fact_health",
)


# 快测档（2026-09-12）：本文件是重量级/集成型用例（每例起一次临时库，约 3s/例），打 slow 标记。
# 全量默认照跑；日常开发用 pytest -m "not slow" 跳过本档（见 docs/engineering-protocol.md 十八）。
pytestmark = pytest.mark.slow

@pytest.fixture()
def b_db(monkeypatch, tmp_path):
    """临时库：建全模型 + 把相关模块的异步工厂指向临时工厂。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.join(str(tmp_path), 'b.db')}", poolclass=NullPool)
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
    import app.events.facts as facts
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(uf, "async_session_factory", factory)
    monkeypatch.setattr(ccs, "async_session_factory", factory)
    monkeypatch.setattr(facts, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _set_flags(monkeypatch, **kw):
    """显式设置 AGENT_FLAGS（未指定的槽 flag 一律置 False，保证用例确定性）。"""
    from app.agent.loop import AGENT_FLAGS as _af
    monkeypatch.setitem(_af, "global_user_facts", bool(kw.get("global_user_facts", False)))
    for k in _SLOT_FLAGS:
        monkeypatch.setitem(_af, k, bool(kw.get(k, False)))


def _seed_user(factory, *, user_id=1, location_enabled=False, user_location=None, location_city=None):
    async def _run():
        async with factory() as db:
            u = User(id=user_id, username=f"tester{user_id}", nickname="测试",
                     location_enabled=location_enabled,
                     user_location=user_location, location_city=location_city)
            db.add(u)
            await db.commit()
    asyncio.run(_run())


def _seed_world_fact(factory, *, char_id, user_id, predicate, value, hours_ago=0,
                     subject="user", ttl_hours=None):
    async def _run():
        from datetime import timedelta as _td
        now = now_naive_utc()
        async with factory() as db:
            db.add(WorldFact(
                user_id=user_id, character_id=char_id,
                subject_type=subject, subject_id=(user_id if subject == "user" else char_id),
                predicate=predicate, object_value=value,
                status="active", audience='["public"]', epistemic_status="FACT",
                asserted_at=now - _td(hours=hours_ago),
                expires_at=(now + _td(hours=ttl_hours)) if ttl_hours is not None else None,
            ))
            await db.commit()
    asyncio.run(_run())


# ── B2 细粒度 flag ──────────────────────────────────────────────────────

def test_default_all_slots_disabled(monkeypatch):
    from app.memory.user_facts import MUTABLE_SLOTS, enabled_user_fact_slots, user_fact_slot_enabled
    _set_flags(monkeypatch)
    assert enabled_user_fact_slots() == []
    assert all(not user_fact_slot_enabled(s) for s in MUTABLE_SLOTS)


def test_only_location_slot(monkeypatch):
    from app.memory.user_facts import enabled_user_fact_slots, user_fact_slot_enabled
    _set_flags(monkeypatch, user_fact_location=True)
    assert enabled_user_fact_slots() == ["location"]
    assert user_fact_slot_enabled("location") is True
    assert user_fact_slot_enabled("job") is False


def test_master_gate_enables_all(monkeypatch):
    from app.memory.user_facts import MUTABLE_SLOTS, enabled_user_fact_slots
    _set_flags(monkeypatch, global_user_facts=True)
    assert set(enabled_user_fact_slots()) == set(MUTABLE_SLOTS.keys())


def test_slot_flag_hot_switch_affects_read(monkeypatch, b_db):
    """细粒度：job 槽关时即便库里有 job 事实，默认读取也拿不到（只取启用槽）。"""
    from app.memory.user_facts import upsert_user_fact, get_active_user_facts
    factory = b_db
    _seed_user(factory)
    asyncio.run(upsert_user_fact(1, "job", "程序员"))
    _set_flags(monkeypatch, user_fact_location=True)
    assert asyncio.run(get_active_user_facts(1)) == []  # job 槽未启用 → 不返回
    _set_flags(monkeypatch, user_fact_location=True, user_fact_job=True)
    rows = asyncio.run(get_active_user_facts(1))
    assert [r.slot for r in rows] == ["job"]


# ── C2-③ 回家识别（纯函数）──────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "我到家了", "终于回到家了", "到家啦", "我已经回到家里", "返程回到家", "回了家",
])
def test_detect_home_return_positive(text):
    from app.memory.user_facts import detect_home_return
    assert detect_home_return(text) is True


@pytest.mark.parametrize("text", [
    "我想明天回家", "打算周末回家", "我回长沙了", "回到学校了", "回公司加班",
    "我回老家看看", "回家乡发展", "等我回家再说", "要是能回家就好了", "今天天气不错",
    "回了趟家又走了",  # 歧义（已离开）：保守不命中
])
def test_detect_home_return_negative(text):
    from app.memory.user_facts import detect_home_return
    assert detect_home_return(text) is False


def test_settle_location_on_home_return(monkeypatch, b_db):
    """开 location 槽：出行城市「长沙」→ 回家收敛到常驻城市「广州」，旧值进 previous_value。"""
    from app.memory.user_facts import (
        upsert_user_fact, get_active_user_facts, settle_location_on_home_return,
    )
    factory = b_db
    _seed_user(factory, user_location="广州")
    asyncio.run(upsert_user_fact(1, "location", "长沙", source="chat"))
    _set_flags(monkeypatch, user_fact_location=True)
    assert asyncio.run(settle_location_on_home_return(1, "我到家了")) is True
    row = asyncio.run(get_active_user_facts(1, slots=["location"]))[0]
    assert row.value == "广州"
    assert row.previous_value == "长沙"
    assert row.source == "chat_home_return"


def test_settle_location_plan_tense_and_elsewhere_no_op(monkeypatch, b_db):
    from app.memory.user_facts import (
        upsert_user_fact, get_active_user_facts, settle_location_on_home_return,
    )
    factory = b_db
    _seed_user(factory, user_location="广州")
    asyncio.run(upsert_user_fact(1, "location", "长沙", source="chat"))
    _set_flags(monkeypatch, user_fact_location=True)
    assert asyncio.run(settle_location_on_home_return(1, "我想明天回家")) is False
    assert asyncio.run(settle_location_on_home_return(1, "我回长沙了")) is False
    row = asyncio.run(get_active_user_facts(1, slots=["location"]))[0]
    assert row.value == "长沙"  # 未收敛


def test_settle_location_disabled_slot_no_op(monkeypatch, b_db):
    from app.memory.user_facts import upsert_user_fact, get_active_user_facts, settle_location_on_home_return
    factory = b_db
    _seed_user(factory, user_location="广州")
    asyncio.run(upsert_user_fact(1, "location", "长沙", source="chat"))
    _set_flags(monkeypatch)  # 全关
    assert asyncio.run(settle_location_on_home_return(1, "我到家了")) is False
    assert asyncio.run(get_active_user_facts(1, slots=["location"]))[0].value == "长沙"


def test_settle_location_without_home_city_no_write(monkeypatch, b_db):
    """常驻城市取不到（user_location 空）→ 不臆造城市，返回 False。"""
    from app.memory.user_facts import upsert_user_fact, get_active_user_facts, settle_location_on_home_return
    factory = b_db
    _seed_user(factory, user_location=None)
    asyncio.run(upsert_user_fact(1, "location", "长沙", source="chat"))
    _set_flags(monkeypatch, user_fact_location=True)
    assert asyncio.run(settle_location_on_home_return(1, "我到家了")) is False
    assert asyncio.run(get_active_user_facts(1, slots=["location"]))[0].value == "长沙"


# ── C2-② 新鲜窗 / C2-① 矛盾去重 ────────────────────────────────────────

def test_location_freshness_window_72h(b_db):
    from app.events.facts import get_active_facts
    factory = b_db
    _seed_user(factory)
    _seed_world_fact(factory, char_id=1, user_id=1, predicate="location", value="广州", hours_ago=71)
    _seed_world_fact(factory, char_id=2, user_id=1, predicate="location", value="长沙", hours_ago=73)
    fresh = asyncio.run(get_active_facts(character_id=1, user_id=1))
    stale = asyncio.run(get_active_facts(character_id=2, user_id=1))
    assert [f.object_value for f in fresh] == ["广州"]   # 71h：在窗内
    assert stale == []                                    # 73h：超窗不注入


def test_mood_and_status_windows(b_db):
    from app.events.facts import get_active_facts
    factory = b_db
    _seed_user(factory)
    _seed_world_fact(factory, char_id=1, user_id=1, predicate="mood", value="开心", hours_ago=11)
    _seed_world_fact(factory, char_id=2, user_id=1, predicate="mood", value="低落", hours_ago=13)
    _seed_world_fact(factory, char_id=3, user_id=1, predicate="status", value="在吃饭", hours_ago=11)
    _seed_world_fact(factory, char_id=4, user_id=1, predicate="status", value="在睡觉", hours_ago=13)
    assert [f.object_value for f in asyncio.run(get_active_facts(character_id=1, user_id=1))] == ["开心"]
    assert asyncio.run(get_active_facts(character_id=2, user_id=1)) == []
    assert [f.object_value for f in asyncio.run(get_active_facts(character_id=3, user_id=1))] == ["在吃饭"]
    assert asyncio.run(get_active_facts(character_id=4, user_id=1)) == []


def test_activity_not_limited_by_status_window(b_db):
    """activity 不入瞬时新鲜表：50h 前的活动事实仍注入（与改动前一致，走 3 天 TTL 兜底）。"""
    from app.events.facts import get_active_facts
    factory = b_db
    _seed_user(factory)
    _seed_world_fact(factory, char_id=1, user_id=1, predicate="activity", value="完成了创作", hours_ago=50)
    out = asyncio.run(get_active_facts(character_id=1, user_id=1))
    assert [f.object_value for f in out] == ["完成了创作"]


def test_same_predicate_only_latest_includes_location_mood(b_db):
    from app.events.facts import get_active_facts
    factory = b_db
    _seed_user(factory)
    _seed_world_fact(factory, char_id=1, user_id=1, predicate="location", value="旧城市", hours_ago=5)
    _seed_world_fact(factory, char_id=1, user_id=1, predicate="location", value="新城市", hours_ago=1)
    _seed_world_fact(factory, char_id=2, user_id=1, predicate="mood", value="旧心情", hours_ago=5)
    _seed_world_fact(factory, char_id=2, user_id=1, predicate="mood", value="新心情", hours_ago=1)
    loc = asyncio.run(get_active_facts(character_id=1, user_id=1))
    mood = asyncio.run(get_active_facts(character_id=2, user_id=1))
    assert [f.object_value for f in loc] == ["新城市"]
    assert [f.object_value for f in mood] == ["新心情"]


# ── C3 现状锚点 ─────────────────────────────────────────────────────────

def test_anchor_profile_location_switch(monkeypatch, b_db):
    """已授权位置感知：复习/主动通道（True）带城市；主聊天 section（False）不带（避免与 location 区重复）。"""
    from app.memory.current_state import current_user_state_anchor
    factory = b_db
    _seed_user(factory, location_enabled=True, location_city="深圳", user_location="广州")
    _set_flags(monkeypatch)
    with_loc = asyncio.run(current_user_state_anchor(character_id=1, user_id=1, include_profile_location=True))
    without_loc = asyncio.run(current_user_state_anchor(character_id=1, user_id=1, include_profile_location=False))
    assert "位置：深圳" in with_loc and "TA 当前已知现状" in with_loc
    assert without_loc == ""


def test_anchor_only_enabled_slots(monkeypatch, b_db):
    """只开 location：锚点含位置，job 事实虽在库但不注入（job 槽未启用）。"""
    from app.memory.current_state import current_user_state_anchor
    from app.memory.user_facts import upsert_user_fact
    factory = b_db
    _seed_user(factory)
    asyncio.run(upsert_user_fact(1, "location", "广州", source="gps"))
    asyncio.run(upsert_user_fact(1, "job", "程序员", source="chat"))
    _set_flags(monkeypatch, user_fact_location=True)
    txt = asyncio.run(current_user_state_anchor(character_id=1, user_id=1, include_profile_location=False))
    assert "位置：广州" in txt
    assert "工作" not in txt and "程序员" not in txt


def test_anchor_empty_when_unauthorized(monkeypatch, b_db):
    """默认零行为变化：未授权位置 + 槽全关 + 无 per-char 用户事实 → 空串（三通道都不注入）。"""
    from app.memory.current_state import current_user_state_anchor
    factory = b_db
    _seed_user(factory, location_enabled=False)
    _set_flags(monkeypatch)
    assert asyncio.run(current_user_state_anchor(character_id=1, user_id=1, include_profile_location=True)) == ""
    assert asyncio.run(current_user_state_anchor(character_id=1, user_id=1, include_profile_location=False)) == ""


def test_current_state_section_empty_by_default(monkeypatch, b_db):
    """主聊天 section：无现状返回 []（不注入空块）。"""
    from app.agent.context.section_current_state import current_state_section
    factory = b_db
    _seed_user(factory, location_enabled=False)
    _set_flags(monkeypatch)
    assert asyncio.run(current_state_section({"character_id": 1, "user_id": 1}, {})) == []


def test_current_state_section_injects_when_authorized(monkeypatch, b_db):
    from app.agent.context.section_current_state import current_state_section
    from app.memory.user_facts import upsert_user_fact
    factory = b_db
    _seed_user(factory)
    asyncio.run(upsert_user_fact(1, "job", "程序员", source="chat"))
    _set_flags(monkeypatch, user_fact_job=True)
    blocks = asyncio.run(current_state_section({"character_id": 1, "user_id": 1}, {}))
    assert len(blocks) == 1 and "工作：程序员" in blocks[0]


def test_review_anchor_equals_public_anchor(monkeypatch, b_db):
    """复习通道 _current_status_anchor 与公共锚点（带 User 城市）等价。"""
    from app.memory.current_state import current_user_state_anchor
    from app.scheduling.memory_review import _current_status_anchor
    factory = b_db
    _seed_user(factory, location_enabled=True, location_city="深圳")
    _set_flags(monkeypatch)
    a = asyncio.run(_current_status_anchor(1, 1))
    b = asyncio.run(current_user_state_anchor(character_id=1, user_id=1, include_profile_location=True))
    assert a == b != ""


def test_section_registered_order_42():
    """注册表接线自检：section 已注册（order=42，在 memories 40 之后、user_now 44 之前）。"""
    from app.agent.context.sections import get_sections
    keys = {s.key: s.order for s in get_sections()}
    assert "current_state_anchor" in keys
    assert keys["current_state_anchor"] == 42
    assert keys["current_state_anchor"] > keys.get("memories", 40)
    assert keys["current_state_anchor"] < keys.get("user_now", 44)


def test_proactive_anchor_empty_by_default(monkeypatch, b_db):
    """主动消息通道：默认（未授权）锚点为空串 → prompt 不新增现状块。"""
    from app.memory.current_state import current_user_state_anchor
    factory = b_db
    _seed_user(factory, location_enabled=False)
    _set_flags(monkeypatch)
    assert asyncio.run(current_user_state_anchor(character_id=1, user_id=1, include_profile_location=True)) == ""
