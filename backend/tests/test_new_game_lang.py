# -*- coding: utf-8 -*-
"""新建对局路径的语言解析测试（2026-09-19 复检 P3-⑤）。

背景：`self.lang` 原先只在 `GameEngine.load()` 里从 User.lang 赋值，而创建对局路径
（app/api/games.py `_create_session_in_db`）是 engine_cls(session) → setup()，不经过 load()，
于是新建局首轮的匿名名回落会用 settings.default_lang 而非用户语言。

修复：语言解析抽成公共方法 `GameEngine.resolve_lang(db)`，创建路径在 setup() 前显式调一次。
本文件断言：创建路径跑完后 engine.lang == 创建者 User.lang（用例里刻意取 en，
与服务端默认 zh 不同，避免「碰巧等于默认值」的假绿）。
"""
import asyncio

from _dbclone import clone_engine, make_session_factory

from app.api import games as games_api
from app.games.base import GameEngine

_CREATOR_LANG = "en"  # 与 settings.default_lang（zh）不同，才能真正证明取自 User.lang


def _build_factory(tmp_path):
    db_path = (tmp_path / "lang.db").as_posix()
    engine = clone_engine(db_path)
    return engine, make_session_factory(engine)


async def _init_schema(factory):
    import app.models  # noqa: F401
    from app.models.character import AICharacter
    from app.models.user import User

    async with factory() as db:
        db.add(User(id=1, username="u1", nickname="用户一", lang=_CREATOR_LANG))
        db.add(User(id=2, username="u2", nickname="用户二", lang="zh"))
        for i in range(101, 103):
            db.add(AICharacter(id=i, user_id=1, name=f"角色{i}", personality="外向",
                               chat_style="口语化", relation_type="朋友", is_active=True))
        await db.commit()


def test_create_session_resolves_creator_lang(monkeypatch, tmp_path):
    """创建路径结束后 engine.lang == 创建者语言（en），而非服务端默认 zh。"""
    engine, factory = _build_factory(tmp_path)
    asyncio.run(_init_schema(factory))
    monkeypatch.setattr("app.api.games.async_session_factory", factory)

    try:
        async def _run():
            async with factory() as db:
                _session, eng = await games_api._create_session_in_db(
                    db, user_id=1, game_type="twenty_q", player_ids=[101],
                    spectator_ids=[], user_as_player=True, group_id=None,
                    trigger="user_initiated",
                )
                return eng.lang

        assert asyncio.run(_run()) == _CREATOR_LANG
    finally:
        asyncio.run(engine.dispose())


def test_create_session_calls_resolve_lang_once(monkeypatch, tmp_path):
    """创建路径确实显式调用了 resolve_lang（计数断言，防止后续重构把调用删掉）。"""
    engine, factory = _build_factory(tmp_path)
    asyncio.run(_init_schema(factory))
    monkeypatch.setattr("app.api.games.async_session_factory", factory)

    calls = []
    real = GameEngine.resolve_lang

    async def _counting(self, db):
        calls.append(self.game_type)
        await real(self, db)

    monkeypatch.setattr(GameEngine, "resolve_lang", _counting)

    try:
        async def _run():
            async with factory() as db:
                await games_api._create_session_in_db(
                    db, user_id=1, game_type="twenty_q", player_ids=[101],
                    spectator_ids=[], user_as_player=True, group_id=None,
                    trigger="user_initiated",
                )

        asyncio.run(_run())
    finally:
        asyncio.run(engine.dispose())

    assert calls == ["twenty_q"], calls
