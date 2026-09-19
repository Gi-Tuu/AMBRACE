# -*- coding: utf-8 -*-
"""P3-2（2026-09-18）：_resume_ai_turns 异常/非 playing 退出必须清理 guardrails._REGISTRY，
同时「正常轮到用户、对局继续」的返回路径绝不清理 guard。

验证手段：monkeypatch async_session_factory / engine_for / ai_decide，避免连真实库；
直接观察模块级 _REGISTRY 在退出后是否仍持有 session_id。沿用项目约定：sync test + asyncio.run。
"""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.api import games as games_api
from app.games.guardrails import _REGISTRY, drop_guard, get_guard

SID = 91237

# 测试期由引擎实例读取的「当前回合座位」：None=轮到用户，0=AI 回合
_STATE = {"seat": 0}


class _FakeSession:
    id = SID
    status = "playing"
    game_type = "werewolf"
    round = 0
    phase = ""
    user_id = 1
    state_json = "{}"


class _FakeResult:
    def all(self):
        return []


class _FakeDB:
    async def get(self, model, ident):
        return _FakeSession()

    async def execute(self, *a, **k):
        return _FakeResult()


@asynccontextmanager
async def _fake_factory():
    yield _FakeDB()


class _FakeEngine:
    def __init__(self, session):
        self.session = session

    async def load(self, db):
        return None

    def current_turn_seat(self, *a, **k):
        return _STATE["seat"]

    def is_ai(self, seat):
        return seat is not None and seat == _STATE["seat"]

    def player_at(self, seat):
        return SimpleNamespace() if seat is not None else None


async def _raise_ai_decide(engine, seat):
    raise RuntimeError("injected ai_decide failure")


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    get_guard(SID)  # 预置一条 guard，模拟「已在对局中」
    monkeypatch.setattr(games_api, "async_session_factory", _fake_factory)
    monkeypatch.setattr(games_api, "engine_for", lambda gt: _FakeEngine)
    yield
    drop_guard(SID)  # 清理，避免污染其它用例


def test_abnormal_exit_clears_guard(monkeypatch):
    """ai_decide 抛异常（异常终局）→ 函数退出后 _REGISTRY 必须被清理。"""
    _STATE["seat"] = 0  # AI 回合，会走到 ai_decide
    monkeypatch.setattr(games_api, "ai_decide", _raise_ai_decide)
    asyncio.run(games_api._resume_ai_turns(SID))
    assert SID not in _REGISTRY, "异常退出后 guard 应已清理，不得泄漏"


def test_normal_user_turn_keeps_guard(monkeypatch):
    """正常轮到用户（current_turn_seat=None）→ 返回后 guard 必须保留（不被误清）。"""
    _STATE["seat"] = None  # 轮到用户 / 已结束分支
    asyncio.run(games_api._resume_ai_turns(SID))
    assert SID in _REGISTRY, "正常轮到用户返回路径不得清理 guard（用户下一步还要累计）"


def test_session_not_playing_clears_guard(monkeypatch):
    """session 已不是 playing（db 返回非 playing）→ 退出后清理 guard。"""

    class _EndedSession(_FakeSession):
        status = "finished"

    class _EndedDB(_FakeDB):
        async def get(self, model, ident):
            return _EndedSession()

    @asynccontextmanager
    async def _ended_factory():
        yield _EndedDB()

    _STATE["seat"] = 0
    monkeypatch.setattr(games_api, "async_session_factory", _ended_factory)
    asyncio.run(games_api._resume_ai_turns(SID))
    assert SID not in _REGISTRY, "session 非 playing 退出后应清理 guard"
