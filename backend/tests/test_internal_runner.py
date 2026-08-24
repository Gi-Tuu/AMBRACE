# -*- coding: utf-8 -*-
"""P0-1b 测试：内部 AI 行为统一执行（登记 + internal_runner + 高频接入点）"""
import asyncio

from app.agent import internal_runner
from app.agent import tools


def test_内部工具登记_scope无门禁():
    for n in ("memory_extract", "memory_fact_check", "emotion_care", "weave_card", "memory_summary"):
        s = tools.get_tool(n)
        assert s is not None, n
        assert s.scope is None  # 内部行为无权限门禁
        assert s.provenance == n


def test_run_internal_未登记返回错误():
    out = asyncio.run(internal_runner.run_internal("不存在工具", {}))
    assert out["status"] == "error"
    assert "not registered" in out["error"]


def test_run_internal_走统一执行入口(monkeypatch):
    seen = {}

    async def _fake_execute(spec, payload, **kw):
        seen["spec"] = spec
        seen["payload"] = payload
        return {"status": "ok", "result": {"ok": True}}

    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _fake_execute)
    out = asyncio.run(internal_runner.run_internal(
        "memory_extract",
        {"session_id": 9, "character_id": 11, "user_id": 4, "user_msg": "x", "ai_msg": "y"},
        character_id=11, user_id=4,
    ))
    assert seen["spec"].name == "memory_extract"
    assert seen["spec"].scope is None
    assert seen["payload"]["character_id"] == 11
    assert out["status"] == "ok"


def test_run_internal_异常隔离(monkeypatch):
    async def _boom(spec, payload, **kw):
        raise RuntimeError("内部失败")

    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _boom)
    out = asyncio.run(internal_runner.run_internal("memory_extract", {}))
    assert out["status"] == "error"
    assert "内部失败" in out["error"]


def test_extractor_批量经内部入口(monkeypatch):
    from app.memory import extractor
    calls = []

    async def _fake_internal(tool, payload, **kw):
        calls.append((tool, payload))
        return {"status": "ok"}

    monkeypatch.setattr("app.agent.internal_runner.run_internal", _fake_internal)
    # 构造批量条件：同一 session 累积 BATCH_SIZE(4) 条（2026-08-18 C1 批次 2->4），且节流通过
    extractor._pending.clear()
    extractor._last_batch_at.clear()
    sid = 999901
    asyncio.run(extractor.add_chat_memory_extraction(sid, 11, 4, "m1", "r1", source_id=1))
    asyncio.run(extractor.add_chat_memory_extraction(sid, 11, 4, "m2", "r2", source_id=2))
    asyncio.run(extractor.add_chat_memory_extraction(sid, 11, 4, "m3", "r3", source_id=3))
    asyncio.run(extractor.add_chat_memory_extraction(sid, 11, 4, "m4", "r4", source_id=4))
    assert len(calls) == 4
    assert calls[0][0] == "memory_extract"
    assert calls[0][1]["character_id"] == 11
    assert calls[0][1]["session_id"] == sid


def test_fact_check_经内部入口(monkeypatch):
    from app.memory import fact_check
    calls = []

    async def _fake_internal(tool, payload, **kw):
        calls.append((tool, payload))

    monkeypatch.setattr("app.agent.internal_runner.run_internal", _fake_internal)

    async def _main():
        fact_check.schedule_fact_check(11, 4, "用户消息", "AI 回复")
        await asyncio.sleep(0.02)  # 让 fire-and-forget task 在同一 loop 执行

    asyncio.run(_main())
    assert calls and calls[0][0] == "memory_fact_check"
    assert calls[0][1]["character_id"] == 11
