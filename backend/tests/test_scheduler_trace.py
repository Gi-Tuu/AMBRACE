# -*- coding: utf-8 -*-
"""Phase D 测试：Scheduler 主动任务 → AgentTask trace（灰度 + flag 回退 + 失败静默）"""
import asyncio

from app.agent import loop
from app.scheduling import arbiter


def test_灰度判定_10分桶():
    assert arbiter.scheduler_gray_character(0) is True
    assert arbiter.scheduler_gray_character(10) is True
    assert arbiter.scheduler_gray_character(20) is True
    assert arbiter.scheduler_gray_character(1) is False
    assert arbiter.scheduler_gray_character(11) is False
    assert arbiter.scheduler_gray_character(7) is False


def test_trace_flag关闭不记录(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls.append(kw))
    loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    try:
        asyncio.run(arbiter._trace_scheduler_task(
            {"type": "greeting", "candidate": {"character_id": 1, "user_id": 2, "session_id": 3}}, True, 123,
        ))
    finally:
        loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    assert calls == []  # flag 关 = 不记录（可一键回退）


def test_trace_灰度角色写入(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls.append(kw))
    loop.AGENT_FLAGS["agent_loop_scheduler"] = True
    try:
        asyncio.run(arbiter._trace_scheduler_task(
            {"type": "motivation", "priority": 2,
             "candidate": {"character_id": 10, "user_id": 2, "session_id": 3}}, True, 456,
        ))
    finally:
        loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    assert len(calls) == 1
    c = calls[0]
    assert c["trigger"] == "scheduler"
    assert c["route"] == "scheduler_gray"  # char_id%10==0 → 灰度组
    assert c["character_id"] == 10
    assert c["user_id"] == 2
    assert c["session_id"] == 3
    assert c["llm_calls"] == 1
    assert c["status"] == "ok"
    assert c["latency_ms"] == 456
    assert "motivation" in c["steps_json"]


def test_trace_非灰度角色(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls.append(kw))
    loop.AGENT_FLAGS["agent_loop_scheduler"] = True
    try:
        asyncio.run(arbiter._trace_scheduler_task(
            {"type": "state_trigger", "priority": 1,
             "candidate": {"character_id": 7, "user_id": 2, "session_id": 3}}, False, 10,
        ))
    finally:
        loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    assert calls[0]["route"] == "scheduler"
    assert calls[0]["status"] == "blocked"  # 被拦截（限额/条件）
    assert calls[0]["llm_calls"] == 0
    assert calls[0]["error"] == "限额/条件拦截或执行失败"


def test_trace_timer事件取event字段(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls.append(kw))
    loop.AGENT_FLAGS["agent_loop_scheduler"] = True

    class _Ev:
        character_id = 10
        user_id = 2
        session_id = 9

    try:
        asyncio.run(arbiter._trace_scheduler_task({"type": "timer", "event": _Ev()}, True, 789))
    finally:
        loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    assert calls[0]["character_id"] == 10
    assert calls[0]["session_id"] == 9
    assert calls[0]["route"] == "scheduler_gray"


def test_trace_无角色不记录(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls.append(kw))
    loop.AGENT_FLAGS["agent_loop_scheduler"] = True
    try:
        asyncio.run(arbiter._trace_scheduler_task({"type": "x", "candidate": {}}, True, 1))
    finally:
        loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    assert calls == []
