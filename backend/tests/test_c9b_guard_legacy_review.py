"""Independent missing-evidence paths; genuine sources and no mocked settlement."""
import asyncio
import pytest
from _plan_completion_fixture_c9b import ARRIVAL_PLAN, ARRIVAL_SIGNAL, c9b_world as c9b_world
from app.scheduling import prospective_intent as pi

@pytest.mark.parametrize("mode", ["legacy-text-only", "missing-plan-source", "foreign-completion-no-session", "collect-missing-source"])
def test_missing_or_unbound_evidence_cannot_close(c9b_world, mode):
    async def case():
        w = c9b_world
        if mode in ("missing-plan-source", "collect-missing-source"):
            ident = await w.plan(source_id=999999)
        else:
            ident = await w.plan()
        if mode == "legacy-text-only":
            assert await pi.settle_promises_on_user_message(201, 301, ARRIVAL_SIGNAL) == []
        elif mode == "foreign-completion-no-session":
            foreign = await w.message(ARRIVAL_SIGNAL, session_id=305, ago=1)
            assert await pi.settle_promises_on_user_message(201, None, ARRIVAL_SIGNAL,
                user_id=101, source_message_id=foreign) == []
        elif mode == "missing-plan-source":
            source = await w.message(ARRIVAL_SIGNAL, ago=1)
            assert await pi.settle_promises_on_user_message(201, 301, ARRIVAL_SIGNAL,
                user_id=101, source_message_id=source) == []
        else:
            await w.message(ARRIVAL_SIGNAL, ago=1)
            assert ident in await w.candidates(), "Collection must retain an unbound plan rather than assert fulfillment"
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())

def test_valid_bound_completion_still_closes(c9b_world):
    async def case():
        w = c9b_world
        ident = await w.plan(ARRIVAL_PLAN)
        source = await w.message(ARRIVAL_SIGNAL, ago=1)
        assert await pi.settle_promises_on_user_message(201, 301, ARRIVAL_SIGNAL,
            user_id=101, source_message_id=source) == [ident]
        assert (await w.state(ident))["status"] == "discharged"
    asyncio.run(case())
