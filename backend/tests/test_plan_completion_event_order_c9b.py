"""C9b event-order contracts: genuine failures remain assertions, never xfail/skip."""
import asyncio
import sqlite3

import pytest
from sqlalchemy import select

from app.models.chat import ChatMessage
from app.models.memory import ProcessedExtraction, ProspectiveIntent

from _plan_completion_fixture_c9b import c9b_world as c9b_world
from datetime import timedelta

from _plan_completion_fixture_c9b import (
    ARRIVAL_PLAN, GENERIC_DONE, GENERIC_PLAN,
)


def test_supported_event_normal_order_closes_silently(c9b_world):
    async def case():
        w = c9b_world
        ident = await w.plan()
        assert ident in await w.candidates()
        before = await w.counts()
        assert await w.signal() == [ident]
        assert (await w.state(ident))["status"] == "discharged"
        assert ident not in await w.candidates()
        assert await w.counts() == before
    asyncio.run(case())


def test_generic_completion_normal_order_excludes_candidate(c9b_world):
    async def case():
        w = c9b_world
        ident = await w.plan(GENERIC_PLAN)
        assert ident in await w.candidates()
        await w.signal(GENERIC_DONE)
        assert ident not in await w.candidates(), "Explicit synthetic task completion must remove its reminder"
    asyncio.run(case())


def test_supported_late_plan_latest_signal_is_guarded(c9b_world):
    async def case():
        w = c9b_world
        source = await w.message(ARRIVAL_PLAN, ago=30)
        assert await w.signal() == []
        ident = await w.plan(source_id=source)
        assert (await w.state(ident))["status"] == "pending"
        assert ident not in await w.candidates()
        assert (await w.state(ident))["status"] == "discharged"
    asyncio.run(case())


def test_supported_late_plan_buried_signal_is_still_completed(c9b_world):
    async def case():
        w = c9b_world
        source = await w.message(ARRIVAL_PLAN, ago=30)
        await w.signal()
        await w.message("检测仪表的指示灯为蓝色", ago=1)
        ident = await w.plan(source_id=source)
        assert ident not in await w.candidates(), "An intervening message must not erase completion evidence"
    asyncio.run(case())


def test_generic_late_plan_cannot_revive_completed_task(c9b_world):
    async def case():
        w = c9b_world
        source = await w.message(GENERIC_PLAN, ago=30)
        await w.signal(GENERIC_DONE)
        ident = await w.plan(GENERIC_PLAN, source_id=source)
        assert ident not in await w.candidates(), "A later database insert must not revive an earlier completed plan"
    asyncio.run(case())


def test_source_replay_and_repeated_completion_are_idempotent(c9b_world):
    async def case():
        w = c9b_world
        source = await w.message(ARRIVAL_PLAN, ago=30)
        ident = await w.plan(source_id=source)
        assert await w.plan(source_id=source) == ident
        before = await w.counts()
        assert await w.signal() == [ident]
        first = await w.state(ident)
        assert await w.signal() == []
        assert await w.plan(source_id=source) == ident
        assert await w.state(ident) == first
        assert await w.counts() == before
    asyncio.run(case())


def test_past_signal_does_not_complete_next_calendar_plan(c9b_world):
    async def case():
        w = c9b_world
        source = await w.message("用户明天到地方之后读取设备D铭牌", ago=30)
        await w.signal()
        ident = await w.plan("用户明天到地方之后读取设备D铭牌", source_id=source,
                             due=w.due + timedelta(days=1))
        w.now += timedelta(days=1)
        assert ident in await w.candidates(), "Yesterday's event is not evidence for tomorrow's distinct plan"
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_source_event_identity_is_preserved_on_late_write(c9b_world):
    async def case():
        w = c9b_world
        source = await w.message(GENERIC_PLAN, ago=90)
        ident = await w.plan(GENERIC_PLAN, source_id=source)
        row = await w.state(ident)
        assert row["source_id"] == source
        assert row["session_id"] == 301
        assert row["status"] == "pending"
        async with w.factory() as db:
            source_row = await db.get(ChatMessage, source)
            written = await db.get(ProspectiveIntent, ident)
            assert source_row.session_id == 301
            assert written.created_at > source_row.created_at
    asyncio.run(case())


@pytest.mark.parametrize("plan,done", [
    (ARRIVAL_PLAN, "我到了质检台"),
    (GENERIC_PLAN, GENERIC_DONE),
], ids=["supported-event", "generic-task"])
def test_utc_messages_cross_beijing_midnight_without_reviving(plan, done, c9b_world):
    async def case():
        w = c9b_world
        # A historical simulated tick: source before Beijing midnight, completion after it.
        w.now = (w.now - timedelta(days=1)).replace(hour=16, minute=10, second=0, microsecond=0)
        source = await w.message(plan, ago=20)
        completion = await w.message(done, ago=5)
        async with w.factory() as db:
            first = await db.get(ChatMessage, source)
            last = await db.get(ChatMessage, completion)
            assert first.created_at < last.created_at
            assert (first.created_at + timedelta(hours=8)).date() < (last.created_at + timedelta(hours=8)).date()
        await w.signal(done, ago=5)
        ident = await w.plan(plan, source_id=source)
        assert ident not in await w.candidates(), "UTC event order must survive local date boundaries and late inserts"
    asyncio.run(case())


@pytest.mark.parametrize("completed_first", [False, True], ids=["source-replay", "late-completion"])
def test_real_queue_worker_extractor_replay_contract(c9b_world, monkeypatch, tmp_path, completed_first):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extract_queue as queue, extraction_worker as worker, extractor

    monkeypatch.setattr(queue, "_QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setitem(AGENT_FLAGS, "prospective_intent_enabled", True)
    monkeypatch.setitem(AGENT_FLAGS, "curated_knowledge", False)
    calls = []

    async def fixed_llm(**kwargs):
        calls.append(kwargs)
        day = c9b_world.due.strftime("%Y-%m-%d")
        return ("USER_INFO: 无\nEVENTS: 无\nPREFERENCES: 无\nBIO: 无\nSTATUS: 无\n"
                "RELATIONSHIP: 无\nCURATED: 无\n"
                f"INTENT: {GENERIC_PLAN} | promise | {day}~{day} | 设备校验 | high")

    monkeypatch.setattr(extractor, "llm_call", fixed_llm)

    async def case():
        w = c9b_world
        source = await w.message(GENERIC_PLAN, ago=30)
        if completed_first:
            await w.signal(GENERIC_DONE)
        pair = {"source_id": source, "ts": 0, "user_message": GENERIC_PLAN, "ai_response": "实验记录已收到"}
        queue.add(301, pair)
        await worker.process_session(301, 201, 101)
        async with w.factory() as db:
            rows = (await db.execute(select(ProspectiveIntent))).scalars().all()
            assert await db.get(ProcessedExtraction, source) is not None, "Real extraction must have committed, not merely dispatched"
            assert len(rows) == 1
            ident = rows[0].id
            assert rows[0].source_message_id == source
        queue.add(301, pair)
        await worker.process_session(301, 201, 101)
        assert len(calls) == 1
        assert await w.counts() == (1, 0, 0)
        with sqlite3.connect(queue.database_path()) as db:
            assert db.execute("SELECT state FROM extraction_tasks").fetchall() == [("succeeded",)]
        if completed_first:
            assert ident not in await w.candidates(), "An acknowledged completed queue source must not revive its plan"
        else:
            assert ident in await w.candidates()
    asyncio.run(case())
