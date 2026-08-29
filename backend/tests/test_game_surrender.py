# -*- coding: utf-8 -*-
"""游戏投降机制测试（v3.3.10）。

确定性规则（与具体游戏解耦，仅基类 + API 层）：
- 双人（含单人、多人剩 2 人）：投降方判负、对方胜，对局结束；
- 观战者不能投降；
- 多人 3+：投降方转观战，对局继续。
桩参考 test_game_engine.py 的 _Session/_Player/_make/build_player_meta。
"""
import asyncio

from app.games.undercover import UndercoverEngine
from app.games.truth_or_dare import TruthOrDareEngine


class _Session:
    """轻量 GameSession 桩（引擎单元测试不需要真实 DB）。"""

    def __init__(self, game_type, user_id=1, group_id=None):
        self.id = 1
        self.user_id = user_id
        self.group_id = group_id
        self.game_type = game_type
        self.player_mode = ""
        self.status = "created"
        self.round = 0
        self.phase = ""
        self.state_json = "{}"
        self.winner_side = None
        self.trigger = "user_initiated"
        self.archive_json = "{}"
        self.started_at = None
        self.finished_at = None


class _Player:
    def __init__(self, seat, player_type="ai", character_id=None, user_id=None, spectator=False):
        self.seat = seat
        self.player_type = player_type
        self.character_id = character_id
        self.user_id = user_id
        self.role = ""
        self.alive = True
        self.score = 0
        self.is_spectator = spectator
        self.private_json = "{}"


def _make(engine_cls, game_type, players, user_id=1):
    session = _Session(game_type, user_id=user_id)
    engine = engine_cls(session)
    engine.players = players
    meta = {}
    for p in players:
        meta[p.seat] = {
            "name": ("用户" if p.player_type == "user" else f"角色{p.seat}"),
            "character_id": p.character_id,
            "personality": "外向开朗",
            "chat_style": "口语化",
            "relation_type": "朋友",
        }
    engine.build_player_meta(meta)
    return engine


def _sync(coro):
    return asyncio.run(coro)


async def _engine(cls, players):
    """按 test_game_engine 桩构造、走正常 setup，再返回引擎。"""
    e = _make(cls, cls.game_type, players)
    await e.setup()
    return e


# ---------------- 双人：投降结束，对方胜 ----------------
def test_dual_surrender_ends_and_other_wins():
    players = [_Player(0, "user", user_id=1), _Player(1, "ai", character_id=101)]
    e = _sync(_engine(TruthOrDareEngine, players))
    out = _sync(e.apply_surrender(0))
    assert out["ok"] is True
    assert out["end"] is True
    assert out["winner"] == "seat_1"
    assert e.players[0].alive is False
    assert e.players[0].is_spectator is True
    assert len(e.in_play_players()) == 1
    # 投降方被判负、对方作为唯一在场玩家胜出
    assert e.players[1].alive is True and e.players[1].is_spectator is False


# ---------------- 观战者不能投降 ----------------
def test_spectator_cannot_surrender():
    players = [_Player(0, "ai", character_id=100), _Player(1, "ai", character_id=101)]
    e = _sync(_engine(TruthOrDareEngine, players))
    # 先把 1 号置为观战（setup 之后再改，避免双人 setup 缺人）
    e.players[1].is_spectator = True
    out = _sync(e.apply_surrender(1))
    assert out["ok"] is False
    assert "error" in out


# ---------------- 多人 3+：转观战，对局继续 ----------------
def test_multi_three_plus_becomes_spectator_and_continues():
    players = [_Player(i, "ai", character_id=100 + i) for i in range(4)]
    e = _sync(_engine(UndercoverEngine, players))
    out = _sync(e.apply_surrender(0))
    assert out["ok"] is True
    assert out["end"] is False
    assert e.players[0].is_spectator is True
    assert e.players[0].alive is False
    assert len(e.in_play_players()) == 3


# ---------------- 多人剩 2 人：投降结束 ----------------
def test_multi_down_to_two_surrender_ends():
    players = [_Player(0, "ai", character_id=100), _Player(1, "ai", character_id=101)]
    e = _sync(_engine(UndercoverEngine, players))
    out = _sync(e.apply_surrender(0))
    assert out["ok"] is True
    assert out["end"] is True
    assert out["winner"] == "seat_1"
    assert e.players[0].is_spectator is True
    assert len(e.in_play_players()) == 1
