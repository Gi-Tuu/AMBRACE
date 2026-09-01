# -*- coding: utf-8 -*-
"""B5 回归测试（2026-09-01 审查）：liars_bar 结算路径真实会话验证。

事故：liars_bar 引擎把 dict 直接赋给 GamePlayer.private_json（Text 列），结算路径
_settle_game 原先不做 persist_state 就跑 finalize_game——其 SELECT 触发 autoflush
把 dict 绑 Text 列打爆事务，finish 的 status/winner/archive 全部回滚（对局卡 playing）。
本测试走真实 async 会话（不打桩引擎/不打桩 ORM），结算后 commit 并重新取回验证。
"""
import asyncio
import json
import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.games import _create_session_in_db, _settle_game


@pytest.fixture
def game_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="b5_settle_test_")
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
            for i in (101, 102, 103):
                db.add(AICharacter(id=i, user_id=1, name=f"角色{i}", personality="外向",
                                   chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    yield factory
    asyncio.run(engine.dispose())


def test_liars_bar_settle_persists_finished(game_db):
    """真实建局（3 玩家）→ 直调 _settle_game → commit → 重新取回：
    status=finished、winner_side 非空、archive_json 非空、private_json 是 str 且 json.loads 不抛。"""
    factory = game_db

    async def _run():
        from app.models.game import GamePlayer, GameSession
        async with factory() as db:
            session, eng = await _create_session_in_db(
                db, user_id=1, game_type="liars_bar",
                player_ids=[101, 102, 103], spectator_ids=[],
                user_as_player=False, group_id=None, trigger="test",
            )
            sid = session.id
            # 引擎内存态此时是 dict 手牌（liars_bar.setup 赋值），未 persist 前不 commit 行
            await _settle_game(db, session, eng, winner="1")
            await db.commit()
        # 新会话重新取回（验证已真正落库而非内存对象）
        async with factory() as db:
            row = await db.get(GameSession, sid)
            assert row is not None
            assert row.status == "finished", row.status
            assert row.winner_side, row.winner_side
            assert row.archive_json, "archive_json 不应为空"
            json.loads(row.archive_json)  # 归档必须是合法 JSON 字符串
            json.loads(row.state_json)
            players = (await db.execute(
                __import__("sqlalchemy").select(GamePlayer).where(GamePlayer.session_id == sid)
            )).scalars().all()
            assert players, "玩家行缺失"
            for p in players:
                assert isinstance(p.private_json, str), f"private_json 仍是 {type(p.private_json)}"
                json.loads(p.private_json)  # 不抛

    asyncio.run(_run())


def test_settle_autoflush_survives_dirty_engine_rows(game_db):
    """回归主断言：结算流程内（finalize_game 的 SELECT 触发 autoflush 时）
    引擎 dict 手牌已由 persist_state 序列化——不再出现 dict 绑 Text 列的 ProgrammingError。"""
    factory = game_db

    async def _run():
        from app.models.game import GamePlayer
        async with factory() as db:
            session, eng = await _create_session_in_db(
                db, user_id=1, game_type="liars_bar",
                player_ids=[101, 102, 103], spectator_ids=[],
                user_as_player=False, group_id=None, trigger="test",
            )
            # 复现事故前提：对局中 liars_bar._set_cards 会把引擎玩家行的 private_json
            # 重新赋成 dict（建局时的已在 _create_session_in_db 里 persist 序列化）。
            # 此处模拟一次行动后的脏内存态——修复前结算内 autoflush 在此炸 ProgrammingError。
            dirty = False
            for pl in eng.players:
                if getattr(pl, "id", None):
                    pl.private_json = {"cards": [3, 5, 7]}
                    dirty = True
            assert dirty, "无引擎玩家可污染"
            await _settle_game(db, session, eng, winner="1")  # 修复前此处抛 ProgrammingError
            await db.commit()

    asyncio.run(_run())
