"""Formal projection enters genuine assembled messages with synthetic source data."""
import asyncio
import pytest
from app.flags.agent_flags import AGENT_FLAGS
from app.agent.workspace_projection import INJECT_FLAG, INJECT_HEADER, project_into_workspace
from app.agent.workspace import create_workspace
from test_projection_inject_budget_a42c import ctx_db as ctx_db, _assemble as _assemble_messages, _base_state

def _assemble(state, **values):
    _assemble_messages(state, **values)
    return state


@pytest.fixture
def projection_sources(monkeypatch):
    calls = []
    async def current(**kw):
        calls.append(("current", kw))
        return {"entries": [{"key": "job", "label": "事项", "value": "校验设备Q"}]}
    async def world(**kw):
        calls.append(("world", kw))
        return {"routes": {}}
    async def topics(character_id, user_id):
        calls.append(("topics", {"character_id": character_id, "user_id": user_id}))
        return [{"topic": "核对零件R"}]
    monkeypatch.setattr("app.memory.current_state.get_current_user_state", current)
    monkeypatch.setattr("app.events.world_state.world_state_snapshot", world)
    monkeypatch.setattr("app.agent.topic_tracker.load_active_topics_rows", topics)
    monkeypatch.setitem(AGENT_FLAGS, "cognitive_projection_shadow", False)
    return calls

def test_formal_projection_reaches_real_assembled_messages(ctx_db, monkeypatch, projection_sources):
    monkeypatch.setitem(AGENT_FLAGS, INJECT_FLAG, True)
    state = _assemble(_base_state(), workspace_projection=[])
    blocks = [m["content"] for m in state["context_messages"] if m["content"].startswith(INJECT_HEADER)]
    assert len(blocks) == 1
    assert "校验设备Q" in blocks[0] and "核对零件R" in blocks[0]
    assert len(blocks[0]) <= 400
    assert len(projection_sources) == 3
    assert all(kw == {"character_id": 13, "user_id": 1} for _, kw in projection_sources)
    assert state["context_messages"][-1]["role"] == "user"
    asyncio.run(project_into_workspace(state))
    assert len(projection_sources) == 3, "Runtime must not repeat the projection after assembly"

def test_flag_off_preserves_assembly_and_performs_no_projection_reads(ctx_db, monkeypatch, projection_sources):
    monkeypatch.setitem(AGENT_FLAGS, INJECT_FLAG, False)
    state = _assemble(_base_state(), workspace_projection=[])
    assert not any(m["content"].startswith(INJECT_HEADER) for m in state["context_messages"])
    assert projection_sources == []

def test_wrong_scope_workspace_cannot_leak_previous_values(ctx_db, monkeypatch, projection_sources):
    monkeypatch.setitem(AGENT_FLAGS, INJECT_FLAG, True)
    ws = create_workspace(user_id=900, character_id=901, session_id=902)
    ws.current_state = {"entries": [{"key": "job", "label": "事项", "value": "foreign-synthetic-secret"}]}
    state = _assemble(_base_state(workspace=ws), workspace_projection=[])
    assert "foreign-synthetic-secret" not in str(state["context_messages"])
    assert state["workspace"].user_id == 1

def test_failed_sources_clear_old_values(ctx_db, monkeypatch, projection_sources):
    monkeypatch.setitem(AGENT_FLAGS, INJECT_FLAG, True)
    state = _assemble(_base_state(), workspace_projection=[])
    async def broken(**kw):
        raise RuntimeError("synthetic read failure")
    monkeypatch.setattr("app.memory.current_state.get_current_user_state", broken)
    second = _assemble(state, workspace_projection=[])
    assert "校验设备Q" not in str(second["context_messages"])


def test_projection_yields_global_budget_before_existing_blocks(monkeypatch):
    from app.agent import context_builder as cb
    monkeypatch.setitem(AGENT_FLAGS, "context_budget_reserve", False)
    monkeypatch.setattr(cb, "TOTAL_SYSTEM_QUOTA_TOKENS", 100)
    base = {"role": "system", "content": "x" * 200}
    latest = {"role": "user", "content": "synthetic latest request"}
    messages = [dict(base), {"role": "system", "content": INJECT_HEADER + "\n- 身份：设备助理"}, dict(latest)]
    cb._apply_system_total_quota(messages)
    assert messages[0] == base and messages[-1] == latest
    assert messages[1]["content"] == ""


def test_missing_caller_does_not_read_sources(monkeypatch, projection_sources):
    from app.agent.context.projection_injection import projection_blocks
    monkeypatch.setitem(AGENT_FLAGS, INJECT_FLAG, True)
    assert asyncio.run(projection_blocks({"character_id": 13}, [])) == []
    assert projection_sources == []


def test_public_context_entry_builds_formal_projection(ctx_db, monkeypatch, projection_sources):
    from app.agent.context_builder import build_context
    monkeypatch.setitem(AGENT_FLAGS, INJECT_FLAG, True)
    monkeypatch.setattr("app.db.database.async_session_factory", ctx_db)
    monkeypatch.setattr("app.agent.context_builder.async_session_factory", ctx_db)
    state = _base_state()
    result = asyncio.run(build_context(state))
    blocks = [m["content"] for m in result["context_messages"] if m["content"].startswith(INJECT_HEADER)]
    assert len(blocks) == 1 and "校验设备Q" in blocks[0]
    assert result["context_messages"][-1]["role"] == "user"
