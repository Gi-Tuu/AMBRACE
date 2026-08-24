# -*- coding: utf-8 -*-
"""Phase G 测试：Observation 结构化 + tool.executed 事件 + 联动订阅（flag 灰度）"""
import asyncio

from app.agent import loop
from app.agent import tools
from app.agent import tool_runner
from app.events.bus import event_bus
from app.events.types import EventType


def test_eventtype_常量():
    assert EventType.TOOL_EXECUTED.value == "tool.executed"
    assert EventType.TASK_COMPLETED.value == "task.completed"


def test_execute_tool_返回observation():
    spec = tools.get_tool("search")
    exec_spec = tools.ToolSpec(
        name=spec.name, description=spec.description, action_type=spec.action_type,
        risk_level=spec.risk_level, idempotent=spec.idempotent, scope=spec.scope,
        ask_auto_allow=spec.ask_auto_allow, epistemic_status=spec.epistemic_status,
        provenance=spec.provenance,
        execute=lambda p: {"result": "网络搜索结果摘要内容" * 10},
    )
    out = asyncio.run(tool_runner.execute_tool(exec_spec, {"query": "x"}, user_id=1))
    assert out["status"] == "ok"
    obs = out["observation"]
    assert obs["epistemic_status"] == "UNVERIFIED"  # 网络搜索未证实
    assert obs["provenance"] == "web_search"
    assert len(obs["summary"]) <= 120


def test_execute_tool_本地工具observation_fact():
    spec = tools.get_tool("note_calendar")
    exec_spec = tools.ToolSpec(
        name=spec.name, description=spec.description, action_type=spec.action_type,
        risk_level=spec.risk_level, idempotent=spec.idempotent, scope=spec.scope,
        execute=lambda p: {"ok": True, "summary": "已记录"},
    )
    out = asyncio.run(tool_runner.execute_tool(exec_spec, {}, user_id=1))
    assert out["status"] == "ok"
    assert out["observation"]["epistemic_status"] == "FACT"
    assert out["observation"]["summary"] == "已记录"


def test_tool_executed_事件发布():
    received = []

    async def _handler(payload):
        received.append(payload)

    event_bus.subscribe("tool.executed", _handler)
    try:
        async def _main():
            spec = tools.ToolSpec(name="fake_evt", description="测试", execute=lambda p: {"ok": True})
            await tool_runner.execute_tool(spec, {}, user_id=1, character_id=2, session_id=3)
            await asyncio.sleep(0.02)  # 让 publish 的 task 在同一 loop 执行完

        asyncio.run(_main())
    finally:
        event_bus.unsubscribe("tool.executed", _handler)
    assert len(received) == 1
    evt = received[0]
    assert evt["tool"] == "fake_evt"
    assert evt["status"] == "ok"
    assert evt["character_id"] == 2
    assert evt["session_id"] == 3
    assert evt["epistemic_status"] == "FACT"


def test_tool_blocked_也发布事件():
    received = []

    async def _handler(payload):
        received.append(payload)

    event_bus.subscribe("tool.executed", _handler)
    orig = tool_runner.check_tool_permission

    async def _forbid(spec, uid):
        return "forbid"

    try:
        tool_runner.check_tool_permission = _forbid
        async def _main():
            spec = tools.ToolSpec(name="fake_block", description="测试", execute=lambda p: {"ok": True})
            out = await tool_runner.execute_tool(spec, {}, user_id=1)
            await asyncio.sleep(0.02)
            return out

        out = asyncio.run(_main())
        assert out["status"] == "blocked"
    finally:
        tool_runner.check_tool_permission = orig
        event_bus.unsubscribe("tool.executed", _handler)
    assert received and received[0]["status"] == "blocked"


def test_on_tool_executed_flag关不联动(monkeypatch):
    from app.events import handlers
    calls = []
    monkeypatch.setattr("app.weave.incremental.schedule_incremental_weave", lambda *a, **k: calls.append(a))
    loop.AGENT_FLAGS["agent_tool_events"] = False
    try:
        asyncio.run(handlers._on_tool_executed(
            {"status": "ok", "tool": "search", "user_id": 1, "character_id": 2},
        ))
    finally:
        loop.AGENT_FLAGS["agent_tool_events"] = False
    assert calls == []  # 默认关 = 完全无联动


def test_on_tool_executed_flag开仅ok联动(monkeypatch):
    from app.events import handlers
    calls = []
    monkeypatch.setattr("app.weave.incremental.schedule_incremental_weave", lambda *a, **k: calls.append(a))
    loop.AGENT_FLAGS["agent_tool_events"] = True
    try:
        asyncio.run(handlers._on_tool_executed(
            {"status": "ok", "tool": "search", "user_id": 1, "character_id": 2},
        ))
        asyncio.run(handlers._on_tool_executed(
            {"status": "blocked", "tool": "search", "user_id": 1, "character_id": 2},
        ))
    finally:
        loop.AGENT_FLAGS["agent_tool_events"] = False
    assert len(calls) == 1  # 只有 ok 联动
