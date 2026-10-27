"""Structured kernel tests; all sources are synthetic."""
import asyncio
from datetime import timedelta
import pytest
from sqlalchemy import delete, select, func, text
from sqlalchemy.exc import IntegrityError
pytest_plugins = ["_plan_completion_fixture_c9b"]
from app.memory.plan_completion import (PlanScope, EvidenceConflict, register_item,
    record_assertion, bind_explicit, item_is_completed)
from app.models import ChatMessage, PlanItem, PlanCompletionEvidence

SCOPE = PlanScope(101,201,301)
TARGET = "calibrate synthetic sensor Z"
CLAIM = TARGET + " complete"


async def seed(w):
    return await w.message(TARGET, ago=20), await w.message(CLAIM, ago=10)


def test_unresolved_survives_late_plan_and_replay(c9b_world):
    async def case():
        w = c9b_world
        source, signal = await seed(w)
        async with w.factory() as db:
            ev = await record_assertion(db,SCOPE,signal,TARGET,CLAIM)
            eid = ev.id
            assert ev.item_id is None
            await db.commit()
        async with w.factory() as db:
            item = await register_item(db,SCOPE,source,TARGET)
            ev = await db.get(PlanCompletionEvidence,eid)
            assert await bind_explicit(db,ev,item)
            await db.commit()
        async with w.factory() as db:
            item = await register_item(db,SCOPE,source,TARGET)
            ev = await record_assertion(db,SCOPE,signal,TARGET,CLAIM)
            assert await bind_explicit(db,ev,item)
            assert await item_is_completed(db,item)
            assert await db.scalar(select(func.count()).select_from(PlanItem)) == 1
            assert await db.scalar(select(func.count()).select_from(PlanCompletionEvidence)) == 1
            assert ev.source_event_at == w.now - timedelta(minutes=10)
    asyncio.run(case())


@pytest.mark.parametrize("scope", [PlanScope(103,201,301),PlanScope(101,202,301),PlanScope(101,201,302)])
def test_wrong_scope_rejected(c9b_world,scope):
    async def case():
        source = await c9b_world.message(TARGET)
        async with c9b_world.factory() as db:
            with pytest.raises(EvidenceConflict):
                await register_item(db,scope,source,TARGET)
            assert await db.scalar(select(func.count()).select_from(PlanItem)) == 0
    asyncio.run(case())


@pytest.mark.parametrize("bad", ["missing","ai","span","negative_ordinal"])
def test_invalid_sources_rejected(c9b_world,bad):
    async def case():
        source = await c9b_world.message(TARGET)
        async with c9b_world.factory() as db:
            if bad == "ai":
                msg = await db.get(ChatMessage,source)
                msg.sender_type = "ai"
                await db.flush()
            with pytest.raises(EvidenceConflict):
                await register_item(db,SCOPE,source+10000 if bad == "missing" else source,
                    "fabricated" if bad == "span" else TARGET,
                    ordinal=-1 if bad == "negative_ordinal" else 0)
    asyncio.run(case())


def test_conflicting_replay_rolls_back_whole_transaction(c9b_world):
    async def case():
        source = await c9b_world.message(TARGET + " and another target")
        async with c9b_world.factory() as db:
            await register_item(db,SCOPE,source,TARGET)
            with pytest.raises(EvidenceConflict):
                await register_item(db,SCOPE,source,"another target")
            await db.rollback()
        async with c9b_world.factory() as db:
            assert await db.scalar(select(func.count()).select_from(PlanItem)) == 0
    asyncio.run(case())


def test_old_wrong_scope_and_rebound_evidence_rejected(c9b_world):
    async def case():
        w = c9b_world
        source, signal = await seed(w)
        old = await w.message(CLAIM,ago=30)
        foreign = await w.message(CLAIM,session_id=302)
        async with w.factory() as db:
            item = await register_item(db,SCOPE,source,TARGET)
            ev = await record_assertion(db,SCOPE,old,TARGET,CLAIM)
            assert not await bind_explicit(db,ev,item)
            ev = await record_assertion(db,PlanScope(101,201,302),foreign,TARGET,CLAIM)
            assert not await bind_explicit(db,ev,item)
            ev = await record_assertion(db,SCOPE,signal,TARGET,CLAIM)
            assert await bind_explicit(db,ev,item)
            second = await register_item(db,SCOPE,source,TARGET,ordinal=1)
            with pytest.raises(EvidenceConflict):
                await bind_explicit(db,ev,second)
    asyncio.run(case())


@pytest.mark.parametrize("change", ["delete","edit","time"])
def test_invalidated_source_cannot_keep_completion(c9b_world,change):
    async def case():
        w = c9b_world
        source, signal = await seed(w)
        async with w.factory() as db:
            item = await register_item(db,SCOPE,source,TARGET)
            ev = await record_assertion(db,SCOPE,signal,TARGET,CLAIM)
            assert await bind_explicit(db,ev,item)
            assert await item_is_completed(db,item)
            msg = await db.get(ChatMessage,signal)
            if change == "delete":
                await db.execute(delete(ChatMessage).where(ChatMessage.id==signal))
            elif change == "edit":
                msg.content = "retracted synthetic assertion"
            else:
                msg.created_at += timedelta(seconds=1)
            await db.flush()
            assert not await item_is_completed(db,item)
            assert await db.scalar(select(func.count()).select_from(PlanCompletionEvidence)) == 1
            assert not await bind_explicit(db,ev,item)
    asyncio.run(case())


def test_fk_enabled_and_session_cleanup(c9b_world):
    async def case():
        w = c9b_world
        source, signal = await seed(w)
        async with w.factory() as db:
            assert await db.scalar(text("PRAGMA foreign_keys")) == 1
            item = await register_item(db,SCOPE,source,TARGET)
            ev = await record_assertion(db,SCOPE,signal,TARGET,CLAIM)
            assert await bind_explicit(db,ev,item)
            iid = item.id
            await db.commit()
            await db.execute(text("DELETE FROM chat_sessions WHERE id=301"))
            await db.commit()
            assert await db.get(PlanItem,iid,populate_existing=True) is None
            assert await db.scalar(select(func.count()).select_from(PlanCompletionEvidence)) == 0
            assert (await db.execute(text("PRAGMA foreign_key_check"))).all() == []
        async with w.factory() as db:
            with pytest.raises(IntegrityError):
                await db.execute(text("INSERT INTO business_plan_items (item_key,user_id,character_id,chat_session_id,source_event_at,source_hash,ordinal,target_text) VALUES ('bad',999,201,302,'2026-01-01','hash',0,'test')"))
    asyncio.run(case())


@pytest.mark.parametrize("reverse", [False,True])
def test_same_timestamp_uses_real_source_id_order(c9b_world,reverse):
    async def case():
        w = c9b_world
        if reverse:
            signal = await w.message(CLAIM)
            source = await w.message(TARGET)
        else:
            source = await w.message(TARGET)
            signal = await w.message(CLAIM)
        async with w.factory() as db:
            item = await register_item(db,SCOPE,source,TARGET)
            ev = await record_assertion(db,SCOPE,signal,TARGET,CLAIM)
            assert await bind_explicit(db,ev,item) is (not reverse)
    asyncio.run(case())


def test_family_access_does_not_collapse_actual_actor_scope(c9b_world):
    async def case():
        w = c9b_world
        source = await w.message(TARGET,ago=20)
        child_source = await w.message(TARGET,session_id=305,ago=20)
        child_signal = await w.message(CLAIM,session_id=305,ago=10)
        async with w.factory() as db:
            root_item = await register_item(db,SCOPE,source,TARGET)
            child_scope = PlanScope(102,201,305)
            child_item = await register_item(db,child_scope,child_source,TARGET)
            ev = await record_assertion(db,child_scope,child_signal,TARGET,CLAIM)
            assert not await bind_explicit(db,ev,root_item)
            assert await bind_explicit(db,ev,child_item)
    asyncio.run(case())


def test_actual_account_discovery_and_character_cleanup(c9b_world):
    async def case():
        from app.application.user_cascade import discover_purge_plan, SCOPE_DELETE_FAMILY_ROOT
        w = c9b_world
        source, signal = await seed(w)
        async with w.factory() as db:
            item = await register_item(db,SCOPE,source,TARGET)
            ev = await record_assertion(db,SCOPE,signal,TARGET,CLAIM)
            assert await bind_explicit(db,ev,item)
            plan = await discover_purge_plan(db,user_id=101,scope=SCOPE_DELETE_FAMILY_ROOT)
            found = {entry["table"] for entry in plan["user_family"]}
            assert {"business_plan_items","plan_completion_evidence"} <= found
            await db.execute(text("DELETE FROM ai_characters WHERE id=201"))
            await db.flush()
            for model in (PlanItem,PlanCompletionEvidence):
                assert await db.scalar(select(func.count()).select_from(model)) == 0
            assert (await db.execute(text("PRAGMA foreign_key_check"))).all() == []
    asyncio.run(case())


@pytest.mark.parametrize("value", [True,0,-1,"101"])
def test_scope_identity_rejects_numeric_aliases(value):
    with pytest.raises(EvidenceConflict):
        PlanScope(value,201,301)


def test_source_id_rejects_bool_alias(c9b_world):
    async def case():
        async with c9b_world.factory() as db:
            with pytest.raises(EvidenceConflict):
                await register_item(db,SCOPE,True,TARGET)
    asyncio.run(case())
