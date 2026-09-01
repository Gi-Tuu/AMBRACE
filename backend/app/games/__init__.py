"""游戏领域包（群聊游戏 Phase 1，2026-08-26）。

引擎框架 + 三款首发游戏 + AI 玩家决策 + 记忆桥接 + 游乐手札。
"""
from app.games.base import GameEngine, ActionResult, GameContext, PlayerView
from app.games.registry import engine_for, list_games
from app.games.undercover import UndercoverEngine
from app.games.truth_or_dare import TruthOrDareEngine
from app.games.twenty_q import TwentyQEngine
from app.games.werewolf import WerewolfEngine
from app.games.liars_bar import LiarsBarEngine
from app.games.turtle_soup import TurtleSoupEngine

__all__ = [
    "GameEngine",
    "ActionResult",
    "GameContext",
    "PlayerView",
    "engine_for",
    "list_games",
    "UndercoverEngine",
    "TruthOrDareEngine",
    "TwentyQEngine",
    "WerewolfEngine",
    "LiarsBarEngine",
    "TurtleSoupEngine",
]
