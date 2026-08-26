"""游戏模板注册表（Groups Games Phase 1）。"""
from __future__ import annotations

from app.games.base import GameEngine
from app.games.undercover import UndercoverEngine
from app.games.truth_or_dare import TruthOrDareEngine
from app.games.twenty_q import TwentyQEngine

_REGISTRY: dict[str, type[GameEngine]] = {
    "undercover": UndercoverEngine,
    "truth_or_dare": TruthOrDareEngine,
    "twenty_q": TwentyQEngine,
    # Phase 2: "werewolf", "liars_bar", "turtle_soup"
}

_GAME_META = {
    "undercover": {
        "name": "谁是卧底", "player_mode": "multi",
        "min_players": 4, "max_players": 8, "needs_gm": True,
        "description": "描述词语、投票找出卧底",
    },
    "truth_or_dare": {
        "name": "真心话大冒险", "player_mode": "dual",
        "min_players": 2, "max_players": 2, "needs_gm": False,
        "description": "轮流选择真心话或大冒险",
    },
    "twenty_q": {
        "name": "猜词20问", "player_mode": "single",
        "min_players": 2, "max_players": 2, "needs_gm": False,
        "description": "用20个是非问句猜出对方想的词",
    },
}


def engine_for(game_type: str) -> type[GameEngine]:
    cls = _REGISTRY.get(game_type)
    if cls is None:
        raise ValueError(f"unknown game_type: {game_type}")
    return cls


def list_games() -> list[dict]:
    return [{"game_type": k, **v} for k, v in _GAME_META.items()]
