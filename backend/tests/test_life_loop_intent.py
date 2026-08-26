# -*- coding: utf-8 -*-
"""聊天→生活意图提取测试（chat_intent.py，零 LLM 本地规则）。

覆盖（2026-08-26，v3.3.6 CI 加固）：
- detect_life_intent 纯函数：正则意图（想去公园→go_out/this_week/2、散散步→walk、想吃火锅→eat）
- 显式指令（this_turn）："你现在去睡觉" → sleep / priority=3 / horizon=this_turn
- 长度边界：过短/过长 → None
- 节流：同角色 5 分钟内二次调用跳过写库（mock factory 不应被调用）
- 即时指令写库后触发 run_character_tick

v3.3.6 CI 修复：改为纯函数 + mock 测试，不依赖 aiosqlite 文件库（CI Linux 全量下
aiosqlite 线程残留偶发导致写库测试不稳定）。
"""
import asyncio
import time

import app.life.chat_intent as chat_intent
from app.life.chat_intent import detect_life_intent


class _StubResult:
    def scalar_one_or_none(self):
        return None


class _StubDB:
    """最小 async context manager 假 session：记录 commit/assert 误用。"""

    def __init__(self, calls=None):
        self.calls = calls if calls is not None else []

    async def __aenter__(self):
        self.calls.append("enter")
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        return _StubResult()

    def add(self, *args, **kwargs):
        pass

    async def commit(self):
        self.calls.append("commit")


class _BoomDB(_StubDB):
    """节流生效时不应被打开的假 session：打开即失败。"""

    async def __aenter__(self):
        raise AssertionError("节流生效时不应写库")


# ─────────── 纯函数：正则意图 ───────────

def test_detect_想去公园_go_out():
    info = detect_life_intent("我想去公园逛逛")
    assert info == {"action_type": "go_out", "horizon": "this_week", "priority": 2}


def test_detect_散散步_walk():
    assert detect_life_intent("我想出去散散步")["action_type"] == "walk"


def test_detect_想吃火锅_eat():
    assert detect_life_intent("好饿，想吃火锅")["action_type"] == "eat"


def test_detect_无意图_None():
    assert detect_life_intent("今天天气不错") is None


def test_detect_太短或超长_None():
    assert detect_life_intent("去") is None
    assert detect_life_intent("好" * 101) is None


# ─────────── 纯函数：显式指令 ───────────

def test_detect_即时指令_去睡觉():
    info = detect_life_intent("你现在去睡觉")
    assert info == {"action_type": "sleep", "horizon": "this_turn", "priority": 3}


def test_detect_即时指令_出去转转():
    info = detect_life_intent("出去转转")
    assert info["action_type"] == "walk" and info["priority"] == 3


# ─────────── 节流与写库路径（mock factory，无真实 DB） ───────────

def test_节流_5分钟内不重复():
    result = asyncio.run(chat_intent.extract_life_intent(
        1, 100, "我想去公园逛逛",
        session_factory=lambda: _BoomDB(), throttle_state={1: time.monotonic()},
    ))
    assert result == "throttled"
    # 若 throttle 未生效会触发 _BoomDB.__aenter__ 断言


def test_正常路径_写库commit():
    calls = []
    result = asyncio.run(chat_intent.extract_life_intent(
        1, 100, "我想去公园逛逛",
        session_factory=lambda: _StubDB(calls), throttle_state={},
    ))
    assert result == "persisted", f"reason={result} file={chat_intent.__file__} fnline={chat_intent.extract_life_intent.__code__.co_firstlineno}"
    assert calls == ["enter", "commit"]


def test_即时指令_触发run_character_tick():
    calls = []

    def _fake_schedule(character_id, user_id):
        calls.append((character_id, user_id))

    result = asyncio.run(chat_intent.extract_life_intent(
        1, 100, "你现在去睡觉",
        session_factory=lambda: _StubDB(calls), tick_scheduler=_fake_schedule,
        throttle_state={},
    ))
    assert result == "persisted", f"reason={result} file={chat_intent.__file__} fnline={chat_intent.extract_life_intent.__code__.co_firstlineno}"
    assert calls == ["enter", "commit", (1, 100)]


def test_非即时_不触发run_character_tick():
    calls = []

    def _fake_schedule(character_id, user_id):
        calls.append((character_id, user_id))

    result = asyncio.run(chat_intent.extract_life_intent(
        1, 100, "我想去公园逛逛",
        session_factory=lambda: _StubDB(calls), tick_scheduler=_fake_schedule,
        throttle_state={},
    ))
    assert result == "persisted", f"reason={result} file={chat_intent.__file__} fnline={chat_intent.extract_life_intent.__code__.co_firstlineno}"
    assert calls == ["enter", "commit"]