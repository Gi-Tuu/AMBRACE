"""游戏模板注册表（Groups Games Phase 1）。"""
from __future__ import annotations

from app.games.base import GameEngine
from app.games.undercover import UndercoverEngine
from app.games.truth_or_dare import TruthOrDareEngine
from app.games.twenty_q import TwentyQEngine
from app.games.werewolf import WerewolfEngine
from app.games.liars_bar import LiarsBarEngine
from app.games.turtle_soup import TurtleSoupEngine

_REGISTRY: dict[str, type[GameEngine]] = {
    "undercover": UndercoverEngine,
    "truth_or_dare": TruthOrDareEngine,
    "twenty_q": TwentyQEngine,
    "werewolf": WerewolfEngine,
    "liars_bar": LiarsBarEngine,
    "turtle_soup": TurtleSoupEngine,
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
    "werewolf": {
        "name": "狼人杀", "player_mode": "multi",
        "min_players": 4, "max_players": 8, "needs_gm": True,
        "description": "夜晚刀人、白天发言投票，找出狼人",
    },
    "liars_bar": {
        "name": "骗子酒馆", "player_mode": "multi",
        "min_players": 3, "max_players": 5, "needs_gm": True,
        "description": "出牌虚报、跟牌或质疑，看谁最能唬人",
    },
    "turtle_soup": {
        "name": "海龟汤", "player_mode": "dual",
        "min_players": 2, "max_players": 2, "needs_gm": False,
        "description": "用是非问句揭开古怪汤面的真相",
    },
}


def engine_for(game_type: str) -> type[GameEngine]:
    cls = _REGISTRY.get(game_type)
    if cls is None:
        raise ValueError(f"unknown game_type: {game_type}")
    return cls


def list_games() -> list[dict]:
    return [{"game_type": k, **v} for k, v in _GAME_META.items()]
