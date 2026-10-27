"""Feedback cannot manufacture provenance or use AI output as counter-evidence."""
import asyncio

import pytest

from test_memory_evidence_writes import store as _evidence_store, save
from app.memory import reliability, fact_check
from app.models.memory import Memory


@pytest.fixture
def store(monkeypatch, tmp_path):
    yield from _evidence_store.__wrapped__(monkeypatch, tmp_path)


@pytest.fixture(autouse=True)
def reset_signals(monkeypatch):
    monkeypatch.setattr(reliability, "_last_signal_at", {})


@pytest.mark.parametrize("message", ["不是的", "没错"])
def test_unbound_feedback_never_searches_for_an_arbitrary_target(monkeypatch, message):
    searches = []

    async def search(*args, **kwargs):
        searches.append(True)
        return [{"id": 999}]

    monkeypatch.setattr("app.memory.search_memories", search)
    asyncio.run(reliability.process_feedback_signal(10, 1, message, "上一轮引用的是记忆8"))
    assert searches == []


def test_explicit_single_citation_targets_correct_row_and_does_not_throttle_other_corrections(store, monkeypatch):
    monkeypatch.setattr(reliability, "async_session_factory", store[0])
    corrected = []

    async def correct(mid, **kwargs):
        corrected.append(mid)

    monkeypatch.setattr(reliability, "apply_correction", correct)

    async def scenario():
        first = await save(source_id=100)
        second = await save(source_id=101)
        for mid in (first.id, second.id):
            await reliability.process_feedback_signal(10, 1, "不是的", cited_memory_ids=[mid])
        await reliability.process_feedback_signal(10, 1, "不是的", cited_memory_ids=[first.id])
        assert corrected == [first.id, second.id]
    asyncio.run(scenario())


def test_ambiguous_or_unauthorized_citations_cannot_mutate(store, monkeypatch):
    monkeypatch.setattr(reliability, "async_session_factory", store[0])
    corrected = []

    async def correct(mid, **kwargs):
        corrected.append(mid)

    monkeypatch.setattr(reliability, "apply_correction", correct)

    async def scenario():
        first = await save()
        second = await save(source_id=101)
        await reliability.process_feedback_signal(10, 1, "不是的", cited_memory_ids=[first.id, second.id])
        await reliability.process_feedback_signal(20, 2, "不是的", cited_memory_ids=[first.id])
        await reliability.process_feedback_signal(10, 2, "不是的", cited_memory_ids=[first.id])
        assert corrected == []
    asyncio.run(scenario())


def test_model_conflict_cannot_downgrade_correct_memory(store, monkeypatch):
    monkeypatch.setattr(reliability, "async_session_factory", store[0])

    async def scenario():
        memory = await save()
        await reliability.apply_correction(memory.id, source="fact_check")
        async with store[0]() as db:
            current = await db.get(Memory, memory.id)
        assert current.epistemic_status == "FACT"
        assert current.contradiction_count == 0
        assert current.status == "active"
    asyncio.run(scenario())


def test_fact_checker_records_response_conflict_without_correcting_memory(monkeypatch):
    events = []

    async def enabled(*args):
        return True

    async def search(**kwargs):
        return [{"id": 8, "content": "用户住在上海"}]

    async def llm(**kwargs):
        return '{"contradictions":[{"memory_id":8,"reason":"回复用了旧地址"}]}'

    async def forbidden(*args, **kwargs):
        raise AssertionError("AI output must not mutate memory")

    monkeypatch.setattr(fact_check, "_memory_v2_enabled", enabled)
    monkeypatch.setattr(fact_check, "_check_limit", lambda *args: True)
    monkeypatch.setattr("app.memory.search_memories", search)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", llm)
    monkeypatch.setattr(reliability, "apply_correction", forbidden)
    monkeypatch.setattr("app.memory.observability.obs_event", lambda *args, **kwargs: events.append(args))
    asyncio.run(fact_check.async_fact_check(10, 1, "请告诉我你记得我现在居住在哪个城市", "你住在北京"))
    assert events == [(10, "response_memory_conflict", {"memory_id": 8})]


def test_retrieval_does_not_reinforce_candidates(monkeypatch):
    from app.agent import nodes
    calls = []

    async def search(**kwargs):
        return [{"id": 8, "content": "合成证据"}]

    async def reinforce(*args, **kwargs):
        calls.append(True)

    monkeypatch.setattr("app.memory.search_memories", search)
    monkeypatch.setattr("app.memory.service.reinforce_memories", reinforce)
    state = {"character_id": 10, "user_id": 1, "user_message": "我住在哪里？"}
    asyncio.run(nodes.retrieve_memories(state))
    assert state["retrieved_memories"][0]["id"] == 8
    assert calls == []


def test_request_dedup_never_hides_followups_or_new_versions():
    from app.agent.context import section_memories as section
    first = {"id": 8, "content": "旧证据"}
    assert section._filter_recently_injected(10, [first, first]) == [first]
    section._mark_memories_injected(10, [first])
    updated = {"id": 8, "content": "编辑后的新证据"}
    assert section._filter_recently_injected(10, [updated]) == [updated]
    assert section._filter_recently_injected(10, [updated]) == [updated]
