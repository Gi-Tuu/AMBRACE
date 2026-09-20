# -*- coding: utf-8 -*-
"""记忆时态缺陷族·第二批（2026-09-17）回归用例。

覆盖交接文件任务 1–3 的可验证结论：
- 任务1：元对话/情绪宣泄守卫（纯规则，正常句不误伤）+ title 证据锚点 + tense 兜底收窄；
- 任务2：位置槽跨角色共享开关（默认开、不吃细槽总闸）+ 位置值证据锚点（拒垃圾值）+
  易变槽 TTL（valid_to）+ 现状锚点取到共享位置；**红线：感情/健康仍 opt-in**；
- 任务2.3：位置冲突保守判定（「在长沙」判冲突、「长沙特产」不判）；
- 任务3：迁移在链上（单 head 由 alembic 校验，sqlite 空库双向由交接验证脚本跑）。

用项目既有「临时 SQLite 文件库 + monkeypatch 异步工厂」夹具法，不触碰 backend/data。
"""
import asyncio
import os
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.memory.tense import classify_tense

pytestmark = pytest.mark.slow

_SLOT_FLAGS = (
    "user_fact_location", "user_fact_job", "user_fact_relationship",
    "user_fact_living", "user_fact_goal_state", "user_fact_health",
)

_STRONG_LOCATION = "常驻湛江市·广东海洋大学湖光校区·学生宿舍（大二在读学生）"


@pytest.fixture()
def b2_db(monkeypatch, tmp_path):
    """临时库：建全模型 + 把相关模块的异步工厂指向临时工厂（测试产物在 tmp_path）。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.join(str(tmp_path), 'b2.db')}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    import app.memory.user_facts as uf
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(uf, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _set_flags(monkeypatch, **kw):
    """显式设置 AGENT_FLAGS（未指定的槽 flag 一律 False，保证用例确定性）。"""
    from app.agent.loop import AGENT_FLAGS as _af
    monkeypatch.setitem(_af, "global_user_facts", bool(kw.get("global_user_facts", False)))
    for k in _SLOT_FLAGS:
        monkeypatch.setitem(_af, k, bool(kw.get(k, False)))


def _seed_user(factory, *, user_id=1, location_enabled=False, user_location=None, location_city=None):
    async def _run():
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=user_id, username=f"tester{user_id}", nickname="测试",
                        location_enabled=location_enabled,
                        user_location=user_location, location_city=location_city))
            await db.commit()
    asyncio.run(_run())


# ────────────────────────── 任务1：元对话守卫 / title 锚点（纯函数）──────────────────────────

def test_meta_guard_blocks_production_sample():
    """生产实证 id=9814：记忆回退气话 + 无事实锚点 → 判元对话污染（不写 user_info/enduring）。"""
    from app.memory.meta_guard import is_meta_about_ai, is_meta_without_anchor
    s = "你看看你上面说什么去长沙不带电脑，粥好了快来吃，明显就是记忆回退了，我是个失败的爱人"
    assert is_meta_about_ai(s) is True
    assert is_meta_without_anchor(s) is True


@pytest.mark.parametrize("text", [
    "用户今天去图书馆复习，准备考试",
    "用户在学习 AI 模型相关课程",
    "你记得我喜欢吃辣吗",
    "用户叫小轩，今年大二，在湛江读书",
    "用户总是忘了他答应的事",
    "用户说他想养一只猫，家里已经有一只狗",
])
def test_meta_guard_does_not_block_normal_sentences(text):
    """误伤面守卫：带真实事实锚点（学业/偏好/身份/位置）的正常句一律放行。"""
    from app.memory.meta_guard import is_meta_without_anchor
    assert is_meta_without_anchor(text) is False


def test_title_evidence_anchor():
    """任务1.3：title「用户的名字/职业」必须有证据，否则泛化 title 并归 episodic。"""
    from app.memory.meta_guard import apply_title_anchor, looks_like_person_name
    assert looks_like_person_name("小轩") is True
    assert looks_like_person_name("sam") is True
    assert looks_like_person_name("个失败的爱人") is False
    assert apply_title_anchor("用户的名字", "我是个失败的爱人") == ("一段对话", False)
    assert apply_title_anchor("用户的名字", "我叫小轩") == ("用户的名字", True)
    assert apply_title_anchor("用户的职业", "我在工地搬砖") == ("一段对话", False)
    assert apply_title_anchor("用户的职业", "我是湛江的大学生") == ("用户的职业", True)
    # 其它 title 不设锚点（不扩大误伤面）
    assert apply_title_anchor("用户的年龄", "我今年 20 岁") == ("用户的年龄", True)


def test_response_parser_downgrades_bad_title_and_meta():
    """提取管线：坏 title 降级 + 元对话整条降级为 event（不再进 user_info）。"""
    from app.agent.response_parser import extract_info_from_message
    bad = extract_info_from_message("我是个失败的爱人，粥好了快来吃，你的记忆回退了")
    assert bad and all(m["type"] == "event" for m in bad)
    assert all(m["title"] == "一段对话" for m in bad)
    good = extract_info_from_message("我叫小轩")
    assert any(m["type"] == "user_info" and m["title"] == "用户的名字" for m in good)


def test_tense_fallback_narrowed():
    """任务1.1：兜底收窄——已发生琐事/元对话守卫行归 episodic；正常恒久事实不被误伤。"""
    assert classify_tense({"memory_type": "user_info", "sub_type": "extracted",
                           "content": "用户买了三盒披萨回家"}) == "episodic"
    assert classify_tense({"memory_type": "user_info", "sub_type": "extracted",
                           "content": "用户点了外卖，外卖刚到"}) == "episodic"
    assert classify_tense({"memory_type": "event", "sub_type": "meta_guard",
                           "content": "明天记得回退"}) == "episodic"
    assert classify_tense({"memory_type": "user_info", "sub_type": None, "title": "用户的名字",
                           "content": "你的记忆又回退了，粥好了快来吃，我是个失败的爱人"}) == "episodic"
    # 不误伤（与 test_memory_reminisce 的既有口径一致）
    assert classify_tense({"memory_type": "user_info", "sub_type": None,
                           "content": "用户叫小轩"}) == "enduring"
    assert classify_tense({"memory_type": "preference", "sub_type": None,
                           "content": "用户不吃香菜"}) == "enduring"
    assert classify_tense({"memory_type": "user_info", "sub_type": "extracted",
                           "content": "用户近期将去长沙"}) == "plan"


# ────────────────────────── 任务2.3：位置冲突保守判定（纯函数）──────────────────────────

def test_location_value_anchor_and_conflict():
    from app.memory.location_guard import (
        looks_like_location_value, location_conflict, strong_location_value,
    )
    # 值锚点：垃圾聊天行被拒；强/弱锚点区分
    assert looks_like_location_value(_STRONG_LOCATION) is True
    assert looks_like_location_value("湛江市") is True
    assert looks_like_location_value("用户说芒芒已经被照顾好了，外卖到了会叫我。") is False
    assert strong_location_value(_STRONG_LOCATION) is True
    assert strong_location_value("长沙") is False
    # 冲突判定：当前位置介词窗口内的异城判冲突；名词性提及/出行叙事/权威城市本身不判
    assert location_conflict("轩这会儿在长沙顶着大太阳", _STRONG_LOCATION) == "长沙"
    assert location_conflict("把长沙特产的事记你账上", _STRONG_LOCATION) is None
    assert location_conflict("你上次说要去长沙玩", _STRONG_LOCATION) is None
    assert location_conflict("我回湛江了", _STRONG_LOCATION) is None
    assert location_conflict("我在长沙", "") is None


# ────────────────────────── 任务2：共享读路径 + 红线（DB）──────────────────────────

@pytest.mark.slow
def test_shared_location_not_gated_and_sensitive_slots_off(monkeypatch, b2_db):
    """红线：放行 location 共享后，感情/健康仍为 False；写侧细槽仍 opt-in。"""
    from app.memory.user_facts import (
        enabled_user_fact_slots, get_shared_user_facts, upsert_user_fact,
        user_current_location_shared, user_fact_slot_enabled,
    )
    _set_flags(monkeypatch)  # 总闸关 + 6 槽全关
    _seed_user(b2_db)
    asyncio.run(upsert_user_fact(1, "location", "湛江市", source="manual"))
    assert user_current_location_shared() is True            # 独立开关默认开
    assert user_fact_slot_enabled("location") is False       # 写侧不受影响
    assert user_fact_slot_enabled("relationship") is False   # 红线
    assert user_fact_slot_enabled("health") is False         # 红线
    assert enabled_user_fact_slots() == []
    assert asyncio.run(get_shared_user_facts(1)) == {"location": "湛江市"}


@pytest.mark.slow
def test_shared_read_never_carries_sensitive_slots(monkeypatch, b2_db):
    """共享读路径只从 enabled 槽 + location 取；库里有感情/健康行也不带出去。"""
    from app.memory.user_facts import get_shared_user_facts, upsert_user_fact
    _set_flags(monkeypatch)
    _seed_user(b2_db)
    asyncio.run(upsert_user_fact(1, "location", "湛江市", source="manual"))
    asyncio.run(upsert_user_fact(1, "relationship", "已婚", source="chat"))
    asyncio.run(upsert_user_fact(1, "health", "生病住院", source="chat"))
    assert asyncio.run(get_shared_user_facts(1)) == {"location": "湛江市"}


@pytest.mark.slow
def test_shared_location_off_rolls_back(monkeypatch, b2_db):
    from app.agent.loop import AGENT_FLAGS
    from app.memory.user_facts import get_shared_user_facts, upsert_user_fact
    _set_flags(monkeypatch)
    _seed_user(b2_db)
    asyncio.run(upsert_user_fact(1, "location", "湛江市", source="manual"))
    monkeypatch.setitem(AGENT_FLAGS, "user_current_location_share", False)
    assert asyncio.run(get_shared_user_facts(1)) == {}


@pytest.mark.slow
def test_location_write_rejects_non_location_value(monkeypatch, b2_db):
    """任务2：位置槽证据锚点——无关聊天行不得覆盖权威值。"""
    from app.memory.user_facts import get_active_user_facts, upsert_user_fact
    _set_flags(monkeypatch, user_fact_location=True)
    _seed_user(b2_db)
    assert asyncio.run(upsert_user_fact(1, "location", "用户说芒芒已经被照顾好了，外卖到了会叫我。")) is None
    assert asyncio.run(get_active_user_facts(1, slots=["location"])) == []
    assert asyncio.run(upsert_user_fact(1, "location", "湛江市")) is not None


@pytest.mark.slow
def test_location_ttl_and_previous_value_fallback(monkeypatch, b2_db):
    """任务2.5：易变槽写 TTL；现行值不达标时回退强锚点 previous_value（弱旧值不复活）。"""
    from app.memory.user_facts import (
        fact_is_expired, get_active_user_facts, resolve_location_value, upsert_user_fact,
    )
    from app.utils.timeutil import now_naive_utc
    _set_flags(monkeypatch, user_fact_location=True)
    _seed_user(b2_db)
    asyncio.run(upsert_user_fact(1, "location", _STRONG_LOCATION))
    asyncio.run(upsert_user_fact(1, "location", "湛江市"))  # 新值取代 → 权威值进 previous_value
    row = asyncio.run(get_active_user_facts(1, slots=["location"]))[0]
    assert row.valid_to is not None
    assert row.valid_to > now_naive_utc()
    # 过期判定（dict 兼容）
    assert fact_is_expired({"valid_to": now_naive_utc() - timedelta(days=1)}) is True
    assert fact_is_expired({"valid_to": None}) is False
    # 现行值被垃圾覆盖 → 回退 previous_value 强锚点（与生产 user_id=3 同形）
    row.value = "用户说芒芒已经被照顾好了，外卖到了会叫我。"
    assert resolve_location_value(row) == _STRONG_LOCATION
    # 弱旧值（长沙）不满足强锚点 → 不复活
    row.value = "垃圾值"
    row.previous_value = "长沙"
    assert resolve_location_value(row) is None


@pytest.mark.slow
def test_expired_fact_not_read(monkeypatch, b2_db):
    from app.memory.user_facts import get_active_user_facts
    from app.models.user import GlobalUserFact
    from app.utils.timeutil import now_naive_utc
    _set_flags(monkeypatch, user_fact_location=True)
    _seed_user(b2_db)

    async def _seed():
        async with b2_db() as db:
            db.add(GlobalUserFact(user_id=1, slot="location", value="湛江市",
                                  valid_from=now_naive_utc(),
                                  valid_to=now_naive_utc() - timedelta(days=1)))
            await db.commit()
    asyncio.run(_seed())
    assert asyncio.run(get_active_user_facts(1, slots=["location"])) == []


@pytest.mark.slow
def test_current_state_anchor_uses_shared_location(monkeypatch, b2_db):
    """任务2.2：现状锚点（主聊天 section include_profile_location=False）也能取到共享位置。"""
    from app.memory.current_state import current_user_state_anchor
    from app.memory.user_facts import upsert_user_fact
    _set_flags(monkeypatch)  # 槽全关（未授权 User 城市）
    _seed_user(b2_db, location_enabled=False)
    asyncio.run(upsert_user_fact(1, "location", "湛江市", source="manual"))
    txt = asyncio.run(current_user_state_anchor(
        character_id=1, user_id=1, include_profile_location=False))
    assert "位置：湛江市" in txt


# ────────────────────────── flag 登记自检 ──────────────────────────

def test_flag_registered_default_on():
    from app.agent.loop import AGENT_FLAGS
    assert AGENT_FLAGS.get("user_current_location_share") is True
