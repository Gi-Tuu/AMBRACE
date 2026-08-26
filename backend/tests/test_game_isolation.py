# -*- coding: utf-8 -*-
"""游戏信息隔离测试（群聊游戏 Phase 1，2026-08-26）。

- 断言卧底词不出现在平民 AI 的 build_ai_prompt 输出中（反之亦然）；
- 断言 game_memories 不进主记忆（main memories/search_memories 不含游戏详情）；
- 断言 game_summary 指针存在，且每角色最多保留 5 条（超出软删最旧）。
"""
import asyncio
import dataclasses
import json
import os
import random
import tempfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.games.undercover import UndercoverEngine
from app.games.memory_bridge import finalize_game, _trim_summary_pointers


class _Session:
    def __init__(self, game_type, user_id=1):
        self.id = 1
        self.user_id = user_id
        self.group_id = None
        self.game_type = game_type
        self.player_mode = ""
        self.status = "created"
        self.round = 0
        self.phase = ""
        self.state_json = "{}"
        self.winner_side = None
        self.trigger = "user_initiated"
        self.archive_json = "{}"
        self.started_at = datetime.now(timezone.utc)
        self.finished_at = None


class _Player:
    def __init__(self, seat, player_type="ai", character_id=None, user_id=None):
        self.seat = seat
        self.player_type = player_type
        self.character_id = character_id
        self.user_id = user_id
        self.role = ""
        self.alive = True
        self.score = 0
        self.is_spectator = False
        self.private_json = "{}"


def _make_engine(players, user_id=1):
    session = _Session("undercover", user_id=user_id)
    engine = UndercoverEngine(session)
    engine.players = players
    meta = {p.seat: {"name": f"座{p.seat}", "character_id": p.character_id,
                     "personality": "外向", "chat_style": "口语化", "relation_type": "朋友"}
            for p in players}
    engine.build_player_meta(meta)
    return engine, session


def _sync(coro):
    return asyncio.run(coro)


# ---------------- 信息隔离：词语不出现在他人 prompt ----------------
def test_undercover_word_not_in_other_prompt():
    random.seed(21)
    players = [_Player(0, "user", user_id=1)] + [
        _Player(i, "ai", character_id=100 + i) for i in range(1, 4)
    ]
    engine, _ = _make_engine(players)
    _sync(engine.setup())
    pair = engine.state["pair"]
    for p in engine.players:
        if p.is_spectator:
            continue
        ctx = engine.build_ai_prompt(p.seat)
        my_word = ctx.my_view.private.get("word")
        other_word = pair[0] if my_word == pair[1] else pair[1]
        prompt_text = json.dumps(
            {**ctx.__dict__, "my_view": dataclasses.asdict(ctx.my_view)}, ensure_ascii=False
        )
        # 别人的词绝不能出现在我的 prompt 里（只允许出现我自己的词）
        assert other_word not in prompt_text
        # 我自己的词允许出现（作为"你的身份/手牌"）
        assert my_word in prompt_text


def test_undercover_spectator_has_no_word():
    random.seed(31)
    players = [_Player(0, "user", user_id=1)] + [
        _Player(i, "ai", character_id=100 + i) for i in range(1, 4)
    ] + [_Player(4, "ai", character_id=104)]
    # 把 4 号置为观战者
    players[-1].is_spectator = True
    engine, _ = _make_engine(players)
    _sync(engine.setup())
    sp = engine.view_for(4)
    assert sp.private == {}


# ---------------- 游戏记忆隔离（DB） ----------------
@pytest.fixture
def game_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="game_isolation_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        from app.models.game import GameSession
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(GameSession(user_id=1, game_type="undercover", player_mode="multi",
                               status="finished", round=3, phase="result", winner_side="civilians"))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr("app.memory.service.async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _finalize_engine(session_id):
    players = [_Player(0, "user", user_id=1)] + [
        _Player(1, "ai", character_id=101), _Player(2, "ai", character_id=102),
        _Player(3, "ai", character_id=103),
    ]
    engine, session = _make_engine(players)
    session.id = session_id
    session.status = "finished"
    session.round = 3
    session.winner_side = "civilians"
    session.finished_at = datetime.now(timezone.utc)
    # 覆写角色与词（平民=词A，1 号=卧底=词B）
    engine.state["pair"] = ["苹果", "梨"]
    for p in engine.players:
        p.role = "undercover" if p.seat == 1 else "civilian"
        p.private_json = {"word": "梨" if p.seat == 1 else "苹果"}
    engine._events = [
        {"event_type": "announce", "content": "开始", "phase": "start", "round": 1, "visibility": "public", "created_at": None},
        {"event_type": "announce", "content": "平民获胜", "phase": "result", "round": 3, "visibility": "public", "created_at": None},
    ]
    return engine, session


def test_game_memories_not_in_main_memory(game_db):
    from app.models.game import GameSession, GameMemory
    from app.models.memory import Memory

    async def _do():
        async with game_db() as db:
            row = (await db.execute(select(GameSession).where(GameSession.user_id == 1))).scalars().first()
            sid = row.id
        engine, session = _finalize_engine(sid)
        async with game_db() as db:
            await finalize_game(db, session, engine)
        async with game_db() as db:
            gm = (await db.execute(select(GameMemory).where(GameMemory.session_id == sid))).scalars().all()
            ptr = (await db.execute(
                select(Memory).where(Memory.source == "game", Memory.sub_type == "game_summary")
            )).scalars().all()
            detail = (await db.execute(
                select(Memory).where(Memory.source == "game", Memory.sub_type != "game_summary")
            )).scalars().all()
            return gm, ptr, detail

    gm, ptr, detail = asyncio.run(_do())
    assert len(gm) == 3  # 3 个 AI 角色各一条 game_memories
    assert len(ptr) == 3  # 每个 AI 角色各一条 game_summary 指针
    # 游戏详情（source=game 且 sub_type != game_summary）绝不进主记忆
    assert len(detail) == 0


def test_game_summary_pointer_cap(game_db):
    from app.models.memory import Memory

    async def _do():
        from app.memory.service import save_memory
        for i in range(6):
            await save_memory(user_id=1, character_id=101, memory_type="event",
                              content=f"和用户玩了谁是卧底第{i}局", importance=35.0,
                              sub_type="game_summary", source="game", skip_dedup=True)
        async with game_db() as db:
            await _trim_summary_pointers(db, 101)
            await db.commit()
            active = (await db.execute(
                select(Memory).where(
                    Memory.character_id == 101, Memory.source == "game",
                    Memory.sub_type == "game_summary", Memory.is_archived == False,  # noqa: E712
                )
            )).scalars().all()
            return len(active)

    assert asyncio.run(_do()) == 5
