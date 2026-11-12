# -*- coding: utf-8 -*-
"""Phase I 测试：群聊统一 Runtime 可观测（group_chat trace）。

2026-11-12：`agent_trace_group` flag 已退役（恒记录），故不再有「flag 关不记录」用例与设键/恢复包裹。
"""
from app.application.chat_groups import _trace_group_reply  # F8：api 门面已删，改指定义模块


def test_group_reply_trace_写入(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls.append(kw))
    _trace_group_reply(
        5, 4,
        [{"character_id": 11, "content": "好的"}, {"character_id": 12, "content": "哈哈"}],
        True, 320,
    )
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
    _trace_group_reply(5, 4, [], False, 999)
    assert calls[0]["status"] == "error"
    assert calls[0]["llm_calls"] == 0
    assert calls[0]["error"] == "群聊回应生成失败"
