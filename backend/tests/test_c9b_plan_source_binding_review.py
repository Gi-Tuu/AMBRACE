"""Synthetic authoritative plan-source identity checks; real database and services."""
import asyncio
import pytest
from _plan_completion_fixture_c9b import ARRIVAL_PLAN, ARRIVAL_SIGNAL, c9b_world as c9b_world
from app.models.chat import ChatMessage
from app.scheduling import prospective_intent as pi

@pytest.mark.parametrize("route", ["settle", "collect"])
@pytest.mark.parametrize("source_kind", ["other-account", "other-session", "assistant", "valid"])
def test_plan_source_must_bind_to_actual_obligation(c9b_world, route, source_kind):
    async def case():
        w = c9b_world
        sess = {"other-account": 305, "other-session": 302}.get(source_kind, 301)
        source = await w.message(ARRIVAL_PLAN, session_id=sess, ago=20)
        if source_kind == "assistant":
            async with w.factory() as db:
                msg = await db.get(ChatMessage, source)
                msg.sender_type = "ai"
                await db.commit()
        ident = await w.plan(source_id=source)
        signal = await w.message(ARRIVAL_SIGNAL, ago=1)
        if route == "settle":
            actual = await pi.settle_promises_on_user_message(
                201, 301, ARRIVAL_SIGNAL, user_id=101, source_message_id=signal)
            assert actual == ([ident] if source_kind == "valid" else [])
        else:
            remaining = await w.candidates()
            assert (ident not in remaining) == (source_kind == "valid")
        assert (await w.state(ident))["status"] == (
            "discharged" if source_kind == "valid" else "pending")
    asyncio.run(case())
