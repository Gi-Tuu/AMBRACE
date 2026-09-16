# -*- coding: utf-8 -*-
"""#62 Phase 3：游戏内容源（用户 > 内容包 > 内置）+ 成就/统计 测试（2026-09-15）。

纪律：全程走 conftest 会话沙箱库（DATABASE_URL 指向 tmp_path），不碰真实库。
"""
import asyncio
from types import SimpleNamespace


# ────────────── 内容源 ──────────────

def test_builtin_content_roundtrip():
    from app.games import content_store as cs
    cs.register_builtin_content("unitgame", "word_pool", ["A", "B"])
    assert cs.builtin_content("unitgame", "word_pool") == ["A", "B"]
    got = cs.builtin_content("unitgame", "word_pool")
    got.append("X")
    assert cs.builtin_content("unitgame", "word_pool") == ["A", "B"]  # 返回副本，改不动源


def test_plugin_content_only_when_enabled(monkeypatch):
    from app.games import content_store as cs
    from app.plugins import registry as pr

    pack = {"kind": "game_content",
            "items": [{"game_type": "unitgame", "key": "quick_answers", "values": ["Q1", "Q2"]}]}
    monkeypatch.setitem(pr._loaded, "unit_pack", {"info": {"content": pack}})
    monkeypatch.setitem(pr._enabled, "unit_pack", True)
    assert cs.plugin_content("unitgame", "quick_answers") == ["Q1", "Q2"]

    monkeypatch.setitem(pr._enabled, "unit_pack", False)
    assert cs.plugin_content("unitgame", "quick_answers") is None


async def _parents():
    from app.db.database import async_session_factory
    from app.models.user import User
    from app.models.character import AICharacter
    async with async_session_factory() as db:
        await db.merge(User(id=9, username="u9", nickname="U9"))
        await db.merge(AICharacter(id=21, user_id=9, name="C21"))
        await db.commit()


def test_user_override_priority(monkeypatch):
    """优先级：用户自定义 > 插件内容包 > 内置常量（首个非空整段胜出）。"""
    from app.games import content_store as cs
    from app.plugins import registry as pr

    cs.register_builtin_content("unitgame", "quick_answers", ["内置"])
    monkeypatch.setitem(pr._loaded, "unit_pack", {"info": {"content": {
        "kind": "game_content",
        "items": [{"game_type": "unitgame", "key": "quick_answers", "values": ["内容包"]}]}}})
    monkeypatch.setitem(pr._enabled, "unit_pack", True)

    async def _run():
        await _parents()
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            await cs.upsert_user_override(db, user_id=9, game_type="unitgame",
                                          key="quick_answers", values=["我的"])
            await db.commit()
            return await cs.load_user_overrides(db, user_id=9, game_type="unitgame")

    ov = asyncio.run(_run())
    assert cs.builtin_content("unitgame", "quick_answers") == ["内置"]
    assert cs.plugin_content("unitgame", "quick_answers") == ["内容包"]
    assert ov.get("quick_answers") == ["我的"]


# ────────────── 成就与统计 ──────────────

def test_apply_outcome_accumulates():
    from app.games.achievements import _apply_outcome
    row = SimpleNamespace(games_played=0, wins=0, losses=0, draws=0, aborted=0,
                          total_rounds=0, last_played_at=None)
    _apply_outcome(row, "won", 3)
    _apply_outcome(row, "lost", 2)
    _apply_outcome(row, "draw", 1)
    _apply_outcome(row, "aborted", 5)
    assert (row.games_played, row.wins, row.losses, row.draws, row.aborted, row.total_rounds)         == (3, 1, 1, 1, 1, 11)
    assert row.last_played_at is not None


def _session(winner_side="seat_0", rounds=4):
    return SimpleNamespace(id=1, game_type="twenty_q", round=rounds, user_id=9,
                           winner_side=winner_side, config_json="{}")


def _engine():
    players = [
        SimpleNamespace(seat=0, player_type="user", user_id=9, character_id=None,
                        is_spectator=False, role="guesser"),
        SimpleNamespace(seat=1, player_type="ai", user_id=None, character_id=21,
                        is_spectator=False, role="thinker"),
    ]
    return SimpleNamespace(players=players, state={})


def test_record_game_result_stats_and_achievements():
    from app.games import achievements as A

    async def _run():
        await _parents()
        from sqlalchemy import delete, select
        from app.db.database import async_session_factory
        from app.models.game import GameStats, GameAchievement
        async with async_session_factory() as db:
            await db.execute(delete(GameAchievement))
            await db.execute(delete(GameStats))
            await db.commit()

            session, engine = _session(), _engine()
            await A.record_game_result(db, session, engine)      # 第 1 次：正常结算
            await A.record_game_result(db, session, engine)      # 第 2 次：幂等，不应重复计
            await db.commit()

            stats = (await db.execute(select(GameStats).order_by(GameStats.id))).scalars().all()
            achs = (await db.execute(select(GameAchievement))).scalars().all()
            return ([{ "u": s.user_id, "c": s.character_id, "g": s.game_type,
                       "played": s.games_played, "wins": s.wins, "losses": s.losses,
                       "rounds": s.total_rounds} for s in stats],
                    [(a.user_id, a.character_id, a.achievement_key) for a in achs])

    stats, achs = asyncio.run(_run())

    user_rows = [s for s in stats if s["c"] is None]
    ai_rows = [s for s in stats if s["c"] == 21]
    assert len(user_rows) == 1 and len(ai_rows) == 1
    assert user_rows[0]["played"] == 1 and user_rows[0]["wins"] == 1      # 幂等：只计一次
    assert user_rows[0]["rounds"] == 4
    assert ai_rows[0]["losses"] == 1 and ai_rows[0]["wins"] == 0
    # 成就只解锁一次
    assert achs.count((9, None, "first_win")) == 1
    assert achs.count((9, None, "first_game")) == 1
    assert achs.count((9, 21, "first_game")) == 1


def test_abort_not_counted_as_win():
    from app.games import achievements as A

    async def _run():
        await _parents()
        from sqlalchemy import delete, select
        from app.db.database import async_session_factory
        from app.models.game import GameStats, GameAchievement
        async with async_session_factory() as db:
            await db.execute(delete(GameAchievement))
            await db.execute(delete(GameStats))
            await db.commit()

            session, engine = _session(winner_side=""), _engine()
            await A.record_game_result(db, session, engine, aborted=True)
            await db.commit()

            rows = (await db.execute(select(GameStats))).scalars().all()
            achs = (await db.execute(select(GameAchievement))).scalars().all()
            return ([(r.user_id, r.character_id, r.games_played, r.wins, r.aborted) for r in rows],
                    len(achs))

    rows, ach_count = asyncio.run(_run())
    assert rows, "aborted 局也要写统计行"
    assert all(r[3] == 0 for r in rows), "无胜负终止绝不进胜场"
    assert all(r[2] == 0 for r in rows), "aborted 不计 games_played"
    assert all(r[4] == 1 for r in rows), "aborted 单独计数"
    assert ach_count == 0
