# -*- coding: utf-8 -*-
"""Garden read-only must not trigger weave incremental writes.

Confirmed issue: ToolRunner status=ok is execution-layer; garden_read business
failure/empty also returns status=ok. handlers._on_tool_executed previously
scheduled weave incremental for any status=ok tool. This test pins:
- garden_read success/empty/business-failure never schedules weave;
- other tools keep existing status=ok behavior;
- execution-layer vs business-layer result is observable.
"""
import asyncio
from contextlib import contextmanager

from app.agent import tool_runner, tools
from app.events import handlers
from app.weave import incremental as weave_inc


def _run_event(monkeypatch, payload):
    calls = []
    monkeypatch.setattr(weave_inc, "schedule_incremental_weave",
                        lambda *a, **k: calls.append((a, k)))
    asyncio.run(handlers._on_tool_executed(payload))
    return calls


def test_garden_read_never_schedules_weave(monkeypatch):
    for result_ok in (True, False):
        for status in ("ok",):
            calls = _run_event(monkeypatch, {
                "tool": "garden_read", "status": status, "user_id": 1,
                "character_id": 13, "result_ok": result_ok,
            })
            assert calls == [], (result_ok, calls)


def test_other_tool_status_ok_still_schedules(monkeypatch):
    calls = _run_event(monkeypatch, {
        "tool": "search", "status": "ok", "user_id": 1, "character_id": 13,
    })
    assert len(calls) == 1 and calls[0][0] == (1, 13, "shared"), calls


def test_error_tool_does_not_schedule(monkeypatch):
    calls = _run_event(monkeypatch, {
        "tool": "garden_read", "status": "error", "user_id": 1, "character_id": 13,
    })
    assert calls == []


def test_execution_ok_can_still_be_business_failure():
    """garden_read failure returns execute status=ok but business ok=False."""
    spec = tools.ToolSpec(
        name="garden_read", description="x",
        execute=lambda payload: {"ok": False, "text": "failure", "code": "read_failed"},
    )
    out = asyncio.run(tool_runner.execute_tool(
        spec, {}, user_id=1, character_id=13, session_id=11))
    assert out["status"] == "ok"
    assert out["result"]["ok"] is False
    assert out["result"]["text"] == "failure"

# ── exact state restoration for event-bus subscribers + weave debounce ──
@contextmanager
def _preserve_event_state(inc):
    from app.events.bus import event_bus
    old_obj = event_bus._subscribers
    old_subs = {k: list(v) for k, v in old_obj.items()}
    old_ts = dict(inc._last_ts)
    try:
        yield
    finally:
        # restore same dict object/content (other modules may hold a reference)
        old_obj.clear()
        old_obj.update(old_subs)
        event_bus._subscribers = old_obj
        inc._last_ts.clear()
        inc._last_ts.update(old_ts)


def _new_tasks_since(old_tasks):
    from app.utils.async_tasks import _BG_TASKS
    return [t for t in _BG_TASKS if t not in old_tasks and not t.done()]


# ── end-to-end: real context_inject -> ToolRunner -> event bus -> handler -> scheduler → persistence ──
from test_garden_portable_user_context import wired  # noqa: E402,F401
from test_garden_chat_readonly_closure import LivePersonal  # noqa: E402


def _reset_weave(monkeypatch, tools_mod, inc):
    from app.models.memory import WeaveCard, WeaveCardCharacter
    inc._last_ts.clear()
    factory = tools_mod._garden_session_factory
    async def fake_run_internal(tool_name, payload, *, character_id=None, user_id=None):
        async with factory() as db:
            card = WeaveCard(user_id=int(user_id), character_id=int(character_id),
                             title="synthetic", summary="synthetic",
                             detail="{}", importance=60.0,
                             content_hash="synthetic", domain=payload.get("domain", "shared"))
            db.add(card)
            await db.flush()
            db.add(WeaveCardCharacter(card_id=card.id, character_id=int(character_id)))
            await db.commit()
        return {"status": "ok", "result": {"created": 1}}
    import app.agent.internal_runner as ir
    monkeypatch.setattr(ir, "run_internal", fake_run_internal)


async def _inject_and_drain(registry, old_tasks=None):
    msgs = []
    await registry.run_hook("context_inject", {
        "user_id": 7, "character_id": 13, "context_messages": msgs,
        "session_id": "synthetic", "user_message": "去花园看看，最近有什么新帖",
    }, user_id=7)
    await asyncio.sleep(0.05)
    if old_tasks is not None:
        new = _new_tasks_since(old_tasks)
        if new:
            await asyncio.wait(new, timeout=3)
    return msgs


async def _count_cards(tools_mod):
    from sqlalchemy import func, select
    from app.models.memory import WeaveCard
    async with tools_mod._garden_session_factory() as db:
        return int((await db.execute(select(func.count()).select_from(WeaveCard))).scalar_one())


def _register_handlers():
    from app.events.handlers import register_builtin_handlers
    register_builtin_handlers()


def test_e2e_garden_success_empty_failure_no_weave_persistence(monkeypatch, wired):  # noqa: F811
    import app.agent.tools as tools_mod
    from app.weave import incremental as inc
    from app.utils import async_tasks
    mod, registry, doc, cfg = wired
    mod.GardenForum = LivePersonal
    old_tasks = set(async_tasks._BG_TASKS)
    with _preserve_event_state(inc):
        _register_handlers()
        _reset_weave(monkeypatch, tools_mod, inc)
        for mode in ("ok", "empty", "invalid"):
            LivePersonal.calls = []
            LivePersonal.mode = mode
            msgs = asyncio.run(_inject_and_drain(registry, old_tasks))
            # 成功/空/失败都必须把明确结果交给模型；且都不应触发织库卡
            assert msgs, mode
            assert asyncio.run(_count_cards(tools_mod)) == 0, (mode, msgs)
            if mode in ("ok", "empty"):
                assert "feed" in LivePersonal.calls, (mode, LivePersonal.calls)
            else:
                assert "feed" not in LivePersonal.calls, (mode, LivePersonal.calls)
                assert "读取失败" in msgs[0]["content"], msgs


def test_e2e_other_tool_still_writes_weave_card(monkeypatch, wired):  # noqa: F811
    import app.agent.tools as tools_mod
    from app.weave import incremental as inc
    from app.events.bus import event_bus
    from app.utils import async_tasks
    mod, registry, doc, cfg = wired
    old_tasks = set(async_tasks._BG_TASKS)
    with _preserve_event_state(inc):
        _register_handlers()
        _reset_weave(monkeypatch, tools_mod, inc)
        async def run():
            await event_bus.publish("tool.executed", {
                "tool": "search", "status": "ok", "user_id": 7, "character_id": 13,
                "session_id": "synthetic", "result_ok": True,
            })
            await asyncio.sleep(0.05)
            new = _new_tasks_since(old_tasks)
            if new:
                await asyncio.wait(new, timeout=3)
            return await _count_cards(tools_mod)
        assert asyncio.run(run()) == 1

def test_garden_read_no_agent_task_log(monkeypatch):
    import app.agent.trace as trace
    calls=[]
    monkeypatch.setattr(trace,"enqueue_task_log",lambda **kw: calls.append(kw))
    asyncio.run(handlers._on_tool_executed({"tool":"garden_read","status":"ok",
        "user_id":1,"character_id":13,"session_id":11}))
    assert calls == []
    asyncio.run(handlers._on_tool_executed({"tool":"search","status":"ok",
        "user_id":1,"character_id":13,"session_id":11}))
    assert len(calls) == 1


def test_garden_read_no_trace_and_tool_trace_is_permanent(monkeypatch):
    """agent_tool_exec_trace 已常驻移除：没有开关，普通工具恒留痕，garden_read 恒不留。"""
    import app.agent.trace as trace
    from app.agent import loop
    calls = []
    monkeypatch.setattr(trace, "enqueue_task_log", lambda **kw: calls.append(kw))
    assert "agent_tool_exec_trace" not in loop.AGENT_FLAGS
    asyncio.run(handlers._on_tool_executed({"tool": "garden_read", "status": "ok",
        "user_id": 1, "character_id": 13, "session_id": 11}))
    assert calls == []
    asyncio.run(handlers._on_tool_executed({"tool": "search", "status": "ok",
        "user_id": 1, "character_id": 13, "session_id": 11}))
    assert len(calls) == 1


def test_event_state_restored_on_success_and_exception():
    from app.events.bus import event_bus
    from app.weave import incremental as inc
    sentinel = lambda payload: None
    old_subs = {k: list(v) for k, v in event_bus._subscribers.items()}
    old_ts = dict(inc._last_ts)
    try:
        event_bus._subscribers.setdefault("__sentinel__", []).append(sentinel)
        inc._last_ts[(9, 9, "shared")] = 123.0
        before_subs = {k: list(v) for k, v in event_bus._subscribers.items()}
        before_ts = dict(inc._last_ts)
        # normal exit
        with _preserve_event_state(inc):
            event_bus._subscribers["__added__"] = [sentinel]
            inc._last_ts.clear()
        assert {k: list(v) for k, v in event_bus._subscribers.items()} == before_subs
        assert dict(inc._last_ts) == before_ts
        # exception exit
        try:
            with _preserve_event_state(inc):
                event_bus._subscribers["__added__"] = [sentinel]
                inc._last_ts.clear()
                raise RuntimeError("synthetic exception")
        except RuntimeError:
            pass
    except RuntimeError:
        pass
    assert {k: list(v) for k, v in event_bus._subscribers.items()} == before_subs
    assert dict(inc._last_ts) == before_ts
    event_bus._subscribers.clear(); event_bus._subscribers.update(old_subs)
    inc._last_ts.clear(); inc._last_ts.update(old_ts)
