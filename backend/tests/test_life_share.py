# -*- coding: utf-8 -*-
"""#63 机制4：活动完成自然分享单测（概率/亲密度/fatigue 门控、arbiter 门控、配额、发送）。"""
import asyncio
import os
import random
import tempfile

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.scheduler import life_share
from app.models.character import CharacterState
from app.models.character import ProactiveTriggerLog


@pytest.fixture
def share_db():
    tmp = tempfile.mkdtemp(prefix="life_share_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(CharacterState(character_id=101, trust=80, attachment=90, fatigue=30))
            await db.commit()

    asyncio.run(_init())
    yield factory
    asyncio.run(engine.dispose())


# ---------------- 纯函数 ----------------
def test_share_probability_by_type():
    assert life_share.share_probability("create") == 0.3
    assert life_share.share_probability("browse") == 0.15
    assert life_share.share_probability("learn") == 0.12
    assert life_share.share_probability("reflect") == 0.05
    assert life_share.share_probability("rest") == 0.0
    assert life_share.share_probability("unknown") == 0.0


def test_intimacy_multiplier_bounds():
    assert life_share.intimacy_multiplier(50, 50) == pytest.approx(1.0, abs=0.01)
    assert life_share.intimacy_multiplier(100, 100) == pytest.approx(1.3, abs=0.01)
    assert life_share.intimacy_multiplier(0, 0) == pytest.approx(0.7, abs=0.01)


def test_should_share_fatigue_blocks():
    class _Never:
        def random(self):
            return 0.0
    never = _Never()
    ok, _ = life_share.should_share(0.3, 1.0, 80, rng=never)  # fatigue>75 拦截
    assert ok is False
    ok, prob = life_share.should_share(0.3, 1.0, 30, rng=never)
    assert ok is True
    assert prob == pytest.approx(0.3, abs=0.01)


def test_should_share_zero_probability():
    ok, _ = life_share.should_share(0.0, 1.0, 30)
    assert ok is False


def test_should_share_intimacy_scales_probability():
    ok, prob_high = life_share.should_share(0.3, 1.3, 30, rng=random.Random(0))
    ok2, prob_low = life_share.should_share(0.3, 0.7, 30, rng=random.Random(0))
    assert prob_high > prob_low


# ---------------- 配额 ----------------
def test_life_share_quota(share_db):
    async def _run():
        async with share_db() as db:
            # 初始无配额 → ok
            assert await life_share._quota_ok(db, 101) is True
            # 插入一条 6h 内 approved → 不再 ok
            db.add(ProactiveTriggerLog(
                character_id=101, user_id=1, trigger_type="life_share", decision="approved", priority=5,
            ))
            await db.commit()
            assert await life_share._quota_ok(db, 101) is False
    asyncio.run(_run())


# ---------------- 门控 + 发送 ----------------
def test_on_activity_completed_gate_and_send(share_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "life_share_enabled", True)
    monkeypatch.setattr(life_share, "async_session_factory", share_db)

    sent = []

    async def _fake_send(session_id, character_id, user_id, content, message_type="state_trigger"):
        sent.append((session_id, character_id, user_id, content, message_type))

    async def _fake_sid(user_id, character_id):
        return 1

    async def _fake_gen(character_id, user_id, activity_type, summary, retry=False):
        return "我刚画完一张图，超好看的！"

    # arbiter 门控全部放行
    for f in ("is_dnd_now", "is_user_active", "unreplied_cooldown_active"):
        async def _pass(*a, **k):
            return False
        monkeypatch.setattr(f"app.scheduler.arbiter.{f}", _pass)

    monkeypatch.setattr(life_share, "_generate_share", _fake_gen)
    monkeypatch.setattr(life_share, "_naturalness_flag", lambda: False)
    monkeypatch.setattr(life_share, "should_share", lambda *a, **k: (True, 0.3))
    monkeypatch.setattr("app.scheduler.scheduler.send_to_session", _fake_send)
    monkeypatch.setattr("app.services.chat_service.get_latest_session_id", _fake_sid)

    payload = {"data": {
        "user_id": 1, "character_id": 101, "activity_type": "create",
        "summary": "画了一张水彩风景", "importance": 60,
    }}
    asyncio.run(life_share.on_activity_completed(payload))
    assert len(sent) == 1
    assert sent[0][1] == 101 and sent[0][4] == "life_share"
    # 配额已落库
    async def _check():
        async with share_db() as db:
            rows = (await db.execute(
                select(ProactiveTriggerLog).where(ProactiveTriggerLog.trigger_type == "life_share")
            )).scalars().all()
            return len(rows)
    assert asyncio.run(_check()) == 1


def test_on_activity_completed_user_active_blocks(share_db, monkeypatch):
    """用户活跃（is_user_active=True）→ 不分享、不发送。"""
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "life_share_enabled", True)
    monkeypatch.setattr(life_share, "async_session_factory", share_db)
    sent = []

    async def _fake_send(*a, **k):
        sent.append(a)

    # 用户活跃 → 拦截
    async def _active(*a, **k):
        return True
    async def _pass(*a, **k):
        return False
    for f in ("is_dnd_now", "unreplied_cooldown_active"):
        monkeypatch.setattr(f"app.scheduler.arbiter.{f}", _pass)
    monkeypatch.setattr("app.scheduler.arbiter.is_user_active", _active)
    monkeypatch.setattr(life_share, "should_share", lambda *a, **k: (True, 0.3))
    monkeypatch.setattr("app.scheduler.scheduler.send_to_session", _fake_send)

    payload = {"data": {
        "user_id": 1, "character_id": 101, "activity_type": "create",
        "summary": "画了一张画", "importance": 60,
    }}
    asyncio.run(life_share.on_activity_completed(payload))
    assert sent == []


def test_generate_share_passes_character_id(share_db, monkeypatch):
    """P3-6：_generate_share 调 chat_completion 时补传 character_id（未来角色级模型配置）。"""
    captured = {}

    async def _fake_chat(**kw):
        captured.update(kw)
        return "我刚画完一张图！"

    async def _fake_sid(user_id, character_id):
        return 1

    async def _fake_last(*a, **k):
        return ""

    monkeypatch.setattr(life_share, "async_session_factory", share_db)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_chat)
    monkeypatch.setattr("app.services.chat_service.get_latest_session_id", _fake_sid)
    monkeypatch.setattr("app.scheduler.triggers.get_last_messages", _fake_last)

    text = asyncio.run(life_share._generate_share(101, 1, "create", "画了一张水彩风景"))
    assert text == "我刚画完一张图！"
    assert captured.get("character_id") == 101
