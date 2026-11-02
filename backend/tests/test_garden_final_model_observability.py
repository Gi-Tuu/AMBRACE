# -*- coding: utf-8 -*-
"""Real build_context -> stream node -> fake LLM coverage for all garden outcomes."""
import asyncio
import re

from test_garden_portable_user_context import wired  # noqa: E402,F401
from test_garden_chat_readonly_closure import LivePersonal  # noqa: E402


def _seed_global():
    from app.db.database import async_session_factory
    from app.models.character import AICharacter
    from app.models.user import User

    async def _do():
        async with async_session_factory() as db:
            if await db.get(User, 7) is None:
                db.add(User(id=7, username="garden_u7", nickname="u7"))
            if await db.get(AICharacter, 13) is None:
                db.add(AICharacter(id=13, user_id=7, name="Sam", personality="x",
                                   chat_style="x", relation_type="x", is_active=True))
            await db.commit()
    asyncio.run(_do())


def _state(message):
    return {
        "user_message": message,
        "character_id": 13, "user_id": 7, "session_id": 1,
        "intent": "", "retrieved_memories": [], "context_messages": [],
        "character_info": {}, "ai_response": "", "should_update_memory": False,
        "new_memories": [], "emotional_state": "", "bio_update": None,
        "status_update": None, "lang": "zh",
    }


def _setup(monkeypatch, wired_fixture, final_logs, captured):
    import app.agent.nodes as nodes
    import app.agent.llm_client as llm

    mod, registry, doc, cfg = wired_fixture
    mod.GardenForum = LivePersonal
    monkeypatch.setenv("GARDEN_OBS_V1", "1")
    monkeypatch.setattr(llm._logger, "info", lambda msg, *a: final_logs.append((msg, a)))

    async def fake_stream(**kwargs):
        captured.append(kwargs["messages"])
        yield "合成回答"
    monkeypatch.setattr(nodes, "chat_completion_stream", fake_stream)
    return mod, registry


async def _build_and_stream(state):
    import app.agent.context_builder as cb
    import app.agent.nodes as nodes

    out = await cb.build_context(state, stream=True)
    msgs = out["context_messages"]
    state["context_messages"] = msgs

    async def sink(*a, **k):
        return None
    await nodes._stream_generate(state, None, sink)
    return msgs


def _final_text(final_logs):
    return "\n".join(str(m % a) for m, a in final_logs if str(m).startswith("garden_obs"))


def test_real_build_context_empty_fail_timeout_and_trim(monkeypatch, wired):  # noqa: F811
    _seed_global()
    final_logs, captured = [], []
    mod, _registry = _setup(monkeypatch, wired, final_logs, captured)
    import app.agent.tool_runner as tr
    from test_garden_safe_observability import _obs_logs

    _obs_logs(mod, monkeypatch)
    cases = [("empty", "empty", "没有公开帖子", "retained=1"),
             ("invalid", "fail", "读取失败", "retained=1")]
    for mode, business, expected, retained in cases:
        LivePersonal.mode = mode
        final_logs.clear(); captured.clear()
        msgs = asyncio.run(_build_and_stream(_state("去花园看看，最近有什么新帖？")))
        text = "\n".join(str(m.get("content") or "") for m in msgs)
        ftext = _final_text(final_logs)
        assert expected in text, (mode, text)
        assert f"business={business}" in ftext and retained in ftext, (mode, ftext)

    # timeout through real build_context -> plugin timeout branch
    LivePersonal.mode = "ok"
    async def _timeout(*a, **k):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(tr, "execute_tool", _timeout)
    final_logs.clear(); captured.clear()
    msgs = asyncio.run(_build_and_stream(_state("去花园看看，最近有什么新帖？")))
    text = "\n".join(str(m.get("content") or "") for m in msgs)
    ftext = _final_text(final_logs)
    assert "读取超时" in text and "business=timeout" in ftext and "retained=1" in ftext

    # forced trim: _text replaced, injected=0, expected hash absent -> retained=unknown
    mod._GARDEN_CONTEXT_MAX_CHARS = 0
    monkeypatch.setattr(tr, "execute_tool", lambda *a, **k: None)
    final_logs.clear(); captured.clear()
    msgs = asyncio.run(_build_and_stream(_state("去花园看看，最近有什么新帖？")))
    text = "\n".join(str(m.get("content") or "") for m in msgs)
    ftext = _final_text(final_logs)
    assert "预算不足" in text and "injected=0" in ftext and "retained=unknown" in ftext, ftext


def test_same_task_consecutive_requests_clear_state(monkeypatch, wired):  # noqa: F811
    _seed_global()
    final_logs, captured = [], []
    mod, _registry = _setup(monkeypatch, wired, final_logs, captured)
    from test_garden_safe_observability import _obs_logs
    _obs_logs(mod, monkeypatch)
    LivePersonal.mode = "ok"

    async def scenario():
        await _build_and_stream(_state("去花园看看，最近有什么新帖？"))
        first = list(final_logs)
        final_logs.clear()
        await _build_and_stream(_state("今天天气不错"))
        second = list(final_logs)
        return first, second

    first, second = asyncio.run(scenario())
    f1 = _final_text(first)
    f2 = _final_text(second)
    assert "stage=final_input" in f1 and "business=ok" in f1
    assert f2 == "", f2  # 第二个普通请求必须无残留 garden 关联
    assert not any("garden-obs:" in str(m.get("content") or "")
                   for batch in captured for m in batch)


def test_concurrent_real_requests_isolated(monkeypatch, wired):  # noqa: F811
    _seed_global()
    final_logs, captured = [], []
    mod, _registry = _setup(monkeypatch, wired, final_logs, captured)
    from test_garden_safe_observability import _obs_logs
    _obs_logs(mod, monkeypatch)
    LivePersonal.mode = "ok"

    async def one():
        return await _build_and_stream(_state("去花园看看，最近有什么新帖？"))
    async def run():
        return await asyncio.gather(one(), one())
    asyncio.run(run())
    corrs = set(re.findall(r"corr=([0-9a-f]{8})", _final_text(final_logs)))
    assert len(corrs) == 2, (corrs, _final_text(final_logs))

def test_canceled_task_context_does_not_leak(monkeypatch):
    import app.agent.llm_client as llm
    monkeypatch.setenv("GARDEN_OBS_V1", "1")
    async def child():
        llm.set_garden_obs("cancelled1")
        await asyncio.sleep(10)
    async def run():
        t = asyncio.create_task(child())
        await asyncio.sleep(0)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        return llm._GARDEN_OBS_CTX.get()
    assert asyncio.run(run()) is None


def test_exception_then_next_request_clears_state(monkeypatch, wired):  # noqa: F811
    _seed_global()
    final_logs, captured = [], []
    mod, _registry = _setup(monkeypatch, wired, final_logs, captured)
    from test_garden_safe_observability import _obs_logs
    _obs_logs(mod, monkeypatch)
    LivePersonal.mode = "ok"
    import app.agent.context_builder as cb

    async def scenario():
        state = _state("去花园看看，最近有什么新帖？")
        await cb.build_context(state, stream=True)
        try:
            raise RuntimeError("synthetic mid-request exception")
        except RuntimeError:
            pass
        # 同一任务后续普通请求必须清空上一请求关联
        await _build_and_stream(_state("今天天气不错"))
        return final_logs

    logs = asyncio.run(scenario())
    assert _final_text(logs) == "", _final_text(logs)
    assert not any("garden-obs:" in str(m.get("content") or "")
                   for batch in captured for m in batch)

def test_inflight_build_context_cancel_isolated(monkeypatch, wired):  # noqa: F811
    """真实 build_context 进入 context_inject 后挂起，再取消；后续普通请求不继承。"""
    _seed_global()
    final_logs, captured = [], []
    mod, registry = _setup(monkeypatch, wired, final_logs, captured)
    from test_garden_safe_observability import _obs_logs
    _obs_logs(mod, monkeypatch)
    LivePersonal.mode = "ok"
    import app.agent.llm_client as llm
    entered = asyncio.Event()
    release = asyncio.Event()
    orig_run_hook = registry.run_hook

    async def slow_hook(name, ctx, **kwargs):
        if name == "context_inject":
            await orig_run_hook(name, ctx, **kwargs)
            assert llm._GARDEN_OBS_CTX.get() is not None
            entered.set()
            await release.wait()  # 确保取消发生在 hook 已执行、ContextVar 已设置之后
            return
        return await orig_run_hook(name, ctx, **kwargs)

    monkeypatch.setattr(registry, "run_hook", slow_hook)

    async def scenario():
        task = asyncio.create_task(_build_and_stream(_state("去花园看看，最近有什么新帖？")))
        await asyncio.wait_for(entered.wait(), timeout=3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # 同一任务后续普通请求：build_context 必须清空上一关联
        await _build_and_stream(_state("今天天气不错"))
        return llm._GARDEN_OBS_CTX.get()

    assert asyncio.run(scenario()) is None
    assert _final_text(final_logs) == "", _final_text(final_logs)


def test_nonstream_generate_response_final_input(monkeypatch, wired):  # noqa: F811
    """若项目非流式 generate_response 可执行：真实 build_context + fake chat_completion。"""
    _seed_global()
    final_logs, captured = [], []
    mod, _registry = _setup(monkeypatch, wired, final_logs, captured)
    LivePersonal.mode = "ok"
    import app.agent.context_builder as cb
    import app.agent.nodes as nodes
    import app.agent.llm_client as llm

    async def fake_completion(**kwargs):
        captured.append(kwargs["messages"])
        return "合成非流式回答"
    monkeypatch.setattr(nodes, "chat_completion", fake_completion)

    async def no_cfg(uid):
        return None
    monkeypatch.setattr(llm, "get_user_llm_config", no_cfg)

    async def scenario():
        state = _state("去花园看看，最近有什么新帖？")
        out = await cb.build_context(state, stream=False)
        state["context_messages"] = out["context_messages"]
        state["stream_sink"] = None
        await nodes.generate_response(state)
        return state["context_messages"]

    try:
        msgs = asyncio.run(scenario())
    except Exception as exc:
        raise AssertionError("nonstream path unavailable: %s" % type(exc).__name__) from exc
    assert captured and captured[0] is msgs
    ftext = _final_text(final_logs)
    assert "stage=final_input" in ftext and "stream=False" in ftext, ftext
    assert "retained=1" in ftext, ftext
