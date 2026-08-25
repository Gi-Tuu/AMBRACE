"""MCP Server 归属查询（多用户隔离，P1，2026-08-29）。

- `owned_server_ids(user_id)`：当前用户拥有的 mcp_servers.id 集合（context_builder 声明注入用，
  每次查库，保证新配置立即生效；查询失败返回空集 —— fail-closed，宁可少注入不泄露他人 Server）。
- `user_owns_server(user_id, server_id)`：tool_runner 归属校验（防御纵深）用，走 30s 缓存
  （hot path 每次工具调用都会触发；缓存短暂滞后可接受，主隔离在 context_builder 的实时查询）。

本项目 mcp SDK 全部延迟 import；本模块仅查询我们自己的 mcp_servers 表，不引入 SDK。
"""
import time

from sqlalchemy import select

from app.db.database import async_session_factory
from app.utils.logger import get_logger

_logger = get_logger("mcp.ownership")

# 归属缓存（user_id -> (monotonic_ts, set[server_id])）；仅 hot path（tool_runner）使用。
_CACHE: dict[int, tuple[float, set[int]]] = {}
_CACHE_TTL = 30.0


async def owned_server_ids(user_id: int, *, use_cache: bool = False) -> set[int]:
    """用户拥有的 mcp server id 集合。

    - use_cache=False（默认）：每次查库（context_builder 声明注入，保证服务器增删即时生效）；
    - use_cache=True：走 30s 缓存（tool_runner 归属校验 hot path）。
    - 查询失败返回空集（fail-closed）。
    """
    if use_cache:
        now = time.monotonic()
        cached = _CACHE.get(user_id)
        if cached is not None and (now - cached[0]) < _CACHE_TTL:
            return cached[1]
    ids: set[int] = set()
    try:
        from app.models.mcp_server import MCPServer

        async with async_session_factory() as db:
            rows = (await db.execute(
                select(MCPServer.id).where(MCPServer.user_id == user_id)
            )).scalars().all()
        ids = {int(r) for r in rows}
    except Exception as e:
        _logger.warning("mcp owned server ids query failed user=%s: %s", user_id, e)
        ids = set()
    if use_cache:
        _CACHE[user_id] = (time.monotonic(), ids)
    return ids


async def user_owns_server(user_id: int | None, server_id: int | None) -> bool:
    """判断 user_id 是否拥有 server_id（tool_runner 防御纵深；hot path 走缓存）。

    user_id / server_id 任一为空 → 不认为拥有（返回 False，fail-closed）。
    """
    if user_id is None or server_id is None:
        return False
    owned = await owned_server_ids(user_id, use_cache=True)
    return server_id in owned
