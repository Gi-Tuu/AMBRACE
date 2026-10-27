"""Read boundaries and scheduled expiration, with real temporary SQLite."""
import asyncio

import pytest

from test_memory_evidence_writes import store as _evidence_store, save
from app.models.memory import Memory


@pytest.fixture
def store(monkeypatch, tmp_path):
    yield from _evidence_store.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("read_only", [True, False])
def test_listing_hides_expired_rows_but_only_maintenance_archives_them(store, monkeypatch, read_only):
    from datetime import timedelta
    from app.memory import maintain, decay, service
    from app.utils.timeutil import now_naive_utc
    vectors = []

    async def delete_vector(mid):
        vectors.append(mid)

    monkeypatch.setattr(service, "delete_memory_vector", delete_vector)
    monkeypatch.setattr(decay, "async_session_factory", store[0])

    async def scenario():
        memory = await save()
        async with store[0]() as db:
            row = await db.get(Memory, memory.id)
            row.delete_at = now_naive_utc() - timedelta(days=1)
            await db.commit()
        listing, total = await maintain.list_memories(user_id=1, character_id=10, read_only=read_only)
        assert listing == [] and total == 0
        async with store[0]() as db:
            row = await db.get(Memory, memory.id)
            assert not row.is_archived
        assert vectors == []
        assert await decay.run_memory_decay() == 1
        async with store[0]() as db:
            row = await db.get(Memory, memory.id)
            assert row.is_archived
        assert vectors == [memory.id]
    asyncio.run(scenario())


@pytest.mark.parametrize("current_view_flag", [False, True])
def test_life_overlay_cannot_reintroduce_archived_or_old_status_rows(store, monkeypatch, current_view_flag):
    from app.agent.context import section_overlay
    from app.flags.agent_flags import AGENT_FLAGS
    from app.models.character import CharacterState
    monkeypatch.setitem(AGENT_FLAGS, "current_view_filter", current_view_flag)
    monkeypatch.setattr("app.db.database.async_session_factory", store[0])
    monkeypatch.setattr("random.random", lambda: 0.0)

    async def scenario():
        live = await save("合成现行生活记录", source="life", source_id=101)
        archived = await save("合成归档生活记录", source="life", source_id=102)
        superseded = await save("合成旧版生活记录", source="life", source_id=103)
        stale = await save("合成失效生活记录", source="life", source_id=104)
        async with store[0]() as db:
            db.add(CharacterState(character_id=10, trust=70))
            (await db.get(Memory, archived.id)).is_archived = True
            (await db.get(Memory, superseded.id)).status = "superseded"
            (await db.get(Memory, stale.id)).status = "stale"
            await db.commit()
        result = await section_overlay.life_share_section({"user_id": 1, "character_id": 10}, {})
        assert len(result) == 1
        assert live.content in result[0]
        assert archived.content not in result[0]
        assert superseded.content not in result[0]
        assert stale.content not in result[0]
    asyncio.run(scenario())


def test_legacy_life_query_cannot_bypass_archive_or_status_with_flags_off(store):
    import ast
    import inspect
    from sqlalchemy import select, true
    from app.agent.context import assembly

    # Execute the actual legacy WHERE expression, avoiding the unrelated context
    # sections. The old optional status helper is deliberately an always-true stub.
    tree = ast.parse(inspect.getsource(assembly.assemble_context))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "where"
             and any("_MemL.source" in ast.unparse(arg) for arg in node.args)]
    assert len(calls) == 1
    query = eval(compile(ast.Expression(calls[0]), "legacy-life-query", "eval"), {
        "select": select, "_MemL": Memory,
        "current_facts_status_clause": true,
        "state": {"user_id": 1, "character_id": 10},
    })

    async def scenario():
        live = await save("合成现行生活", source="life", source_id=201)
        old = await save("合成旧版生活", source="life", source_id=202)
        archived = await save("合成归档生活", source="life", source_id=203)
        async with store[0]() as db:
            (await db.get(Memory, old.id)).status = "superseded"
            (await db.get(Memory, archived.id)).is_archived = True
            await db.commit()
            rows = (await db.execute(query)).scalars().all()
            assert [row.id for row in rows] == [live.id]
    asyncio.run(scenario())
