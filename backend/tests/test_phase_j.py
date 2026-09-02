# -*- coding: utf-8 -*-
"""Phase J 测试：周复盘（flag 灰度/LLM 生成/记忆沉淀/每 REFLECT_INTERVAL_DAYS 天一次）"""
import asyncio

from app.agent import loop
from app.scheduling import daily_reflection


def test_build_prompt_纯函数():
    p = daily_reflection._build_prompt("小遥", "- [chat] 搜索了天气")
    assert "你是小遥" in p
    assert "搜索了天气" in p
    assert "本周复盘" in p  # 周复盘语义
    p2 = daily_reflection._build_prompt("阿澈", "")
    assert "没有记录到特别的活动" in p2
    assert "AI/复盘/系统" in p2  # 提示禁止提系统字眼


def test_generate_flag关不执行(monkeypatch):
    calls = []

    async def _fake_llm(**kw):
        calls.append(kw)
        return "今天的复盘内容"

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_llm)
    loop.AGENT_FLAGS["agent_daily_reflection"] = False
    try:
        ok = asyncio.run(daily_reflection.generate_daily_reflection(11, 4))
    finally:
        loop.AGENT_FLAGS["agent_daily_reflection"] = False
    assert ok is False
    assert calls == []  # flag 关不调 LLM


def test_generate_flag开生成并沉淀(monkeypatch):
    calls = {"llm": [], "memory": [], "trace": []}

    async def _fake_llm(**kw):
        calls["llm"].append(kw)
        return "今天搜索了天气和菜谱，明天想试试做蛋糕。"

    async def _fake_memory(**kw):
        calls["memory"].append(kw)

    async def _no_use(cid):
        return False

    async def _no_data(cid):
        return ""

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_llm)
    monkeypatch.setattr("app.memory.save_memory", _fake_memory)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls["trace"].append(kw))
    monkeypatch.setattr(daily_reflection, "_used_recently", _no_use)
    monkeypatch.setattr(daily_reflection, "_collect_week_data", _no_data)
    loop.AGENT_FLAGS["agent_daily_reflection"] = True
    try:
        ok = asyncio.run(daily_reflection.generate_daily_reflection(11, 4))
    finally:
        loop.AGENT_FLAGS["agent_daily_reflection"] = False
    assert ok is True
    assert calls["llm"] and calls["llm"][0]["task"] == "reflection"
    assert calls["memory"] and calls["memory"][0]["memory_type"] == "ai_reflection"
    assert calls["memory"][0]["importance"] == 5  # 1-5 制最高档（6 会被当 pct=6）
    assert calls["trace"] and calls["trace"][0]["trigger"] == "reflection"
    assert calls["trace"][0]["route"] == "daily_reflection"


def test_generate_已用过不重复(monkeypatch):
    calls = []

    async def _fake_llm(**kw):
        calls.append(kw)
        return "x" * 30

    async def _used(cid):
        return True

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_llm)
    monkeypatch.setattr(daily_reflection, "_used_recently", _used)
    loop.AGENT_FLAGS["agent_daily_reflection"] = True
    try:
        ok = asyncio.run(daily_reflection.generate_daily_reflection(11, 4))
    finally:
        loop.AGENT_FLAGS["agent_daily_reflection"] = False
    assert ok is False
    assert calls == []  # 间隔期内最多 1 次


def test_generate_内容过短返回False(monkeypatch):
    async def _fake_llm(**kw):
        return "太短"

    async def _no_use(cid):
        return False

    async def _no_data(cid):
        return ""

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_llm)
    monkeypatch.setattr(daily_reflection, "_used_recently", _no_use)
    monkeypatch.setattr(daily_reflection, "_collect_week_data", _no_data)
    loop.AGENT_FLAGS["agent_daily_reflection"] = True
    try:
        ok = asyncio.run(daily_reflection.generate_daily_reflection(11, 4))
    finally:
        loop.AGENT_FLAGS["agent_daily_reflection"] = False
    assert ok is False
