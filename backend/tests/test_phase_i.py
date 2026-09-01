# -*- coding: utf-8 -*-
"""Phase I 测试：群聊/社交统一 Runtime 可观测（group_chat trace + flag 回退）"""
from app.agent import loop
from app.application.chat_groups import _trace_group_reply  # F8：api 门面已删，改指定义模块


def test_group_reply_trace_写入(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls.append(kw))
    loop.AGENT_FLAGS["agent_trace_group"] = True
    try:
        _trace_group_reply(
            5, 4,
            [{"character_id": 11, "content": "好的"}, {"character_id": 12, "content": "哈哈"}],
            True, 320,
        )
    finally:
        loop.AGENT_FLAGS["agent_trace_group"] = True
    assert len(calls) == 1
    c = calls[0]
    assert c["trigger"] == "group_chat"
    assert c["route"] == "group_chat"
    assert c["user_id"] == 4
    assert c["llm_calls"] == 1
    assert c["status"] == "ok"
    assert c["latency_ms"] == 320
    assert '"group_id": 5' in c["steps_json"]
    assert "11" in c["steps_json"] and "12" in c["steps_json"]


def test_group_reply_trace_失败记录(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls.append(kw))
    loop.AGENT_FLAGS["agent_trace_group"] = True
    try:
        _trace_group_reply(5, 4, [], False, 999)
    finally:
        loop.AGENT_FLAGS["agent_trace_group"] = True
    assert calls[0]["status"] == "error"
    assert calls[0]["llm_calls"] == 0
    assert calls[0]["error"] == "群聊回应生成失败"


def test_group_reply_trace_flag关不记录(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls.append(kw))
    loop.AGENT_FLAGS["agent_trace_group"] = False
    try:
        _trace_group_reply(5, 4, [{"character_id": 11}], True, 100)
    finally:
        loop.AGENT_FLAGS["agent_trace_group"] = True
    assert calls == []  # 各平台独立回退
