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
from app.application import chat_service as cs
from app.application.chat import streaming


def test_assemble_chunk_meta_rules():
    """_assemble_chunk_meta：首块 reasoning/tools/tts；末块 status/cal/memo；中间块仅 tts。"""
    final_state = {
        "reasoning": "我在思考", "tools_used": ["扩展：笔记"],
        "status_update": "开心", "should_update_memory": False,
    }
    # 首块（idx 0，共 3 块）：reasoning + tools（含语音回复与扩展）+ tts
    meta0 = streaming._assemble_chunk_meta(
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
    meta1 = streaming._assemble_chunk_meta(1, 3, final_state, None, None, None, None, "/uploads/tts/1.mp3", tts=True)
    assert meta1 == {"tts": {"url": "/uploads/tts/1.mp3"}}
    # 末块（idx 2）：status/cal/memo + tts
    meta2 = streaming._assemble_chunk_meta(2, 3, final_state, None, None, "约会", "买牛奶", "/uploads/tts/2.mp3", tts=True)
    assert meta2["status_update"] == "开心"
    assert meta2["cal_note"] == "约会"
    assert meta2["memo"] == "买牛奶"
    assert meta2["tts"] == {"url": "/uploads/tts/2.mp3"}
    # 非 tts 且无首/末信息时返回 None
    assert streaming._assemble_chunk_meta(1, 3, {"tools_used": []}, None, None, None, None, None, False) is None


def test_assemble_chunk_meta_keeps_legacy_non_tts():
    """非 tts 路径与旧 _persist_ai_chunks 规则一致：无「语音回复」、无 tts。"""
    final_state = {"reasoning": "想", "tools_used": ["识图"], "status_update": "好"}
    meta = streaming._assemble_chunk_meta(0, 1, final_state, None, None, None, None, None, False)
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
    monkeypatch.setattr(streaming, "_backfill_stream_tts_meta", _backfill)
    monkeypatch.setattr(streaming, "_push_user_notify", _noop)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)

    events: list[tuple] = []

    async def _sink(event, payload):
        events.append((event, payload))

    asyncio.run(cs.send_and_receive_stream(1, 1, 2, "hi", lang="zh", sink=_sink, tts=True))
    types = [e for e, _ in events]
    # 实时路径：块已由节点流水线推送，这里只回填 + done，不重复推 block
    assert "block" not in types
    # P2-NEW：正常 TTS 实时路径不发送 reset_blocks（仅回退批量路径发送）
    assert "reset_blocks" not in types
    assert types[-1] == "done"
    done_evt = [p for e, p in events if e == "done"][0]
    assert done_evt["message"]["content"] == "第一句。第二句。"
    assert done_evt["blocks"] == stream_saved


def test_stream_generate_tts_cancel_producer_no_consumer_leak(monkeypatch):
    """P2-A：producer 被 CancelledError 取消（客户端断开）时，consumer 也被取消，不永久泄漏。

    复现缺陷：_stream_generate_tts 只 try/except Exception（不含 BaseException），一旦
    CancelledError 传播到 producer，consumer_task.cancel() 永不执行 → consumer 永久卡在
    queue.get()。本测试以 asyncio.all_tasks() 断言取消 producer 后无残留 consumer 任务。
    """
    import asyncio

    produced = asyncio.Event()

    async def _fake_stream(**kw):
        yield "你好。今天天气真好。"
        produced.set()
        # 让 producer 停在 await 上等待下一块，模拟仍联网；随后被任务取消打断
        await asyncio.Event().wait()  # 永不触发，除非任务被取消

    monkeypatch.setattr(nodes, "chat_completion_stream", _fake_stream)

    async def _fake_synth(text, state):
        return f"/uploads/tts/{len(text)}.mp3"
    monkeypatch.setattr(nodes, "_synth_stream_block", _fake_synth)

    async def _block_sink(index, text, url):
        item = {
            "id": 100 + index, "session_id": 1, "sender_type": "ai",
            "content": text, "created_at": "2026-08-24T00:00:00Z", "extra_meta": None,
        }
        if url:
            item["tts_url"] = url
        return item

    async def _sink(event, payload):
        pass

    state = {
        "context_messages": [{"role": "user", "content": "hi"}],
        "emotional_state": "", "temperature": 0.8,
        "tts": True, "block_sink": _block_sink,
        "voice_params": {}, "tts_subdir": "1", "user_id": 1,
    }

    async def _main():
        loop = asyncio.get_running_loop()
        baseline = set(asyncio.all_tasks())
        producer = loop.create_task(nodes._stream_generate(state, None, _sink))
        await produced.wait()
        await asyncio.sleep(0.05)  # 让 consumer 消费掉入队块（进入下一轮 queue.get 阻塞）
        producer.cancel()  # 模拟客户端断开 → producer 被取消
        try:
            await producer
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.05)  # 等 finally 的 wait_for(consumer_task, 1.0) 完成清理
        leaked = [t for t in asyncio.all_tasks() if t not in baseline and not t.done()]
        assert leaked == [], f"consumer task leaked after producer cancel: {leaked}"

    asyncio.run(_main())


def test_send_and_receive_stream_tts_llm_error_sends_reset_blocks(monkeypatch):
    """V2-3：TTS 模式下 LLM 流式异常回退 chunked 前先发 reset_blocks。

    复现缺陷：TTS 半截块已通过 _block_sink 实时落库并推送，LLM 流式异常后回退 chunked 生成
    全新内容/全新 ID 的块，旧半截块 + 新块同时在客户端显示。修复后应先删旧块（_delete_chunks）、
    再发 reset_blocks（reason="stream_error_fallback"），客户端清除本轮旧 AI 块后再接收新块。
    """
    import datetime as _dt

    async def _persist_user(*a, **k):
        return (1, {"id": 1, "content": "hi"})

    deleted: list[int] = []

    async def _delete_chunks(msg_ids):
        deleted.extend(msg_ids)

    # 假 DB session：_block_sink 会 add/flush/refresh/commit；flush 时补 id/created_at。
    # 每次 `async with async_session_factory()` 新建 session，但共享同一个 id 自增器，
    # 保证两个块拿到不同的 id（200、201）。
    _idgen = {"next": 200}

    class _FakeSession:
        def __init__(self):
            self._pending = []

        def add(self, obj):
            self._pending.append(obj)

        async def flush(self):
            for obj in self._pending:
                if getattr(obj, "id", None) is None:
                    obj.id = _idgen["next"]
                    _idgen["next"] += 1
                if getattr(obj, "created_at", None) is None:
                    obj.created_at = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)

        async def refresh(self, obj):
            pass

        async def commit(self):
            self._pending.clear()

    class _FakeCtx:
        def __init__(self):
            self._s = _FakeSession()

        async def __aenter__(self):
            return self._s

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(streaming, "async_session_factory", lambda: _FakeCtx())

    async def _run_core(session_id, user_id, character_id, content, lang, user_msg_id,
                        stream_sink=None, tts=False, stream_tts_ctx=None, reply_delay=False):
        # 实时已落库 2 个块（填充 tts_saved），随后 LLM 流式异常
        block_sink = stream_tts_ctx["block_sink"]
        await block_sink(0, "第一句。", "/uploads/tts/a.mp3")
        await block_sink(1, "第二句。", "/uploads/tts/b.mp3")
        raise RuntimeError("simulated LLM stream failure")

    chunk_res = {
        "chunks": [
            {"id": 300, "session_id": 1, "sender_type": "ai", "content": "完整回退答复。",
             "created_at": "2026-08-24T00:00:00Z", "extra_meta": None},
        ],
        "cold_war": False,
        "memories_updated": False,
    }

    async def _chunked(*a, **k):
        return chunk_res

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(cs, "_run_agent_core", _run_core)
    monkeypatch.setattr(streaming, "_delete_chunks", _delete_chunks)
    monkeypatch.setattr(cs, "send_and_receive_chunked", _chunked)
    monkeypatch.setattr(streaming, "_push_user_notify", _noop)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)

    events: list[tuple] = []

    async def _sink(event, payload):
        events.append((event, payload))

    asyncio.run(cs.send_and_receive_stream(1, 1, 2, "hi", lang="zh", sink=_sink, tts=True))

    # 半截块被删除（id 来自假 session 的 200/201）
    assert deleted == [200, 201]
    event_types = [e for e, _ in events]
    # V2-3：先发 reset_blocks（reason=stream_error_fallback），再推新 block
    reset_evts = [p for e, p in events if e == "reset_blocks"]
    assert reset_evts == [{"reason": "stream_error_fallback"}]
    assert event_types.index("reset_blocks") < event_types.index("block")
    # error 事件在回退前发出，最后以 done 收尾
    assert "error" in event_types
    assert event_types[-1] == "done"
    done_evt = [p for e, p in events if e == "done"][0]
    assert done_evt["message"]["content"] == "完整回退答复。"


def test_send_and_receive_stream_tts_partial_falls_back_to_batch(monkeypatch):
    """P2-B：TTS consumer 中途死亡（stream_saved < stream_blocks）时回退批量落库。

    复现缺陷：consumer 存了 N 块后死亡 → stream_saved 非空 → 旧代码只回填 N 块并 return，
    stream_blocks 中剩余块永不落库。修复后应删除半截块、落到下方批量路径全量落库，
    使该轮 DB 块数与 stream_blocks 一致、done.blocks 与全文对应（历史不缺失）。
    """
    async def _persist_user(*a, **k):
        return (1, {"id": 1, "content": "hi"})

    # consumer 只成功保存前 2 块就死亡；stream_blocks 实际有 3 块
    partial_saved = [
        {"id": 10, "session_id": 1, "sender_type": "ai", "content": "第一句。",
         "created_at": "2026-08-24T00:00:00Z", "extra_meta": None, "tts_url": "/uploads/tts/a.mp3"},
        {"id": 11, "session_id": 1, "sender_type": "ai", "content": "第二句。",
         "created_at": "2026-08-24T00:00:00Z", "extra_meta": None, "tts_url": "/uploads/tts/b.mp3"},
    ]
    all_blocks = ["第一句。", "第二句。", "第三句。"]

    async def _run_core(*a, **k):
        return {
            "final_state": {"reasoning": None, "tools_used": [], "status_update": None,
                            "should_update_memory": False, "emotional_state": ""},
            "final_text": "第一句。第二句。第三句。",
            "gen_prompt": None, "img_text": None, "cal_note_text": None, "memo_text": None,
            "streamed": True, "stream_blocks": all_blocks,
            "stream_saved": partial_saved,
        }

    deleted: list[int] = []

    async def _delete_chunks(msg_ids):
        deleted.extend(msg_ids)

    persisted: list[list[str]] = []

    async def _persist_chunks(session_id, final_state, chunks, gen_prompt, cal, memo,
                              extra_capabilities=None, tts_urls=None, tts=False):
        persisted.append(chunks)
        out = []
        for i, c in enumerate(chunks):
            out.append({
                "id": 50 + i, "session_id": session_id, "sender_type": "ai",
                "content": c, "created_at": "2026-08-24T00:00:00Z", "extra_meta": None,
                "tts_url": tts_urls[i] if tts_urls else None,
            })
        return out

    tts_urls_calls: list[list[str]] = []

    async def _synth_tts(chunk_texts, *a, **k):
        tts_urls_calls.append(chunk_texts)
        return [f"/uploads/tts/{i}.mp3" for i in range(len(chunk_texts))]

    async def _backfill(*a, **k):
        raise AssertionError("partial 路径不应调用 _backfill_stream_tts_meta")

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(cs, "_run_agent_core", _run_core)
    monkeypatch.setattr(streaming, "_delete_chunks", _delete_chunks)
    monkeypatch.setattr(streaming, "_persist_ai_chunks", _persist_chunks)
    monkeypatch.setattr(streaming, "_synthesize_chunks_tts", _synth_tts)
    monkeypatch.setattr(streaming, "_backfill_stream_tts_meta", _backfill)
    monkeypatch.setattr(streaming, "_push_user_notify", _noop)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)

    events: list[tuple] = []

    async def _sink(event, payload):
        events.append((event, payload))

    asyncio.run(cs.send_and_receive_stream(1, 1, 2, "hi", lang="zh", sink=_sink, tts=True))

    # partial：半截块被删除，且未走 _backfill_stream_tts_meta
    assert deleted == [10, 11]
    # 全量块落入批量路径，且 tts_urls 已按全量块逐句合成（每个块仍带 tts_url）
    assert persisted == [all_blocks]
    assert tts_urls_calls == [all_blocks]
    # P2-NEW：回退批量路径先发 reset_blocks（前端据此清除本轮旧 AI 块，避免新旧 id 不同的重复气泡）
    reset_evts = [p for e, p in events if e == "reset_blocks"]
    assert reset_evts == [{"reason": "tts_consumer_fallback"}]
    event_types = [e for e, _ in events]
    # reset_blocks 必须先于新 block 到达（前端先清旧块再接收新块）
    assert event_types.index("reset_blocks") < event_types.index("block")
    # done.blocks 与全文对应（历史不缺失）
    done_evt = [p for e, p in events if e == "done"][0]
    assert done_evt["message"]["content"] == "第一句。第二句。第三句。"
    assert [b["content"] for b in done_evt["blocks"]] == all_blocks
    assert all(b.get("tts_url") for b in done_evt["blocks"])


def test_send_and_receive_stream_tts_consumer_death_multi_round_history(monkeypatch):
    """方案 4.4：TTS consumer 死亡（多轮历史场景）回退批量路径重推 block，块 id 不同导致前端重复的防回归。

    复现缺陷：上一轮已有确认块（history ids，前端 _streamingBlockIds 只跟踪当前轮），本轮 TTS
    逐句消费中途死亡（consumer 只落库前 k 块，stream_saved < stream_blocks）。回退批量路径删除
    半截块（旧 id）再全量新建（新 id）；前端按块 id 去重（_confirmStreamBlock）永远不命中 →
    当前轮重复气泡。修复：回退批量路径先发 reset_blocks（reason="tts_consumer_fallback"），
    前端据此仅清「当前轮」已确认 AI 正式块（history 块不受影响），再接收新 id 的块。
    """
    async def _persist_user(*a, **k):
        return (1, {"id": 1, "content": "hi"})

    # 多轮历史：上一轮已确认的块（前端保留，不参与本轮清空）
    history_blocks = [5, 6]
    # 当前轮 consumer 只成功落库 2 块（id 70/71）就死亡；stream_blocks 实际 3 块
    partial_saved = [
        {"id": 70, "session_id": 1, "sender_type": "ai", "content": "第一句。",
         "created_at": "2026-08-24T00:00:00Z", "extra_meta": None, "tts_url": "/uploads/tts/a.mp3"},
        {"id": 71, "session_id": 1, "sender_type": "ai", "content": "第二句。",
         "created_at": "2026-08-24T00:00:00Z", "extra_meta": None, "tts_url": "/uploads/tts/b.mp3"},
    ]
    all_blocks = ["第一句。", "第二句。", "第三句。"]

    async def _run_core(*a, **k):
        return {
            "final_state": {"reasoning": None, "tools_used": [], "status_update": None,
                            "should_update_memory": False, "emotional_state": ""},
            "final_text": "第一句。第二句。第三句。",
            "gen_prompt": None, "img_text": None, "cal_note_text": None, "memo_text": None,
            "streamed": True, "stream_blocks": all_blocks,
            "stream_saved": partial_saved,
        }

    deleted: list[int] = []

    async def _delete_chunks(msg_ids):
        deleted.extend(msg_ids)

    fetched_blocks: list[list[str]] = []

    async def _persist_chunks(session_id, final_state, chunks, gen_prompt, cal, memo,
                              extra_capabilities=None, tts_urls=None, tts=False):
        fetched_blocks.append(chunks)
        out = []
        for i, c in enumerate(chunks):
            # 回退批量路径全量新建，id 与历史/半截块不同（前端按块 id 去重必然不命中）
            out.append({
                "id": 100 + i, "session_id": session_id, "sender_type": "ai",
                "content": c, "created_at": "2026-08-24T00:00:00Z", "extra_meta": None,
                "tts_url": tts_urls[i] if tts_urls else None,
            })
        return out

    tts_urls_calls: list[list[str]] = []

    async def _synth_tts(chunk_texts, *a, **k):
        tts_urls_calls.append(chunk_texts)
        return [f"/uploads/tts/{i}.mp3" for i in range(len(chunk_texts))]

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(cs, "_persist_user_message", _persist_user)
    monkeypatch.setattr(cs, "_run_agent_core", _run_core)
    monkeypatch.setattr(streaming, "_delete_chunks", _delete_chunks)
    monkeypatch.setattr(streaming, "_persist_ai_chunks", _persist_chunks)
    monkeypatch.setattr(streaming, "_synthesize_chunks_tts", _synth_tts)
    monkeypatch.setattr(streaming, "_push_user_notify", _noop)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)

    events: list[tuple] = []

    async def _sink(event, payload):
        events.append((event, payload))

    asyncio.run(cs.send_and_receive_stream(1, 1, 2, "hi", lang="zh", sink=_sink, tts=True))

    # 只删除当前轮半截块（id 70/71），历史块（5/6）不受影响
    assert deleted == [70, 71]
    assert set(deleted).isdisjoint(set(history_blocks))
    # 全量块落入批量路径，且 tts_urls 已按全量块逐句合成（每个块仍带 tts_url）
    assert fetched_blocks == [all_blocks]
    assert tts_urls_calls == [all_blocks]
    # reset_blocks 必须在重推的新 block 之前（前端先清当前轮旧块再接收新块）
    reset_evts = [p for e, p in events if e == "reset_blocks"]
    assert reset_evts == [{"reason": "tts_consumer_fallback"}]
    event_types = [e for e, _ in events]
    assert event_types.index("reset_blocks") < event_types.index("block")
    # 重推块 id 与前端已确认的历史块 id 不同 → 若无 reset_blocks 前端必重复
    pushed_ids = [p["id"] for e, p in events if e == "block"]
    assert set(pushed_ids).isdisjoint(set(history_blocks))
    assert set(pushed_ids).isdisjoint(set([b["id"] for b in partial_saved]))
    # done.blocks 与全文对应（历史不缺失）
    done_evt = [p for e, p in events if e == "done"][0]
    assert done_evt["message"]["content"] == "第一句。第二句。第三句。"
    assert [b["content"] for b in done_evt["blocks"]] == all_blocks
    assert all(b.get("tts_url") for b in done_evt["blocks"])

