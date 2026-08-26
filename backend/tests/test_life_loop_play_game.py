# -*- coding: utf-8 -*-
"""Life Loop 自主开局测试（群聊游戏 Phase 2，2026-08-26）。

- decide 在可用时返回 play_game；不可用（当日已开局/角色不足/用户在场）不触发；
- _start_group_game 创建 GameSession(trigger=character_suggested) 且用户观战；
- 每日每角色 ≤1 局：开局后 _play_game_played_today 返回 1。
"""
import asyncio
import os
import random
import tempfile
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.life.decision import StateSnapshot, Decision, decide
from app.life.life_loop import LifeLoopTask
from app.models.game import GameSession, GamePlayer


async def _noop_ai(sid: int) -> None:
    return


@pytest.fixture
def life_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="life_play_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        from app.models.character import AICharacter
        from app.models.user import User
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户一"))
            for i in range(101, 106):
                db.add(AICharacter(id=i, user_id=1, name=f"角色{i}", personality="外向",
                                   chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr("app.api.games.async_session_factory", factory)
    monkeypatch.setattr("app.api.games._resume_ai_turns", _noop_ai)
    monkeypatch.setattr("app.memory.service.async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _snap(*, play_game_available=True, user_active=False, needs=None, dnd=False):
    return StateSnapshot(
        character_id=101, user_id=1, energy=80, focus=50,
        needs=needs or {"social": 80, "entertainment": 80},
        phase="afternoon", mood=60, fatigue=30, anger=10,
        location="home", current_room="living",
        user_active_recently=user_active, dnd=dnd,
        play_game_available=play_game_available,
    )


# ---------------- 决策：可用时触发 play_game ----------------
def test_decide_returns_play_game_when_available():
    random.seed(0)  # 固定随机，确保加权选择命中 play_game（可用时它是最高分候选）
    d = decide(_snap(play_game_available=True))
    assert d.action == "play_game"


# ---------------- 决策：不可用时不触发 ----------------
def test_decide_not_trigger_when_budget_used():
    d = decide(_snap(play_game_available=False))
    assert d.action != "play_game"


def test_decide_not_trigger_when_peer_insufficient():
    d = decide(_snap(play_game_available=False, needs={"social": 100, "entertainment": 100}))
    assert d.action != "play_game"


def test_decide_not_trigger_when_user_active():
    d = decide(_snap(play_game_available=False, user_active=True))
    assert d.action != "play_game"


# ---------------- 创建会话 + 用户观战 ----------------
def test_start_group_game_creates_session(life_db):
    async def _do():
        async with life_db() as db:
            task = LifeLoopTask()
            char = SimpleNamespace(id=101, user_id=1, name="角色101")
            decision = Decision("play_game", reason="weighted_choice")
            snap = _snap(play_game_available=True)
            game = await task._start_group_game(db, char, decision, snap)
            assert game is not None
            assert game["game_type"] in ("werewolf", "liars_bar")
            assert game["session_id"] > 0
            # 会话落库：trigger=character_suggested
            srow = (await db.execute(
                select(GameSession).where(GameSession.id == game["session_id"])
            )).scalars().first()
            assert srow is not None
            assert srow.trigger == "character_suggested"
            assert srow.group_id is None
            # 玩家=AI 角色，用户观战（非玩家）
            plist = (await db.execute(
                select(GamePlayer).where(GamePlayer.session_id == srow.id)
            )).scalars().all()
            ai_players = [p for p in plist if p.player_type == "ai" and not p.is_spectator]
            spectator_users = [p for p in plist if p.player_type == "user" and p.is_spectator]
            assert len(ai_players) >= 2
            assert len(spectator_users) == 1
            # 发起角色 101 必在玩家中
            assert any(p.character_id == 101 for p in ai_players)
            return game, srow.id
    game, sid = asyncio.run(_do())
    assert game["session_id"] == sid


# ---------------- 当日限额：开局后不再触发 ----------------
def test_play_game_played_today_limits(life_db):
    async def _do():
        async with life_db() as db:
            task = LifeLoopTask()
            before = await task._play_game_played_today(db, 101)
            assert before == 0
            char = SimpleNamespace(id=101, user_id=1, name="角色101")
            await task._start_group_game(db, char, Decision("play_game"), _snap(play_game_available=True))
            after = await task._play_game_played_today(db, 101)
            return before, after
    before, after = asyncio.run(_do())
    assert before == 0 and after == 1


# ---------------- v3.3.6 审查修复：同用户已有自主对局时不重复开 ----------------
def test_start_group_game_skips_when_active_game_exists(life_db):
    async def _do():
        async with life_db() as db:
            task = LifeLoopTask()
            char = SimpleNamespace(id=101, user_id=1, name="角色101")
            first = await task._start_group_game(db, char, Decision("play_game"), _snap(play_game_available=True))
            second = await task._start_group_game(db, char, Decision("play_game"), _snap(play_game_available=True))
            return first, second
    first, second = asyncio.run(_do())
    assert first is not None
    assert second is None
