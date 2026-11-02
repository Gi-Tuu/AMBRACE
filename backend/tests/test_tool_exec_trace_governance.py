# -*- coding: utf-8 -*-
"""工具执行轨迹已常驻（2026-11-02 移除 agent_tool_exec_trace 开关）回归。"""
import asyncio

from app.events.handlers import _on_tool_executed


def _trace_calls(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls.append(kw))
    return calls


def _payload(**kw):
    p = {
        "tool": "douyin.post", "action_type": "douyin_post", "status": "ok",
        "epistemic_status": "FACT", "provenance": "tool", "summary": "发布成功",
        "user_id": 1, "character_id": 88101, "session_id": 3,
        "latency_ms": 120, "error": None,
    }
    p.update(kw)
    return p


def test_常驻_成功落一条trigger_tool(monkeypatch):
    calls = _trace_calls(monkeypatch)
    asyncio.run(_on_tool_executed(_payload()))
    assert len(calls) == 1
    kw = calls[0]
    assert kw["trigger"] == "tool" and kw["route"] == "douyin.post"
    assert kw["tool_calls"] == 1 and kw["latency_ms"] == 120
    assert kw["status"] == "ok" and kw["error"] is None
    assert kw["character_id"] == 88101 and kw["user_id"] == 1
    assert '"tool": "douyin.post"' in kw["steps_json"]


def test_常驻_失败也落_status_error带错误文本(monkeypatch):
    calls = _trace_calls(monkeypatch)
    asyncio.run(_on_tool_executed(_payload(status="error", error="远端超时")))
    assert len(calls) == 1
    assert calls[0]["status"] == "error" and calls[0]["error"] == "远端超时"


def test_常驻_MCP工具跳过避免双记(monkeypatch):
    calls = _trace_calls(monkeypatch)
    asyncio.run(_on_tool_executed(_payload(tool="mcp.browser_search")))
    assert calls == []


def test_常驻_scheduler前缀与空工具名不落(monkeypatch):
    calls = _trace_calls(monkeypatch)
    asyncio.run(_on_tool_executed(_payload(tool="scheduler.greeting")))
    asyncio.run(_on_tool_executed(_payload(tool="")))
    assert calls == []


def test_织库联动已固化常开(monkeypatch):
    calls = _trace_calls(monkeypatch)
    weave_calls = []
    monkeypatch.setattr("app.weave.incremental.schedule_incremental_weave",
                        lambda uid, cid, domain: weave_calls.append((uid, cid, domain)))
    asyncio.run(_on_tool_executed(_payload()))
    assert len(calls) == 1
    assert weave_calls == [(1, 88101, "shared")]


def test_轨迹写入异常静默不破坏事件链(monkeypatch):
    def boom(**kw):
        raise RuntimeError("db down")
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", boom)
    asyncio.run(_on_tool_executed(_payload()))
