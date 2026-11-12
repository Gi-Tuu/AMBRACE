# -*- coding: utf-8 -*-
"""P2-3（2026-11-11 架构治理）：游戏运行时的进程内状态。

此前 `_ai_turn_locks` / `_game_ws_clients` 住在 `app/api/games.py`，application/life/scheduler
反向依赖 API 模块取用。本模块承接「状态所有权」：API 与 service 都从这里取同一份对象，
依赖方向改为双向→单向指向运行时。

约束：仍在 API 层的 `_broadcast_game_event` / `_guard_stop*`（测试有字符串路径打桩）
**不搬**；本模块只放状态与最小操作，避免行为变化。
"""
from __future__ import annotations

import asyncio

# session_id -> 防重入锁（进程内；跨进程需 DB 行锁，MVP 单进程足够）
AI_TURN_LOCKS: dict[int, asyncio.Lock] = {}

# session_id -> set[WS 客户端]（与 chat 的 connected_clients 隔离，避免 id 撞表；泛型化避免 API 依赖）
GAME_WS_CLIENTS: dict[int, set] = {}


def lazy_lock(session_id: int) -> asyncio.Lock:
    """按对局取（或懒建）防重入锁。"""
    return AI_TURN_LOCKS.setdefault(session_id, asyncio.Lock())


def cleanup_session(session_id: int) -> None:
    """对局结束后的状态清理（锁 + WS 集合），防长期运行内存增长。"""
    AI_TURN_LOCKS.pop(session_id, None)
    GAME_WS_CLIENTS.pop(session_id, None)
