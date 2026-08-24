# -*- coding: utf-8 -*-
"""逐句 TTS 接 SSE 真流式单测（AMBRACE #57-B）。

覆盖：
- _assemble_chunk_meta：首块 reasoning/tools/tts、末块 status/cal/memo、中间块 tts 的组装规则；
- 节点流式 _stream_generate_tts：逐句合成（mock）→ 实时落库（block_sink）→ 推 block（带 tts_url）；
- 服务层 send_and_receive_stream(tts=True)：走实时 stream_saved 路径（回填首/末块），
  不再重推 block（避免与实时推送重复）。
"""
import asyncio

from app.agent import nodes
from app.services import chat_service as cs


def test_assemble_chunk_meta_rules():
    """_assemble_chunk_meta：首块 reasoning/tools/tts；末块 status/cal/memo；中间块仅 tts。"""
    final_state = {
        "reasoning": "我在思考", "tools_used": ["扩展：笔记"],
        "status_update": "开心", "should_update_memory": False,
    }
    # 首块（idx 0，共 3 块）：reasoning + tools（含语音回复与扩展）+ tts
    meta0 = cs._assemble_chunk_meta(
        0, 3, final_state, "生图", ["识图"], None, None, "/uploads/tts/0.mp3", tts=True,
    )
    assert meta0["reasoning"] == "我在思考"
    assert "语音回复" in meta0["tools"]
    assert "生图" in meta0["tools"]
    assert "识图" in meta0["tools"]
    assert "扩展：笔记" in meta0["tools"]
    assert meta0["tts"] == {"url": "/uploads/tts/0.mp3"}
    assert "status_update" not in meta0
    # 中间块（idx 1）：仅 tts
    meta1 = cs._assemble_chunk_meta(1, 3, final_state, None, None, None, None, "/uploads/tts/1.mp3", tts=True)
    assert meta1 == {"tts": {"url": "/uploads/tts/1.mp3"}}
    # 末块（idx 2）：status/cal/memo + tts
    meta2 = cs._assemble_chunk_meta(2, 3, final_state, None, None, "约会", "买牛奶", "/uploads/tts/2.mp3", tts=True)
    assert meta2["status_update"] == "开心"
    assert meta2["cal_note"] == "约会"
    assert meta2["memo"] == "买牛奶"
    assert meta2["tts"] == {"url": "/uploads/tts/2.mp3"}
    # 非 tts 且无首/末信息时返回 None
    assert cs._assemble_chunk_meta(1, 3, {"tools_used": []}, None, None, None, None, None, False) is None


def test_assemble_chunk_meta_keeps_legacy_non_tts():
    """非 tts 路径与旧 _persist_ai_chunks 规则一致：无「语音回复」、无 tts。"""
    final_state = {"reasoning": "想", "tools_used": ["识图"], "status_update": "好"}
    meta = cs._assemble_chunk_meta(0, 1, final_state, None, None, None, None, None, False)
    assert meta["reasoning"] == "想"
    assert meta["tools"] == ["识图"]
    assert "语音回复" not in meta["tools"]
    assert "tts" not in meta
    assert meta["status_update"] == "好"


def test_stream_generate_tts_pushes_blocks_with_tts_url(monkeypatch):
    """节点流式 tts：逐句合成（mock）→ block_sink 落库 → 推 block 带 tts_url，同时保留 delta。"""
    async def _fake_stream(**kw):
        yield "你好。今天天气真好。我们一起出去玩吧。"
        yield "晚上吃什么呢？"
    monkeypatch.setattr(nodes, "chat_completion_stream", _fake_stream)

    async def _fake_synth(text, state):
        return f"/uploads/tts/{len(text)}.mp3"
    monkeypatch.setattr(nodes, "_synth_stream_block", _fake_synth)

    saved: list[dict] = []

    async def _block_sink(index, text, url):
        item = {
            "id": 100 + index, "session_id": 1, "sender_type": "ai",
            "content": text, "created_at": "2026-08-24T00:00:00Z", "extra_meta": None,
        }
        if url:
            item["tts_url"] = url
        saved.append(item)
        return item

    events: list[tuple] = []

    async def _sink(event, payload):
        events.append((event, payload))

    state = {
        "context_messages": [{"role": "user", "content": "hi"}],
        "emotional_state": "", "temperature": 0.8,
        "tts": True, "block_sink": _block_sink,
        "voice_params": {}, "tts_subdir": "1", "user_id": 1,
    }
    raw = asyncio.run(nodes._stream_generate(state, None, _sink))

    assert raw == "你好。今天天气真好。我们一起出去玩吧。晚上吃什么呢？"
    # 流水线消费端按块顺序落库 + 推 block（带 tts_url）
    block_events = [p for e, p in events if e == "block"]
    assert len(block_events) == 2
    assert block_events[0]["content"] == "你好。今天天气真好。我们一起出去玩吧。"
    assert block_events[0]["tts_url"] == f"/uploads/tts/{len('你好。今天天气真好。我们一起出去玩吧。')}.mp3"
    assert block_events[1]["content"] == "晚上吃什么呢？"
    # delta 仍照常推送（打字机不被逐句合成阻塞）
    assert any(e == "delta" for e, _ in events)
    # 落库顺序与 index 单调
    assert [b["id"] for b in block_events] == [100, 101]
    assert state["stream_saved"] == saved
    assert state["stream_blocks"] == ["你好。今天天气真好。我们一起出去玩吧。", "晚上吃什么呢？"]


def test_stream_generate_non_tts_no_block_sink(monkeypatch):
    """非 tts 且无 block_sink：_stream_generate 行为与现状一致（只推 delta，不推 block）。"""
    async def _fake_stream(**kw):
        yield "你好。今天天气真好。"
    monkeypatch.setattr(nodes, "chat_completion_stream", _fake_stream)

    events: list[tuple] = []

    async def _sink(event, payload):
        events.append((event, payload))

    state = {"context_messages": [{"role": "user", "content": "hi"}],
             "emotional_state": "", "temperature": 0.8}
    raw = asyncio.run(nodes._stream_generate(state, None, _sink))
    assert raw == "你好。今天天气真好。"
    assert not any(e == "block" for e, _ in events)
    assert state["stream_saved"] == []


def test_send_and_receive_stream_tts_uses_stream_saved(monkeypatch):
    """服务层 tts=True：实时路径回填首/末块后推 done，不重复推 block。"""
    async def _persist_user(*a, **k):
        return (1, {"id": 1, "content": "hi"})

    stream_saved = [
        {"id": 10, "session_id": 1, "sender_type": "ai", "content": "第一句。",
         "created_at": "2026-08-24T00:00:00Z", "extra_meta": None, "tts_url": "/uploads/tts/a.mp3"},
        {"id": 11, "session_id": 1, "sender_type": "ai", "content": "第二句。",
         "created_at": "2026-08-24T00:00:00Z", "extra_meta": None, "tts_url": "/uploads/tts/b.mp3"},
    ]

    async def _run_core(*a, **k):
        return {
            "final_state": {"reasoning": None, "tools_used": [], "status_update": None,
                            "should_update_memory": False, "emotional_state": ""},
            "final_text": "第一句。第二句。",
            "gen_prompt": None, "img_text": None, "cal_note_text": None, "memo_text": None,
            "streamed": True, "stream_blocks": ["第一句。", "第二句。"],
            "stream_saved": stream_saved,
        }

    async def _backfill(*a, **k):
        return a[0]

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(cs, "_run_agent_core", _run_core)
    monkeypatch.setattr(cs, "_backfill_stream_tts_meta", _backfill)
    monkeypatch.setattr(cs, "_push_user_notify", _noop)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)

    events: list[tuple] = []

    async def _sink(event, payload):
        events.append((event, payload))

    asyncio.run(cs.send_and_receive_stream(1, 1, 2, "hi", lang="zh", sink=_sink, tts=True))
    types = [e for e, _ in events]
    # 实时路径：块已由节点流水线推送，这里只回填 + done，不重复推 block
    assert "block" not in types
    assert types[-1] == "done"
    done_evt = [p for e, p in events if e == "done"][0]
    assert done_evt["message"]["content"] == "第一句。第二句。"
    assert done_evt["blocks"] == stream_saved
