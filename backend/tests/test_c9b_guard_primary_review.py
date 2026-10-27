"""Independent synthetic primary-review cases; no mocked settlement or candidate logic."""
import asyncio
import pytest
from _plan_completion_fixture_c9b import ARRIVAL_PLAN, c9b_world as c9b_world
from app.scheduling import prospective_intent as pi

@pytest.mark.parametrize("mode", ["different-text", "different-character", "earlier-event"])
def test_real_source_identity_and_time_are_required(c9b_world, mode):
    async def case():
        w = c9b_world
        if mode == "different-character":
            ident = await pi.upsert_intent(user_id=101, character_id=202, content=ARRIVAL_PLAN,
                cue_terms=["设备校验"], due_end=w.due, chat_session_id=None, side="user")
            source = await w.message("我到了质检台", ago=1)
            char = 202
        elif mode == "earlier-event":
            source = await w.message("我到了质检台", ago=40)
            later = await w.message("用户再次到达质检台后扫描设备Q", ago=5)
            ident = await w.plan("用户再次到达质检台后扫描设备Q", source_id=later)
            char = 201
        else:
            ident = await w.plan()
            source = await w.message("检测仪的指示灯为蓝色", ago=1)
            char = 201
        result = await pi.settle_promises_on_user_message(
            char, 301, "我到了质检台", user_id=101, source_message_id=source)
        assert result == [], "A real unrelated source row is not sufficient completion evidence"
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())

def test_same_text_different_sessions_keep_distinct_identity(c9b_world):
    async def case():
        first = await c9b_world.plan()
        second = await c9b_world.plan(session_id=302)
        assert first != second
        assert (await c9b_world.state(second))["session_id"] == 302
    asyncio.run(case())

@pytest.mark.parametrize("text", [
    '检测员发回一条记录："我到了质检台"，本人尚未出发',
    "他已经到门口，我尚未出发",
    "如果质检台已经开放，我到了之后才启动扫描",
])
def test_context_not_lost_at_clause_split(c9b_world, text):
    async def case():
        ident = await c9b_world.plan()
        await c9b_world.signal(text)
        assert (await c9b_world.state(ident))["status"] == "pending"
    asyncio.run(case())

def test_precise_due_compares_utc_to_local_consistently():
    from datetime import datetime
    signal = datetime(2032, 6, 18, 1, 55)  # Beijing 09:55
    due_local = datetime(2032, 6, 19, 9, 50)
    assert pi._signal_fresh_enough(signal, due_local) is True
