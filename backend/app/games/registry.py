"""游戏模板注册表（Groups Games Phase 1；X1 开放注册口，2026-08-31）。

内置游戏与插件扩展包走同一注册入口（消除静态字典双轨）：
- 内核启动时 register_game_type(...) 注册内置 6 款（source="builtin"）；
- 插件 main.py 加载期经 sdk.register_game(...) 注册（source=插件名）；
- engine_for / list_games 按来源启用状态过滤：builtin 恒可用，插件来源需插件启用；
- 插件目录被移除后 sync_plugins_db 经 unregister_games_not_in 清理残留注册。

不变量（内核保留）：房间、回合、主持、游戏记忆隔离（memory_bridge）——扩展包只提供规则引擎。
"""
from __future__ import annotations

import re

from app.games.base import GameEngine

_REGISTRY: dict[str, type[GameEngine]] = {}
_GAME_META: dict[str, dict] = {}
_GAME_SOURCE: dict[str, str] = {}  # game_type -> "builtin" | 插件名

_GAME_TYPE_RE = re.compile(r"^[a-z0-9_]{2,24}$")
_META_REQUIRED = ("name", "player_mode", "min_players", "max_players", "needs_gm", "description")


def register_game_type(game_type: str, engine_cls: type[GameEngine], meta: dict, source: str = "builtin") -> None:
    """开放注册口（X1）：内置与插件同一入口；重复注册/非法参数直接抛错（加载期暴露问题）。"""
    if not isinstance(game_type, str) or not _GAME_TYPE_RE.match(game_type or ""):
        raise ValueError(f"非法 game_type（需 2-24 位小写字母/数字/下划线）: {game_type!r}")
    if game_type in _REGISTRY:
        raise ValueError(f"game_type already registered: {game_type}")
    if not (isinstance(engine_cls, type) and issubclass(engine_cls, GameEngine)):
        raise ValueError(f"engine_cls 必须是 GameEngine 子类: {engine_cls!r}")
    meta = dict(meta or {})
    missing = [k for k in _META_REQUIRED if k not in meta]
    if missing:
        raise ValueError(f"meta 缺少必填字段: {missing}")
    mode = str(meta["player_mode"])
    if mode not in ("single", "dual", "multi"):
        raise ValueError(f"player_mode 必须是 single/dual/multi: {mode!r}")
    if not (0 <= int(meta["min_players"]) <= int(meta["max_players"]) <= 16):
        raise ValueError("人数范围非法（0 ≤ min ≤ max ≤ 16）")
    _REGISTRY[game_type] = engine_cls
    _GAME_META[game_type] = {
        "name": str(meta["name"])[:32],
        "player_mode": mode,
        "min_players": int(meta["min_players"]),
        "max_players": int(meta["max_players"]),
        "needs_gm": bool(meta["needs_gm"]),
        "description": str(meta["description"])[:120],
    }
    _GAME_SOURCE[game_type] = str(source or "builtin")


def unregister_games_for_source(source: str) -> list[str]:
    """注销某来源注册的全部游戏（插件卸载用）。返回被注销的 game_type 列表。"""
    removed = [gt for gt, src in _GAME_SOURCE.items() if src == source]
    for gt in removed:
        _REGISTRY.pop(gt, None)
        _GAME_META.pop(gt, None)
        _GAME_SOURCE.pop(gt, None)
    return removed


def unregister_games_not_in(sources: set[str]) -> list[str]:
    """清理：插件来源但来源已不在加载集合中的注册（sync_plugins_db 重扫后调用）。"""
    stale = [gt for gt, src in _GAME_SOURCE.items() if src != "builtin" and src not in sources]
    for gt in stale:
        _REGISTRY.pop(gt, None)
        _GAME_META.pop(gt, None)
        _GAME_SOURCE.pop(gt, None)
    return stale


def _source_enabled(game_type: str) -> bool:
    source = _GAME_SOURCE.get(game_type, "builtin")
    if source == "builtin":
        return True
    try:
        from app.plugins.registry import _enabled
        return bool(_enabled.get(source, False))
    except Exception:
        return False


def engine_for(game_type: str) -> type[GameEngine]:
    cls = _REGISTRY.get(game_type)
    if cls is None or not _source_enabled(game_type):
        raise ValueError(f"unknown game_type: {game_type}")
    return cls


def list_games() -> list[dict]:
    return [
        {"game_type": k, **v}
        for k, v in _GAME_META.items()
        if _source_enabled(k)
    ]


# ── 内置游戏注册（与插件同一入口；X1 前为硬编码字典）──
from app.games.undercover import UndercoverEngine  # noqa: E402
from app.games.truth_or_dare import TruthOrDareEngine  # noqa: E402
from app.games.twenty_q import TwentyQEngine  # noqa: E402
from app.games.werewolf import WerewolfEngine  # noqa: E402
from app.games.liars_bar import LiarsBarEngine  # noqa: E402
from app.games.turtle_soup import TurtleSoupEngine  # noqa: E402

register_game_type("undercover", UndercoverEngine, {
    "name": "谁是卧底", "player_mode": "multi",
    "min_players": 4, "max_players": 8, "needs_gm": True,
    "description": "描述词语、投票找出卧底",
})
register_game_type("truth_or_dare", TruthOrDareEngine, {
    "name": "真心话大冒险", "player_mode": "dual",
    "min_players": 2, "max_players": 2, "needs_gm": False,
    "description": "轮流选择真心话或大冒险",
})
register_game_type("twenty_q", TwentyQEngine, {
    "name": "猜词20问", "player_mode": "single",
    "min_players": 2, "max_players": 2, "needs_gm": False,
    "description": "用20个是非问句猜出对方想的词",
})
register_game_type("werewolf", WerewolfEngine, {
    "name": "狼人杀", "player_mode": "multi",
    "min_players": 4, "max_players": 8, "needs_gm": True,
    "description": "夜晚刀人、白天发言投票，找出狼人",
})
register_game_type("liars_bar", LiarsBarEngine, {
    "name": "骗子酒馆", "player_mode": "multi",
    "min_players": 3, "max_players": 5, "needs_gm": True,
    "description": "出牌虚报、跟牌或质疑，看谁最能唬人",
})
register_game_type("turtle_soup", TurtleSoupEngine, {
    "name": "海龟汤", "player_mode": "dual",
    "min_players": 2, "max_players": 2, "needs_gm": False,
    "description": "用是非问句揭开古怪汤面的真相",
})
