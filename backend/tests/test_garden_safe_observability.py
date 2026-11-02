# -*- coding: utf-8 -*-
"""Safe garden observability: fixed enums only, no content; final input captured via fake LLM."""
import asyncio
import re

from test_garden_portable_user_context import wired  # noqa: E402,F401
from test_garden_chat_readonly_closure import LivePersonal  # noqa: E402


def _hook(registry, message="去花园看看，最近有什么新帖？"):
    msgs = []
    asyncio.run(registry.run_hook("context_inject", {
        "user_id": 7, "character_id": 13, "context_messages": msgs,
        "session_id": "synthetic", "user_message": message,
    }, user_id=7))
    return msgs


def _obs_logs(mod, monkeypatch):
    rows = []
    monkeypatch.setattr(mod.sdk, "log", lambda msg, *a: rows.append((msg, a)))
    return rows


def _text(rows):
    return "\n".join(str(r[0] % r[1]) for r in rows if str(r[0]).startswith("garden_obs"))


def _assert_no_sensitive(rows):
    blob = repr(rows)
    for s in ("实时标题", "忽略之前指令", "SYNTHETIC_SECRET", "api_key", "partner_key", "实时作者"):
        assert s not in blob, blob


def test_observability_success_empty_fail(monkeypatch, wired):  # noqa: F811
    mod, registry, doc, cfg = wired
    mod.GardenForum = LivePersonal
    monkeypatch.setenv("GARDEN_OBS_V1", "1")
    rows = _obs_logs(mod, monkeypatch)
    assert mod._is_garden_browse_request("去花园看看，最近有什么新帖？") is True
    for mode, business in (("ok", "ok"), ("empty", "empty"), ("invalid", "fail")):
        LivePersonal.mode = mode
        LivePersonal.calls = []
        rows.clear()
        _hook(registry)
        text = _text(rows)
        assert "stage=request" in text and f"business={business}" in text, (mode, text)
        assert re.search(r"corr=[0-9a-f]{8}", text), text
        bucket = "zero" if business in ("empty", "fail") else "nonzero"
        assert f"count_bucket={bucket}" in text, (mode, text)
        assert "stage=inject" in text and "injected=1" in text, (mode, text)
        _assert_no_sensitive(rows)


def test_observability_disabled_zero_logs(monkeypatch, wired):  # noqa: F811
    mod, registry, doc, cfg = wired
    mod.GardenForum = LivePersonal
    monkeypatch.delenv("GARDEN_OBS_V1", raising=False)
    rows = _obs_logs(mod, monkeypatch)
    LivePersonal.mode = "ok"
    _hook(registry)
    assert _text(rows) == ""


def test_observability_plain_and_plugin_off(monkeypatch, wired):  # noqa: F811
    mod, registry, doc, cfg = wired
    mod.GardenForum = LivePersonal
    monkeypatch.setenv("GARDEN_OBS_V1", "1")
    rows = _obs_logs(mod, monkeypatch)
    _hook(registry, "今天天气不错")
    assert _text(rows) == ""
    old = cfg["garden_forum_on"]
    cfg["garden_forum_on"] = False
    try:
        _hook(registry)
        assert _text(rows) == ""
    finally:
        cfg["garden_forum_on"] = old


def test_observability_timeout_and_budget(monkeypatch, wired):  # noqa: F811
    mod, registry, doc, cfg = wired
    mod.GardenForum = LivePersonal
    LivePersonal.mode = "ok"
    monkeypatch.setenv("GARDEN_OBS_V1", "1")
    rows = _obs_logs(mod, monkeypatch)
    async def _timeout(*a, **k):
        raise asyncio.TimeoutError()
    import app.agent.tool_runner as tr
    monkeypatch.setattr(tr, "execute_tool", _timeout)
    msgs = _hook(registry)
    text = _text(rows)
    assert "business=timeout" in text and "injected=1" in text
    assert "读取超时" in str(msgs[0].get("content") or "")
    rows.clear()
    monkeypatch.setattr(mod, "_GARDEN_CONTEXT_MAX_CHARS", 0)
    msgs2 = _hook(registry)
    text2 = _text(rows)
    assert "injected=0" in text2
    assert "预算不足" in str(msgs2[0].get("content") or "")


def test_final_model_input_captured_by_fake_llm(monkeypatch, wired):  # noqa: F811
    """真实 context_inject + 真实 nodes._stream_generate；fake LLM 捕获成功/空/失败最终 messages。"""
    import app.agent.nodes as nodes
    import app.agent.llm_client as llm

    mod, registry, doc, cfg = wired
    mod.GardenForum = LivePersonal
    monkeypatch.setenv("GARDEN_OBS_V1", "1")
    rows = _obs_logs(mod, monkeypatch)
    final_logs = []
    monkeypatch.setattr(llm._logger, "info", lambda msg, *a: final_logs.append((msg, a)))

    async def fake_stream(**kwargs):
        fake_stream.captured.append(kwargs["messages"])
        yield "合成回答"
    fake_stream.captured = []
    monkeypatch.setattr(nodes, "chat_completion_stream", fake_stream)

    async def scenario(message):
        msgs = []
        await registry.run_hook("context_inject", {
            "user_id": 7, "character_id": 13, "context_messages": msgs,
            "session_id": "synthetic", "user_message": message,
        }, user_id=7)
        state = {"context_messages": msgs}

        async def sink(*a, **k):
            return None

        await nodes._stream_generate(state, None, sink)
        return msgs

    for mode, business, expected in (
            ("ok", "ok", "实时标题"), ("empty", "empty", "没有公开帖子"),
            ("invalid", "fail", "读取失败")):
        LivePersonal.mode = mode
        fake_stream.captured.clear()
        final_logs.clear()
        msgs = asyncio.run(scenario("去花园看看，最近有什么新帖？"))
        assert fake_stream.captured and fake_stream.captured[0] is msgs
        assert any(expected in str(m.get("content") or "") for m in fake_stream.captured[0]), (mode, msgs)
        text = _text(rows)
        assert f"business={business}" in text, (mode, text)
        ftext = "\n".join(str(m % a) for m, a in final_logs if str(m).startswith("garden_obs"))
        assert "stage=final_input" in ftext and "corr=" in ftext, (mode, ftext)
        assert "stream=True" in ftext and "message_count=" in ftext, ftext
        assert "retained=1" in ftext, (mode, ftext)
        assert not any("garden-obs:" in str(m.get("content") or "") for m in fake_stream.captured[0])
    _assert_no_sensitive(rows)


def test_final_input_non_stream_fixed_log(monkeypatch):
    import app.agent.llm_client as llm
    monkeypatch.setenv("GARDEN_OBS_V1", "1")
    rows = []
    monkeypatch.setattr(llm._logger, "info", lambda msg, *a: rows.append((msg, a)))
    llm.set_garden_obs("deadbeef")
    llm.update_garden_obs(business="fail", injected="1")
    llm.obs_final_messages([{"role": "user", "content": "synthetic"}], stream=False)
    text = "\n".join(str(m % a) for m, a in rows if str(m).startswith("garden_obs"))
    assert "stage=final_input" in text and "stream=False" in text and "business=fail" in text

def test_observability_on_off_messages_byte_identical(monkeypatch, wired):  # noqa: F811
    mod, registry, doc, cfg = wired
    mod.GardenForum = LivePersonal
    LivePersonal.mode = "ok"
    monkeypatch.delenv("GARDEN_OBS_V1", raising=False)
    off = _hook(registry)
    monkeypatch.setenv("GARDEN_OBS_V1", "1")
    _obs_logs(mod, monkeypatch)
    on = _hook(registry)
    assert [m["content"] for m in on] == [m["content"] for m in off]
    assert not any("garden-obs:" in str(m.get("content") or "") for m in on)


def test_context_clear_prevents_stale_final_log(monkeypatch):
    import app.agent.llm_client as llm
    monkeypatch.setenv("GARDEN_OBS_V1", "1")
    rows = []
    monkeypatch.setattr(llm._logger, "info", lambda msg, *a: rows.append((msg, a)))
    llm.set_garden_obs("deadbeef")
    llm.clear_garden_obs()
    llm.obs_final_messages([{"role": "user", "content": "synthetic"}], stream=False)
    assert [r for r in rows if str(r[0]).startswith("garden_obs")] == []


def test_final_retained_false_when_expected_text_absent(monkeypatch):
    import hashlib
    import app.agent.llm_client as llm
    monkeypatch.setenv("GARDEN_OBS_V1", "1")
    rows = []
    monkeypatch.setattr(llm._logger, "info", lambda msg, *a: rows.append((msg, a)))
    llm.set_garden_obs("cafebabe")
    llm.update_garden_obs(business="ok", injected="1",
                          expected_text_hash=hashlib.sha256(b"expected-garden-text").hexdigest())
    llm.obs_final_messages([{"role": "user", "content": "different"}], stream=True)
    text = "\n".join(str(m % a) for m, a in rows if str(m).startswith("garden_obs"))
    assert "retained=0" in text and "retained=1" not in text


def test_real_build_context_clipping_retained_consistent(monkeypatch, wired):  # noqa: F811
    """真实 build_context（section/裁剪/context_inject）+ 真实流式节点 + fake LLM。

    默认真实装配预算下花园块可能被裁剪到只剩前缀；不断言必须 retained=1，
    而是断言日志 retained 与最终 messages 是否真的含实时正文一致。
    """
    import app.agent.context_builder as cb
    import app.agent.nodes as nodes
    import app.agent.llm_client as llm
    from app.db.database import async_session_factory
    from app.models.character import AICharacter
    from app.models.user import User

    mod, registry, doc, cfg = wired
    mod.GardenForum = LivePersonal
    LivePersonal.mode = "ok"
    monkeypatch.setenv("GARDEN_OBS_V1", "1")
    _obs_logs(mod, monkeypatch)
    final_logs = []
    monkeypatch.setattr(llm._logger, "info", lambda msg, *a: final_logs.append((msg, a)))
    captured = []

    async def fake_stream(**kwargs):
        captured.append(kwargs["messages"])
        yield "合成回答"
    monkeypatch.setattr(nodes, "chat_completion_stream", fake_stream)

    async def scenario():
        async with async_session_factory() as db:
            if await db.get(User, 7) is None:
                db.add(User(id=7, username="garden_u7", nickname="u7"))
            if await db.get(AICharacter, 13) is None:
                db.add(AICharacter(id=13, user_id=7, name="Sam", personality="x",
                                   chat_style="x", relation_type="x", is_active=True))
            await db.commit()
        state = {
            "user_message": "去花园看看，最近有什么新帖？",
            "character_id": 13, "user_id": 7, "session_id": 1,
            "intent": "", "retrieved_memories": [], "context_messages": [],
            "character_info": {}, "ai_response": "", "should_update_memory": False,
            "new_memories": [], "emotional_state": "", "bio_update": None,
            "status_update": None, "lang": "zh",
        }
        out = await cb.build_context(state, stream=True)
        msgs = out["context_messages"]
        state["context_messages"] = msgs

        async def sink(*a, **k):
            return None
        await nodes._stream_generate(state, None, sink)
        return msgs

    msgs = asyncio.run(scenario())
    assert captured and captured[0] is msgs
    has_live = any("实时标题" in str(m.get("content") or "") for m in captured[0])
    ftext = "\n".join(str(m % a) for m, a in final_logs if str(m).startswith("garden_obs"))
    assert "stage=final_input" in ftext and "stream=True" in ftext, ftext
    if has_live:
        assert "retained=1" in ftext, ftext
    else:
        assert "retained=0" in ftext, ftext
    assert not any("garden-obs:" in str(m.get("content") or "") for m in captured[0])
