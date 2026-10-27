"""Write correctness: retain changed facts and episodes; replay only the same evidence."""
import asyncio

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory
from app.memory import service as svc
from app.memory import write
from app.models.memory import Memory


@pytest.fixture
def store(monkeypatch, tmp_path):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.models.user import User
    from app.models.character import AICharacter

    engine = clone_engine(tmp_path / "evidence.db")
    factory = make_session_factory(engine)
    hooks, vectors = [], []

    async def seed():
        async with factory() as db:
            db.add_all([User(id=1, username="evidence_one", nickname="合成用户甲"),
                        User(id=2, username="evidence_two", nickname="合成用户乙")])
            await db.flush()
            db.add_all([AICharacter(id=10, user_id=1, name="合成角色甲"),
                        AICharacter(id=20, user_id=2, name="合成角色乙")])
            await db.commit()

    async def embedding(text):
        return [0.1, 0.2]

    async def vector(**kwargs):
        vectors.append(kwargs)

    async def hooks_call(**kwargs):
        hooks.append(kwargs["memory"].id)

    async def no_op(*args, **kwargs):
        pass

    async def forbidden_similarity(*args, **kwargs):
        raise AssertionError("Similarity must not decide writes")

    for key in ("memory_admission_gate", "perception_source_tag", "actor_semantics_shadow",
                "review_plan_validity_extract"):
        monkeypatch.setitem(AGENT_FLAGS, key, False)
    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", embedding)
    monkeypatch.setattr(svc, "find_similar_memory", forbidden_similarity)
    monkeypatch.setattr(svc, "add_memory", vector)
    monkeypatch.setattr(write, "_run_created_memory_hooks", hooks_call)
    monkeypatch.setattr("app.memory.core.maybe_promote_core", no_op)
    monkeypatch.setattr("app.memory.receipt.emit_memory_receipt", lambda *args, **kwargs: None)
    asyncio.run(seed())
    yield factory, hooks, vectors
    asyncio.run(engine.dispose())


async def save(content="用户喜欢咖啡", **kwargs):
    args = dict(user_id=1, character_id=10, memory_type="preference", content=content,
                source="chat", source_id=100, speaker_type="user", speaker_id=1,
                epistemic_status="FACT", importance=3)
    args.update(kwargs)
    return await svc.save_memory(**args)


@pytest.mark.parametrize("old,new", [
    ("用户喜欢咖啡", "用户不喜欢咖啡"),
    ("用户住在北京", "用户住在上海"),
    ("用户明天去医院", "用户昨天去医院"),
    ("共同前缀" * 30 + "否", "共同前缀" * 30 + "是"),
])
def test_similarity_cannot_swallow_changed_content(store, old, new):
    async def scenario():
        first = await save(old)
        second = await save(new)  # Even within one source message, distinct claims survive.
        async with store[0]() as db:
            rows = (await db.execute(select(Memory).order_by(Memory.id))).scalars().all()
        assert first.id != second.id
        assert [row.content for row in rows] == [old, new]
        assert all(not row.review_count for row in rows)
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"source_id": 101}, {"speaker_type": "character", "speaker_id": 10},
    {"epistemic_status": "INFERRED"}, {"memory_type": "event"},
    {"source": "diary"}, {"group_id": 88}, {"scope": "global"},
    {"sub_type": "other_slot"}, {"derived_from_ids": [42]},
    {"user_id": 2, "character_id": 20, "speaker_id": 2},
])
def test_same_words_do_not_merge_different_evidence_or_scope(store, change):
    async def scenario():
        first = await save()
        second = await save(**change)
        assert first.id != second.id
    asyncio.run(scenario())


def test_exact_replay_has_no_reinforcement_or_duplicate_effects(store):
    async def scenario():
        first = await save()
        async with store[0]() as db:
            row = await db.get(Memory, first.id)
            row.is_archived = True
            row.is_locked = True
            row.status = "superseded"
            await db.commit()
        replay = await save(importance=5)
        assert replay.id == first.id
        assert replay.is_archived and replay.is_locked and replay.status == "superseded"
        assert replay.importance == first.importance
        assert replay.review_count == first.review_count
        assert store[1] == [first.id]
        assert len(store[2]) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("kwargs", [{"source_id": None}, {"source": None}, {"skip_dedup": True}])
def test_no_event_proof_or_explicit_opt_out_keeps_independent_rows(store, kwargs):
    async def scenario():
        first = await save(**kwargs)
        second = await save(**kwargs)
        assert first.id != second.id
    asyncio.run(scenario())


def test_concurrent_same_event_creates_one_row(store):
    async def scenario():
        rows = await asyncio.gather(save(), save())
        assert rows[0].id == rows[1].id
        assert len(store[1]) == 1
        assert len(store[2]) == 1
    asyncio.run(scenario())


def test_duplicate_maintenance_only_reports_and_never_archives(store, monkeypatch):
    from app.memory import dedup
    monkeypatch.setattr(dedup, "async_session_factory", store[0])

    async def scenario():
        first = await save(source_id=100)
        second = await save(source_id=101)
        suggestions = await dedup.duplicate_candidates(10)
        assert len(suggestions) == 1
        assert {suggestions[0]["left_id"], suggestions[0]["right_id"]} == {first.id, second.id}
        assert suggestions[0]["requires_review"] is True
        assert await dedup.deduplicate_memories(10) == 0
        async with store[0]() as db:
            rows = (await db.execute(select(Memory))).scalars().all()
        assert len(rows) == 2
        assert all(not row.is_archived for row in rows)
    asyncio.run(scenario())
