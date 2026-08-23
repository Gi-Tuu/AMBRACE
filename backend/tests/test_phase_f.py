# -*- coding: utf-8 -*-
"""Phase F 测试：主链路本地工具（日历/备忘）统一执行入口 + agent_loop_chat flag 回退"""
import asyncio

from app.agent import loop
from app.agent import tools
from app.services import chat_service


def test_note_工具登记_本地能力无权限门禁():
    assert tools.get_tool("note_calendar").scope is None
    assert tools.get_tool("note_memo").scope is None
    assert tools.get_tool("note_calendar").ask_auto_allow is False


def test_save_notes_flag开走execute_tool(monkeypatch):
    loop.AGENT_FLAGS["agent_loop_chat"] = True
    calls = []

    async def _fake_exec(tool, payload, cid):
        calls.append((tool, payload, cid))

    monkeypatch.setattr(chat_service, "_execute_note_tool", _fake_exec)
    try:
        asyncio.run(chat_service._save_phone_desktop_notes(
            11, "正文[CAL_NOTE]2026-08-20 一起做饭[/CAL_NOTE][MEMO]喂猫[/MEMO]",
        ))
    finally:
        loop.AGENT_FLAGS["agent_loop_chat"] = True
    assert len(calls) == 2
    assert calls[0][0] == "note_calendar"
    assert calls[0][1]["date"] == "2026-08-20"
    assert calls[0][1]["text"] == "一起做饭"
    assert calls[0][1]["character_id"] == 11
    assert calls[1][0] == "note_memo"
    assert calls[1][1]["text"] == "喂猫"


def test_save_notes_flag关直落库(monkeypatch):
    loop.AGENT_FLAGS["agent_loop_chat"] = False
    cal_calls, memo_calls = [], []

    async def _fake_cal(cid, date, text, author=""):
        cal_calls.append((cid, date, text))

    async def _fake_memo(cid, text, author=""):
        memo_calls.append((cid, text))

    monkeypatch.setattr(chat_service, "_save_calendar_note", _fake_cal)
    monkeypatch.setattr(chat_service, "_save_memo_note", _fake_memo)
    try:
        asyncio.run(chat_service._save_phone_desktop_notes(
            11, "x[CAL_NOTE]今天 测试备注[/CAL_NOTE][MEMO]测试备忘[/MEMO]",
        ))
    finally:
        loop.AGENT_FLAGS["agent_loop_chat"] = True
    assert len(cal_calls) == 1  # 日期为动态（今天/明天），只验数量与内容
    assert len(memo_calls) == 1
    assert memo_calls[0][1] == "测试备忘"


def test_save_notes_无标记不触发(monkeypatch):
    calls = []

    async def _fake_exec(tool, payload, cid):
        calls.append(tool)

    monkeypatch.setattr(chat_service, "_execute_note_tool", _fake_exec)
    try:
        asyncio.run(chat_service._save_phone_desktop_notes(11, "纯文本无标记"))
    finally:
        loop.AGENT_FLAGS["agent_loop_chat"] = True
    assert calls == []


def test_execute_note_tool_走统一执行入口(monkeypatch):
    seen = {}

    async def _fake_execute(spec, payload, **kw):
        seen["spec"] = spec
        seen["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _fake_execute)
    asyncio.run(chat_service._execute_note_tool(
        "note_calendar", {"character_id": 11, "date": "2026-08-20", "text": "x"}, 11,
    ))
    assert seen["spec"].name == "note_calendar"
    assert seen["payload"]["date"] == "2026-08-20"
    assert seen["spec"].scope is None


def test_execute_note_tool_未登记工具不执行(monkeypatch):
    calls = []

    async def _fake_execute(spec, payload, **kw):
        calls.append(spec)

    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _fake_execute)
    asyncio.run(chat_service._execute_note_tool("不存在工具", {}, 11))
    assert calls == []
