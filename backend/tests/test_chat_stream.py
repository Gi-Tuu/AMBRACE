# -*- coding: utf-8 -*-
"""SSE 真流式链路测试：节点流式生成 / 流式端点事件流 / 错误回退。

采用与 test_doubao_audit_fixes 相同的「独立 FastAPI 实例 + 依赖覆盖 + 边界 mock」策略，
不 import app.main（避免模块级单实例锁占用 8766 端口）。
"""
import asyncio
import json

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.agent import nodes
from app.api import chat as chat_api
from app.auth.deps import get_current_user_id
from app.services.chat import streaming


async def _noop_sink(event, payload):
    return None


# ── 节点流式生成（_stream_generate）───────────────────────────────

def test_stream_generate_pushes_deltas_and_collects_blocks(monkeypatch):
    """流式生成：逐 delta 推送展示文本，同时按句子边界收集语义块。"""
    async def _fake_stream(**kw):
        yield "你好。今天"
        yield "天气真好。我们出去玩吧。"
    monkeypatch.setattr(nodes, "chat_completion_stream", _fake_stream)

    state = {"context_messages": [{"role": "user", "content": "hi"}],
             "emotional_state": "", "temperature": 0.8}
    events: list[tuple] = []

    async def _sink(event, payload):
        events.append((event, payload))

    raw = asyncio.run(nodes._stream_generate(state, None, _sink))

    assert raw == "你好。今天天气真好。我们出去玩吧。"
    assert state["stream_blocks"] == ["你好。今天天气真好。我们出去玩吧。"]
    assert state["stream_display"] == "你好。今天天气真好。我们出去玩吧。"
    # delta 事件拼接 == 语义块正文（打字机与落库一条线）
    delta_text = "".join(p["text"] for e, p in events if e == "delta")
    assert delta_text == "你好。今天天气真好。我们出去玩吧。"


def test_stream_generate_raises_on_llm_error(monkeypatch):
    """LLM 流式异常直接上抛，供服务层回退非流式。"""
    async def _boom(**kw):
        yield "部分"
        raise RuntimeError("网络中断")
    monkeypatch.setattr(nodes, "chat_completion_stream", _boom)

    state = {"context_messages": [], "emotional_state": "", "temperature": 0.8}
    with pytest.raises(RuntimeError):
        asyncio.run(nodes._stream_generate(state, None, _noop_sink))


def test_generate_response_falls_back_when_reasoning_level_2(monkeypatch):
    """深度思考（reasoning_level=2）回退非流式，即使注入了 stream_sink。"""
    seen = {}

    async def _non_stream(**kw):
        seen["non_stream"] = True
        return "非流式回复", ""  # include_reasoning=True 返回 (content, reasoning)
    monkeypatch.setattr(nodes, "chat_completion", _non_stream)
    monkeypatch.setattr(nodes, "chat_completion_stream", _boom_generator)

    async def _get_cfg(user_id):
        return None
    monkeypatch.setattr("app.agent.llm_client.get_user_llm_config", _get_cfg)
    monkeypatch.setattr(nodes, "_has_after_generate_hook", lambda: False)

    state = {"context_messages": [{"role": "user", "content": "hi"}],
             "emotional_state": "", "temperature": 0.8,
             "reasoning_level": 2, "stream_sink": _noop_sink,
             "user_id": 1, "character_id": 2, "ai_response": "",
             "user_message": "hi", "session_id": 1, "new_memories": [],
             "skip_memory_save": True}
    out = asyncio.run(nodes.generate_response(state))
    assert seen.get("non_stream") is True
    assert out["streamed"] is False


def _boom_generator(**kw):
    async def _gen():
        raise AssertionError("流式不应被调用")
    return _gen()


# ── 流式端点事件流（SSE 框架）─────────────────────────────────────

class _DummyDB:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False


def _dummy_async_session():
    return _DummyDB()


class _FakeOwnedSession:
    user_id = 1
    character_id = 2


def _make_client(user_id: int = 1) -> TestClient:
    app = FastAPI()
    app.include_router(chat_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[chat_api.get_db] = lambda: _dummy_async_session()
    return TestClient(app)


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def test_stream_endpoint_emits_delta_block_done(monkeypatch):
    """端点按序输出 user_message/delta/block/done 事件。"""
    async def _fake_session(*a, **k):
        return _FakeOwnedSession()

    scripted = [
        ("user_message", {"id": 1, "content": "hi"}),
        ("delta", {"text": "你好"}),
        ("delta", {"text": "。"}),
        ("block", {"index": 0, "id": 10, "session_id": 1, "sender_type": "ai",
                   "content": "你好。", "created_at": "2026-08-24T00:00:00Z", "extra_meta": None}),
        ("done", {"message": {"content": "你好。"}, "blocks": [], "memories_updated": False}),
    ]

    async def _fake_stream(*a, sink=None, **k):
        for ev, payload in scripted:
            await sink(ev, payload)

    monkeypatch.setattr(chat_api, "get_owned_session", _fake_session)
    monkeypatch.setattr(chat_api, "send_and_receive_stream", _fake_stream)

    r = _make_client().post("/api/v1/chat/sessions/1/messages/stream",
                            json={"content": "hi"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert types == ["user_message", "delta", "delta", "block", "done"]
    assert events[1]["text"] == "你好"
    assert events[3]["id"] == 10
    assert events[3]["content"] == "你好。"
    assert events[4]["message"]["content"] == "你好。"


def test_stream_endpoint_error_on_exception(monkeypatch):
    """send_and_receive_stream 抛异常时，端点仍产出 error 事件并收尾。"""
    async def _fake_session(*a, **k):
        return _FakeOwnedSession()

    async def _fake_stream(*a, **k):
        raise RuntimeError("LLM 挂了")

    monkeypatch.setattr(chat_api, "get_owned_session", _fake_session)
    monkeypatch.setattr(chat_api, "send_and_receive_stream", _fake_stream)

    r = _make_client().post("/api/v1/chat/sessions/1/messages/stream",
                            json={"content": "hi"})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert [e["type"] for e in events] == ["error"]


# ── send_and_receive_stream 业务回退逻辑 ─────────────────────────

def _fake_core(**over):
    return {
        "final_state": {"reasoning": None, "tools_used": [], "status_update": None,
                        "should_update_memory": False, "emotional_state": ""},
        "final_text": "你好。今天天气真好。",
        "gen_prompt": None, "img_text": None, "cal_note_text": None, "memo_text": None,
        "streamed": True, "stream_blocks": ["你好。今天天气真好。"],
    } | over


def test_send_and_receive_stream_success(monkeypatch):
    """流式成功：先推送块事件，再推送 done；落库走 _persist_ai_chunks。"""
    async def _persist_user(*a, **k):
        return (1, {"id": 1, "content": "hi"})
    async def _run_core(*a, **k):
        return _fake_core()
    saved = [{"id": 10, "session_id": 1, "sender_type": "ai", "content": "你好。今天天气真好。",
              "created_at": "2026-08-24T00:00:00Z", "extra_meta": None}]
    async def _persist_chunks(*a, **k):
        return saved
    async def _noop(*a, **k):
        return None

    from app.services import chat_service as cs
    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(cs, "_run_agent_core", _run_core)
    monkeypatch.setattr(streaming, "_persist_ai_chunks", _persist_chunks)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)
    monkeypatch.setattr(streaming, "_push_user_notify", _noop)

    events: list[tuple] = []
    async def _sink(event, payload):
        events.append((event, payload))

    asyncio.run(cs.send_and_receive_stream(1, 1, 2, "hi", lang="zh", sink=_sink))
    types = [e for e, _ in events]
    assert "user_message" in types
    assert "block" in types
    assert types[-1] == "done"
    block_evt = [p for e, p in events if e == "block"][0]
    assert block_evt["content"] == "你好。今天天气真好。"
    done_evt = [p for e, p in events if e == "done"][0]
    assert done_evt["message"]["content"] == "你好。今天天气真好。"


def test_send_and_receive_stream_falls_back_to_chunked_on_error(monkeypatch):
    """LLM 流式异常：推 error 后回退非流式 chunked 并继续推 block/done。"""
    async def _persist_user(*a, **k):
        return (1, {"id": 1, "content": "hi"})
    async def _run_core(*a, **k):
        raise RuntimeError("流式中断")
    async def _chunked(*a, **k):
        return {"chunks": [{"id": 20, "session_id": 1, "sender_type": "ai",
                            "content": "回退回复。", "created_at": "2026-08-24T00:00:00Z",
                            "extra_meta": None}],
                "memories_updated": False, "cold_war": False}
    async def _noop(*a, **k):
        return None

    from app.services import chat_service as cs
    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(cs, "_run_agent_core", _run_core)
    monkeypatch.setattr(cs, "send_and_receive_chunked", _chunked)
    monkeypatch.setattr(streaming, "_push_user_notify", _noop)

    events: list[tuple] = []
    async def _sink(event, payload):
        events.append((event, payload))

    asyncio.run(cs.send_and_receive_stream(1, 1, 2, "hi", lang="zh", sink=_sink))
    types = [e for e, _ in events]
    assert "error" in types
    assert types[-1] == "done"
    assert any(e == "block" for e in types)


def test_send_and_receive_stream_cold_war(monkeypatch):
    """冷战拦截：sink 收到 cold_war 事件。"""
    async def _persist_user(*a, **k):
        return (1, {"id": 1, "content": "hi"})
    # 流式路径 _run_agent_core 返回 None → 回退 chunked 也 cold_war
    async def _run_core(*a, **k):
        return None
    async def _chunked(*a, **k):
        return {"chunks": [], "memories_updated": False, "cold_war": True}

    from app.services import chat_service as cs
    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(cs, "_run_agent_core", _run_core)
    monkeypatch.setattr(cs, "send_and_receive_chunked", _chunked)

    events: list[tuple] = []
    async def _sink(event, payload):
        events.append((event, payload))

    asyncio.run(cs.send_and_receive_stream(1, 1, 2, "hi", lang="zh", sink=_sink))
    assert any(e == "cold_war" for e, _ in events)
