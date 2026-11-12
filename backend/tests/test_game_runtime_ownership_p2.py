# -*- coding: utf-8 -*-
"""P2-3 合同：game 进程内状态下沉 `app/games/runtime.py`（API 保留同名别名）。

判据：
- `app.api.games` 的 `_ai_turn_locks` / `_game_ws_clients` 与运行时**同一对象**；
- `_lazy_lock`（API 重导出 game_service 的实现）写入的是运行时锁表；`cleanup_session` 两表一起清；
- `_broadcast_game_event` 仍留在 API（测试用字符串路径打桩 `app.api.games._broadcast_game_event`）；
- game_service 剩余 `_g.*` 回指＝登记的 6 个债务名（新增必须显式登记）。
"""
import asyncio
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
DEBT = {
    "_g._broadcast_game_event",
    "_g._guard_stop",
    "_g._guard_stop_visible",
    "_g._settle_game",
    "_g.async_session_factory",
    "_g.engine_for",
}


def test_state_objects_shared_with_api():
    from app.api import games as games_api
    from app.games import runtime

    assert games_api._ai_turn_locks is runtime.AI_TURN_LOCKS
    assert games_api._game_ws_clients is runtime.GAME_WS_CLIENTS


def test_lazy_lock_and_cleanup_use_runtime():
    from app.api import games as games_api
    from app.games import runtime

    locks = games_api._lazy_lock(424242)
    assert locks is runtime.AI_TURN_LOCKS[424242]
    assert games_api._lazy_lock(424242) is locks, "同一对局必须复用同一把锁"
    runtime.GAME_WS_CLIENTS.setdefault(424242, set()).add("ws")
    runtime.cleanup_session(424242)
    assert 424242 not in runtime.AI_TURN_LOCKS
    assert 424242 not in runtime.GAME_WS_CLIENTS


def test_api_still_owns_broadcast_for_test_stubs():
    src = (APP / "api" / "games.py").read_text(encoding="utf-8")
    assert "async def _broadcast_game_event(" in src, "字符串路径打桩点不能搬走"
    assert "_ai_turn_locks: dict[int, asyncio.Lock] = {}" not in src, "锁表定义应已下沉运行时"
    assert "_game_ws_clients: dict[int, set[WebSocket]] = {}" not in src, "WS 表定义应已下沉运行时"


def test_remaining_backrefs_are_registered():
    import re

    src = (APP / "application" / "game_service.py").read_text(encoding="utf-8")
    found = {m.group(0) for m in re.finditer(r"_g\.[A-Za-z_]+", src)}
    assert found == DEBT, f"game_service 回指面变化，请同步登记：{sorted(found ^ DEBT)}"


def test_runtime_is_single_home_of_state():
    runtime_src = (APP / "games" / "runtime.py").read_text(encoding="utf-8")
    assert "AI_TURN_LOCKS: dict[int, asyncio.Lock] = {}" in runtime_src
    assert "GAME_WS_CLIENTS: dict[int, set] = {}" in runtime_src
    assert "def lazy_lock(" in runtime_src and "def cleanup_session(" in runtime_src


def test_api_module_imports():
    # 纯导入冒烟：确保 api/games 在别名导入后仍可加载（无循环/无未定义）
    import importlib

    mod = importlib.import_module("app.api.games")
    assert hasattr(mod, "router") and hasattr(mod, "_lazy_lock")
    assert asyncio is not None
