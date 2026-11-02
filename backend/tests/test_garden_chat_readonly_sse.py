# -*- coding: utf-8 -*-
"""Real SSE transport + real assembly + controlled model-input fake (candidate check)."""
import asyncio

from test_chat_core_controlled_tools_a50 import _install_common, _Log  # noqa: E402
from test_garden_portable_user_assembly import wired_user  # noqa: E402,F401

pytest_plugins = ["test_garden_observation_assembly"]

import pytest


@pytest.fixture(autouse=True)
def _role_db(asm_db, monkeypatch):
    import app.agent.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_garden_session_factory", asm_db.async_session_factory)

from app.agent.context.assembly import assemble_context
from app.agent.context_builder import _trim_limits
from app.application import chat_service as cs
from app.application.chat import streaming


class LiveSSE:
    calls = []
    mode = "ok"

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

    def user_whoami(self, user_key):
        LiveSSE.calls.append("whoami")
        return {"ok": True, "active": True, "scope": "readonly",
                "role_id": "role-a", "character_id": "sam", "agent": "a"}

    def user_feed(self, user_key, circle=None, limit=20, offset=0):
        LiveSSE.calls.append("feed")
        if LiveSSE.mode == "empty":
            return {"ok": True, "items": [], "scope": "readonly"}
        if LiveSSE.mode == "fail":
            from garden_forum_sdk import GardenError
            raise GardenError("unreachable", "synthetic")
        return {"ok": True, "items": [{
            "id": 9, "title": "SSE实时标题", "body": "SSE实时正文",
            "author": "SSE作者", "circle": "花园", "created_at": "2026-10-31",
        }], "scope": "readonly"}


class InputAwareAgent:
    """Controlled graph stand-in: invokes real assembly, answers from actual final input."""
    def __init__(self):
        self.seen = ""
        self.answer = ""

    async def ainvoke(self, initial_state):
        out = await assemble_context(
            initial_state, stream=True, _section_values={"relationship": ""},
            _trim=_trim_limits(True),
        )
        self.seen = "\n".join(str(m.get("content") or "") for m in out["context_messages"])
        text = "我看到了花园的SSE实时标题" if "SSE实时标题" in self.seen else "我没有看到花园内容"
        self.answer = text
        return {"ai_response": text, "reasoning": None, "streamed": True,
                "stream_blocks": [text], "character_name": "Fixture", "user_name": "User"}


def test_actual_sse_transport_and_real_assembly_feed_controlled_model(wired_user, monkeypatch):  # noqa: F811
    asm_mod, mod, doc, cfg = wired_user
    mod.GardenForum = LiveSSE
    LiveSSE.calls = []
    log = _Log()
    _install_common(monkeypatch, log)
    agent = InputAwareAgent()
    monkeypatch.setattr(cs, "agent", agent)

    async def _persist_user(*a, **k):
        return 1, {"id": 1, "content": "去花园看看"}
    async def _persist_chunks(*a, **k):
        return [{"id": 10, "session_id": 1, "sender_type": "ai",
                 "content": agent.answer, "created_at": "2026-10-31T00:00:00Z",
                 "extra_meta": None}]
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(streaming, "_persist_ai_chunks", _persist_chunks)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)
    monkeypatch.setattr(streaming, "_push_user_notify", _noop)

    events = []
    async def _sink(event, payload):
        events.append((event, payload))
    asyncio.run(cs.send_and_receive_stream(1, 1, 13, "去花园看看", sink=_sink))
    types = [e for e, _ in events]
    done = [p for e, p in events if e == "done"][-1]
    assert "SSE实时标题" in agent.seen
    assert "SSE实时标题" in done["message"]["content"]
    assert "user_message" in types and "done" in types
    assert LiveSSE.calls.count("feed") == 1


def _run_sse_case(wired_user, monkeypatch, mode):  # noqa: F811
    asm_mod, mod, doc, cfg = wired_user
    mod.GardenForum = LiveSSE
    LiveSSE.mode = mode
    LiveSSE.calls = []
    log = _Log()
    _install_common(monkeypatch, log)
    agent = InputAwareAgent()
    monkeypatch.setattr(cs, "agent", agent)

    async def _persist_user(*a, **k):
        return 1, {"id": 1, "content": "去花园看看"}
    async def _persist_chunks(*a, **k):
        return [{"id": 10, "session_id": 1, "sender_type": "ai",
                 "content": agent.answer, "created_at": "2026-10-31T00:00:00Z",
                 "extra_meta": None}]
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(streaming, "_persist_ai_chunks", _persist_chunks)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)
    monkeypatch.setattr(streaming, "_push_user_notify", _noop)

    events = []
    async def _sink(event, payload):
        events.append((event, payload))
    asyncio.run(cs.send_and_receive_stream(1, 1, 13, "去花园看看", sink=_sink))
    return agent, events


def test_actual_sse_empty_does_not_claim_content(wired_user, monkeypatch):  # noqa: F811
    agent, events = _run_sse_case(wired_user, monkeypatch, "empty")
    done = [p for e, p in events if e == "done"][-1]
    assert "没有看到" in done["message"]["content"]
    assert "SSE实时标题" not in done["message"]["content"]
    assert "SSE实时标题" not in agent.seen


def test_actual_sse_failure_does_not_claim_content(wired_user, monkeypatch):  # noqa: F811
    agent, events = _run_sse_case(wired_user, monkeypatch, "fail")
    done = [p for e, p in events if e == "done"][-1]
    assert "没有看到" in done["message"]["content"]
    assert "SSE实时标题" not in done["message"]["content"]
