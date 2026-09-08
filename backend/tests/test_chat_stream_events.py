# -*- coding: utf-8 -*-
"""v3.4.6 审查 G1 事件流水修复单测（F-2/F-3/F-12）。

覆盖：
- F-2：TTS 实时路径事件延迟到完整性确认后批量补发（route=sse_live，与块一一对应）；
  LLM 流异常回退 / TTS consumer 完整性不足回退两条路径中，被删除的旧块不再产生事件
  （回退前逐块即发事件的孤儿问题消除），新块事件齐全；
- F-3：批量落库 _persist_ai_chunks 事件 actor_id 透传 character_id（原 None）；
- F-12：SSE 回退调用 send_and_receive_chunked 传 route="sse_fallback"；
  chunked 事件 payload route 按调用入口口径标注（默认 ws_chunk）。

（项目未装 pytest-asyncio，统一 asyncio.run；事件经 monkeypatch app.events.store.append_domain_event
 记录——streaming 内懒 import 每次调用时解析模块属性，patch 生效。）
"""
import asyncio
from datetime import datetime, timezone

from app.application import chat_service as cs
from app.application.chat import streaming
from app.events import store as st


# ── 公共工具 ──────────────────────────────────────────────

def _recorder(monkeypatch):
    """记录 append_domain_event 全部调用（绕过 flag，直接替换函数）。"""
    calls: list[dict] = []

    async def _append(event_type, aggregate_type, aggregate_id, **kw):
        calls.append({
            "event_type": event_type, "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id, **kw,
        })

    monkeypatch.setattr(st, "append_domain_event", _append)
    return calls


class _FakeSession:
    """块落库假 session：flush 分配自增 id + created_at，refresh/commit 空操作。"""

    _next_id = 200

    def __init__(self):
        self._objs = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def add(self, obj):
        self._objs.append(obj)

    async def flush(self):
        for o in self._objs:
            if getattr(o, "id", None) is None:
                _FakeSession._next_id += 1
                o.id = _FakeSession._next_id
            if getattr(o, "created_at", None) is None:
                o.created_at = datetime.now(timezone.utc).replace(tzinfo=None)

    async def refresh(self, obj):
        return None

    async def commit(self):
        return None


def _fake_factory(monkeypatch):
    monkeypatch.setattr(streaming, "async_session_factory", lambda: _FakeSession())


def _core_dict(**over):
    return {
        "final_state": {"reasoning": None, "tools_used": [], "status_update": None,
                        "should_update_memory": False, "emotional_state": ""},
        "final_text": "第一句。第二句。",
        "gen_prompt": None, "img_text": None, "cal_note_text": None, "memo_text": None,
        "streamed": True, "stream_blocks": ["第一句。", "第二句。"],
    } | over


async def _noop(*a, **k):
    return None


# ── F-2/F-3：TTS 实时完整路径事件延迟批量补发 ──────────────────

def test_tts实时完整路径_事件延迟批量补发且与块一一对应(monkeypatch):
    async def _persist_user(*a, **k):
        return (1, {"id": 1, "content": "hi"})

    async def _voice_params(_cid):
        return {}

    async def _run_core(*a, stream_tts_ctx=None, **k):
        # 实时落库 2 块（真实 _block_sink + 假 DB）
        sink = stream_tts_ctx["block_sink"]
        saved = [
            await sink(0, "第一句。", "/uploads/tts/a.mp3"),
            await sink(1, "第二句。", "/uploads/tts/b.mp3"),
        ]
        return _core_dict(stream_saved=saved)

    async def _backfill(saved, *a, **k):
        return saved

    monkeypatch.setattr("app.voice.voice_mode.load_character_voice_params", _voice_params)
    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(cs, "_run_agent_core", _run_core)
    monkeypatch.setattr(streaming, "_backfill_stream_tts_meta", _backfill)
    monkeypatch.setattr(streaming, "_push_user_notify", _noop)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)
    _fake_factory(monkeypatch)
    calls = _recorder(monkeypatch)

    async def _sink(event, payload):
        return None

    asyncio.run(cs.send_and_receive_stream(1, 1, 2, "hi", lang="zh", sink=_sink, tts=True))

    sent = [c for c in calls if c["event_type"] == "chat.message_sent"]
    assert len(sent) == 2, f"完整路径应逐块补发 2 条 message_sent，实际 {len(sent)}"
    # route=sse_live、actor_id=character_id（与 WS 路径对齐）、幂等键绑真实块 id
    assert all(c["payload"]["route"] == "sse_live" for c in sent)
    assert all(c["actor_id"] == 2 for c in sent)
    ids = [c["entity_id"] for c in sent]
    assert ids[0] != ids[1]  # 假 session 自增 id
    assert [c["idempotency_key"] for c in sent] == [
        f"chat.message_sent:chat_message:{i}" for i in ids]
    turns = [c for c in calls if c["event_type"] == "chat.turn_completed"]
    assert len(turns) == 1
    assert turns[0]["idempotency_key"] == "chat.turn_completed:1:1"


# ── F-2/F-3：TTS consumer 完整性不足 → 回退批量 ──────────────────

def test_tts_consumer不完整回退_旧块无事件新块事件齐全(monkeypatch):
    async def _persist_user(*a, **k):
        return (1, {"id": 1, "content": "hi"})

    async def _voice_params(_cid):
        return {}

    async def _run_core(*a, stream_tts_ctx=None, **k):
        # consumer 只成功落库 1 块（共 2 块）→ 完整性不足回退批量
        sink = stream_tts_ctx["block_sink"]
        saved = [await sink(0, "第一句。", "/uploads/tts/a.mp3")]
        return _core_dict(stream_saved=saved)

    deleted: list[int] = []

    async def _delete_chunks(msg_ids):
        deleted.extend(msg_ids)

    async def _synth(chunk_texts, *a, **k):
        return [f"/uploads/tts/{i}.mp3" for i in range(len(chunk_texts))]

    monkeypatch.setattr("app.voice.voice_mode.load_character_voice_params", _voice_params)
    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(cs, "_run_agent_core", _run_core)
    monkeypatch.setattr(streaming, "_delete_chunks", _delete_chunks)
    monkeypatch.setattr(streaming, "_synthesize_chunks_tts", _synth)
    monkeypatch.setattr(streaming, "_push_user_notify", _noop)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)
    _fake_factory(monkeypatch)
    calls = _recorder(monkeypatch)

    async def _sink(event, payload):
        return None

    asyncio.run(cs.send_and_receive_stream(1, 1, 2, "hi", lang="zh", sink=_sink, tts=True))

    assert len(deleted) == 1  # 半截块被删除
    old_id = deleted[0]
    sent = [c for c in calls if c["event_type"] == "chat.message_sent"]
    # 旧（被删）块不得有任何事件；新块事件齐全（走真实 _persist_ai_chunks）
    assert all(c["entity_id"] != old_id for c in sent), "回退删除的旧块不应有事件（孤儿）"
    assert len(sent) == 2
    assert all(c["payload"]["route"] == "sse_batch" for c in sent)
    # F-3：批量路径事件 actor_id=character_id（原 None）
    assert all(c["actor_id"] == 2 for c in sent)
    assert [c for c in calls if c["event_type"] == "chat.turn_completed"]


# ── F-2/F-12：LLM 流异常 → 回退 chunked ──────────────────

def test_llm流异常回退_旧块无事件_route标注sse_fallback(monkeypatch):
    async def _persist_user(*a, **k):
        return (1, {"id": 1, "content": "hi"})

    async def _voice_params(_cid):
        return {}

    async def _run_core(*a, stream_tts_ctx=None, **k):
        sink = stream_tts_ctx["block_sink"]
        await sink(0, "半截句。", "/uploads/tts/a.mp3")  # 异常前已实时落库 1 块
        raise RuntimeError("simulated LLM stream failure")

    deleted: list[int] = []

    async def _delete_chunks(msg_ids):
        deleted.extend(msg_ids)

    captured: dict = {}

    async def _chunked(*a, **k):
        captured.update(k)
        return {"chunks": [{"id": 300, "session_id": 1, "sender_type": "ai",
                            "content": "完整回退答复。", "created_at": "2026-09-09T00:00:00Z",
                            "extra_meta": None}],
                "cold_war": False, "memories_updated": False}

    monkeypatch.setattr("app.voice.voice_mode.load_character_voice_params", _voice_params)
    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(cs, "_run_agent_core", _run_core)
    monkeypatch.setattr(streaming, "_delete_chunks", _delete_chunks)
    monkeypatch.setattr(cs, "send_and_receive_chunked", _chunked)
    monkeypatch.setattr(streaming, "_push_user_notify", _noop)
    _fake_factory(monkeypatch)
    calls = _recorder(monkeypatch)

    events: list[tuple] = []

    async def _sink(event, payload):
        events.append((event, payload))

    asyncio.run(cs.send_and_receive_stream(1, 1, 2, "hi", lang="zh", sink=_sink, tts=True))

    assert len(deleted) == 1
    old_id = deleted[0]
    # 旧（被删）块不得有任何 message_sent 事件（F-2：回退路径不再产生孤儿事件）
    sent = [c for c in calls if c["event_type"] == "chat.message_sent"]
    assert all(c["entity_id"] != old_id for c in sent)
    assert not sent  # chunked 为 mock，本用例只锁流式层不再为旧块发事件
    # F-12：SSE 回退调用 chunked 时 route 如实标注 sse_fallback（原硬编码 ws_chunk 失真）
    assert captured.get("route") == "sse_fallback"
    assert [p for e, p in events if e == "reset_blocks"] == [{"reason": "stream_error_fallback"}]


# ── F-12：chunked 事件 route 按入口口径 ──────────────────

def test_chunked事件route按调用入口口径(monkeypatch):
    async def _persist_user(*a, **k):
        return (11, None)

    async def _run_core(*a, **k):
        return _core_dict(streamed=False, stream_blocks=None, final_text="回复。")

    def _split(text, emotion=""):
        return ["回复。"]

    calls: list[dict] = []

    async def _append(event_type, aggregate_type, aggregate_id, **kw):
        calls.append({"event_type": event_type, **kw})

    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(cs, "_run_agent_core", _run_core)
    monkeypatch.setattr("app.agent.nodes.split_response", _split)
    monkeypatch.setattr(cs, "_push_user_notify", _noop)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)
    monkeypatch.setattr(cs, "append_domain_event", _append)
    monkeypatch.setattr(cs, "async_session_factory", lambda: _FakeSession())

    asyncio.run(cs.send_and_receive_chunked(9, 4, 11, "hi", route="emoji"))
    sent = [c for c in calls if c["event_type"] == "chat.message_sent"]
    turns = [c for c in calls if c["event_type"] == "chat.turn_completed"]
    assert sent and all(c["payload"]["route"] == "emoji" for c in sent)
    assert turns and turns[0]["payload"]["route"] == "emoji"

    calls.clear()
    asyncio.run(cs.send_and_receive_chunked(9, 4, 11, "hi"))
    turns = [c for c in calls if c["event_type"] == "chat.turn_completed"]
    assert turns[0]["payload"]["route"] == "ws_chunk"  # 默认口径不变
