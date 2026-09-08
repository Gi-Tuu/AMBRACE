# -*- coding: utf-8 -*-
"""工具轨迹治理 R5 专项回归测试（2026-09-09，方案 §4.5）。

插件/内置工具单次成败落统一轨迹（trigger=tool）：
- flag agent_tool_exec_trace 默认关 = 零写放大（不写 agent_task_logs）；
- 开启后：插件/内置工具 ok/error 均落一条；MCP 工具跳过（已有 mcp_call_logs，避免双记）；
  scheduler 前缀/空工具名等噪音不落；
- 与既有织库联动（agent_tool_events）并列、互不影响；
- 轨迹写入失败静默，不影响事件主链路。

纯 monkeypatch 测试（不触碰 backend/data）；项目未装 pytest-asyncio，统一 asyncio.run。
"""
import asyncio

from app.agent import loop
from app.events.handlers import _on_tool_executed


def _trace_calls(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls.append(kw))
    return calls


def _payload(**kw):
    p = {
        "tool": "douyin.post",
        "action_type": "douyin_post",
        "status": "ok",
        "epistemic_status": "FACT",
        "provenance": "tool",
        "summary": "发布成功",
        "user_id": 1,
        "character_id": 88101,
        "session_id": 3,
        "latency_ms": 120,
        "error": None,
    }
    p.update(kw)
    return p


def test_R5_默认关_不写轨迹(monkeypatch):
    calls = _trace_calls(monkeypatch)
    loop.AGENT_FLAGS["agent_tool_exec_trace"] = False
    try:
        asyncio.run(_on_tool_executed(_payload()))
    finally:
        loop.AGENT_FLAGS["agent_tool_exec_trace"] = False
    assert calls == []


def test_R5_开启_成功落一条trigger_tool(monkeypatch):
    calls = _trace_calls(monkeypatch)
    loop.AGENT_FLAGS["agent_tool_exec_trace"] = True
    try:
        asyncio.run(_on_tool_executed(_payload()))
    finally:
        loop.AGENT_FLAGS["agent_tool_exec_trace"] = False
    assert len(calls) == 1
    kw = calls[0]
    assert kw["trigger"] == "tool"
    assert kw["route"] == "douyin.post"
    assert kw["tool_calls"] == 1
    assert kw["latency_ms"] == 120
    assert kw["status"] == "ok"
    assert kw["error"] is None
    assert kw["character_id"] == 88101 and kw["user_id"] == 1
    assert '"tool": "douyin.post"' in kw["steps_json"]


def test_R5_开启_失败也落_status_error带错误文本(monkeypatch):
    calls = _trace_calls(monkeypatch)
    loop.AGENT_FLAGS["agent_tool_exec_trace"] = True
    try:
        asyncio.run(_on_tool_executed(_payload(status="error", error="远端超时")))
    finally:
        loop.AGENT_FLAGS["agent_tool_exec_trace"] = False
    assert len(calls) == 1
    assert calls[0]["status"] == "error"
    assert calls[0]["error"] == "远端超时"


def test_R5_开启_MCP工具跳过避免双记(monkeypatch):
    """MCP 已有 mcp_call_logs（前端 MCP 分区读取），trigger=tool 只补插件/内置。"""
    calls = _trace_calls(monkeypatch)
    loop.AGENT_FLAGS["agent_tool_exec_trace"] = True
    try:
        asyncio.run(_on_tool_executed(_payload(tool="mcp.browser_search")))
    finally:
        loop.AGENT_FLAGS["agent_tool_exec_trace"] = False
    assert calls == []


def test_R5_开启_scheduler前缀与空工具名不落(monkeypatch):
    calls = _trace_calls(monkeypatch)
    loop.AGENT_FLAGS["agent_tool_exec_trace"] = True
    try:
        asyncio.run(_on_tool_executed(_payload(tool="scheduler.greeting")))
        asyncio.run(_on_tool_executed(_payload(tool="")))
    finally:
        loop.AGENT_FLAGS["agent_tool_exec_trace"] = False
    assert calls == []


def test_R5_与织库联动互不影响(monkeypatch):
    """trace 开、weave 关 → 只写轨迹不触发织库；weave 开、trace 关 → 只织库不写轨迹。"""
    calls = _trace_calls(monkeypatch)
    weave_calls = []
    monkeypatch.setattr(
        "app.weave.incremental.schedule_incremental_weave",
        lambda uid, cid, domain: weave_calls.append((uid, cid, domain)),
    )

    loop.AGENT_FLAGS["agent_tool_exec_trace"] = True
    loop.AGENT_FLAGS["agent_tool_events"] = False
    try:
        asyncio.run(_on_tool_executed(_payload()))
    finally:
        loop.AGENT_FLAGS["agent_tool_exec_trace"] = False
    assert len(calls) == 1
    assert weave_calls == []

    loop.AGENT_FLAGS["agent_tool_exec_trace"] = False
    loop.AGENT_FLAGS["agent_tool_events"] = True
    try:
        asyncio.run(_on_tool_executed(_payload()))
    finally:
        loop.AGENT_FLAGS["agent_tool_events"] = True  # 恢复仓库基线（默认 True）
    assert len(calls) == 1  # 未新增
    assert weave_calls == [(1, 88101, "shared")]


def test_R5_轨迹写入异常静默不破坏事件链(monkeypatch):
    def boom(**kw):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.agent.trace.enqueue_task_log", boom)
    loop.AGENT_FLAGS["agent_tool_exec_trace"] = True
    try:
        asyncio.run(_on_tool_executed(_payload()))  # 不抛即通过
    finally:
        loop.AGENT_FLAGS["agent_tool_exec_trace"] = False
