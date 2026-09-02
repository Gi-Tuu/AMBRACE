# -*- coding: utf-8 -*-
"""Phase H 测试：任务级 Agent（agent_tasks 任务化 + 灰度升级 + 失败静默）"""
import asyncio

from app.agent import loop
from app.agent import task_engine
from app.scheduling import arbiter


def test_run_chat_task_编排(monkeypatch):
    calls = []

    async def _fake_create(**kw):
        calls.append(("create", kw))
        return 42

    async def _fake_update(tid, **kw):
        calls.append(("update", tid, kw))

    monkeypatch.setattr(task_engine, "create_agent_task", _fake_create)
    monkeypatch.setattr(task_engine, "update_task", _fake_update)
    tid = asyncio.run(task_engine.run_chat_task(
        11, 4, 9, "帮我查天气并记到备忘录",
        [{"action": "SEARCH"}, {"action": "MEMO"}], "回复内容", True,
    ))
    assert tid == 42
    assert calls[0][1]["trigger"] == "chat_task"
    assert calls[0][1]["goal"] == "帮我查天气并记到备忘录"
    assert calls[1][1] == 42  # update 的第一个参数是 task id
    assert calls[1][2]["status"] == "done"
    assert len(calls[1][2]["progress"]) == 2


def test_run_chat_task_失败保留进度(monkeypatch):
    calls = []

    async def _fake_create(**kw):
        return 7

    async def _fake_update(tid, **kw):
        calls.append(kw)

    monkeypatch.setattr(task_engine, "create_agent_task", _fake_create)
    monkeypatch.setattr(task_engine, "update_task", _fake_update)
    asyncio.run(task_engine.run_chat_task(11, 4, 9, "多步任务", [{"action": "SEARCH"}], "", ok=False))
    assert calls[0]["status"] == "failed"
    assert calls[0]["error"] == "工具执行未全部成功（已完成步骤已保留）"
    assert calls[0]["progress"] == [{"action": "SEARCH"}]


def test_create_task_异常静默(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.database.async_session_factory", _boom)
    tid = asyncio.run(task_engine.create_agent_task(trigger="x", goal="y", character_id=1))
    assert tid == 0


def test_update_task_空id跳过(monkeypatch):
    def _boom():
        raise AssertionError("不应访问 DB")

    monkeypatch.setattr("app.db.database.async_session_factory", _boom)
    asyncio.run(task_engine.update_task(0, status="done"))  # 不抛即通过


def test_scheduler_灰度升级建真实任务(monkeypatch):
    calls = []

    async def _fake_create(**kw):
        calls.append(("create", kw))
        return 99

    async def _fake_update(tid, **kw):
        calls.append(("update", tid, kw))

    monkeypatch.setattr("app.agent.task_engine.create_agent_task", _fake_create)
    monkeypatch.setattr("app.agent.task_engine.update_task", _fake_update)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: None)
    loop.AGENT_FLAGS["agent_loop_scheduler"] = True
    try:
        asyncio.run(arbiter._trace_scheduler_task(
            {"type": "motivation", "priority": 2,
             "candidate": {"character_id": 10, "user_id": 2, "session_id": 3}}, True, 456,
        ))
    finally:
        loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    assert any(c[0] == "create" and c[1]["trigger"] == "scheduler" and c[1]["character_id"] == 10 for c in calls)
    assert any(c[0] == "update" and c[1] == 99 and c[2]["status"] == "done" for c in calls)


def test_scheduler_非灰度不建任务(monkeypatch):
    calls = []

    async def _fake_create(**kw):
        calls.append(kw)
        return 1

    monkeypatch.setattr("app.agent.task_engine.create_agent_task", _fake_create)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: None)
    loop.AGENT_FLAGS["agent_loop_scheduler"] = True
    try:
        asyncio.run(arbiter._trace_scheduler_task(
            {"type": "state_trigger", "priority": 1,
             "candidate": {"character_id": 7, "user_id": 2, "session_id": 3}}, False, 15,
        ))
    finally:
        loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    assert calls == []  # 非灰度角色只写 trace，不建任务
