# -*- coding: utf-8 -*-
"""P0-B MCP 运行期自愈测试（2026-09-06）。

覆盖（Codex 派工范围）：
1. 维护循环「首败次成」：reconnect_all 对 auto_connect+enabled 但 down 的 server，首次连接失败
   （单次抖动不触发 5min 降频）→ 下次巡检重试成功，fail_streak 清零。
2. 连续失败降频（B7）：streak 达阈值且处于冷却窗口 → 跳过（不刷日志/耗连接）；
   force=True（启动首连语义）强制重试。
3. 旧 worker 收尾「不误清新 worker」：_worker_main finally 的 current_task() 守卫在
   「conn._worker 已指向新 worker」时不得清空（B5）；新 worker 自身收尾才清空。

只用临时 SQLite + monkeypatch，不真正连接 MCP 子进程，不触碰 backend/data 生产库。
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.mcp.connection import _Connection
from app.mcp.manager import MCPClientManager
from app.mcp.transport import STATUS_CONNECTED
from app.models.mcp import MCPServer


@pytest.fixture()
def mcp_db(monkeypatch):
    """临时 SQLite（空闲端口）+ patch app.db.database.async_session_factory。"""
    tmp = tempfile.mkdtemp(prefix="mcp_selfheal_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    import app.models  # noqa: F401  确保全部内核模型（含 mcp_servers）注册进 metadata
    from app.models.base import Base

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def _seed(mcp_db):
    """插入一行 auto_connect=True 且 enabled=True 的 MCP Server，返回其 id。"""
    async def _do():
        async with mcp_db() as db:
            row = MCPServer(user_id=1, name="srv", transport="stdio", command="echo",
                            auto_connect=True, enabled=True)
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id
    return asyncio.run(_do())


def test_reconnect_all_first_fail_second_success(mcp_db, monkeypatch):
    """首败次成：单次失败不触发 5min 降频，下次巡检重试并成功，fail_streak 清零。"""
    sid = _seed(mcp_db)
    mgr = MCPClientManager()
    calls = []
    connected = {"v": False}

    async def fake_connect(server_id):
        calls.append(server_id)
        if len(calls) == 1:
            return {"ok": False, "status": "error", "error": "boom"}  # 首次失败
        connected["v"] = True
        return {"ok": True, "status": STATUS_CONNECTED, "tools": []}

    monkeypatch.setattr(mgr, "connect", fake_connect)
    monkeypatch.setattr(mgr, "is_connected", lambda server_id: connected["v"])

    asyncio.run(mgr.reconnect_all())  # 首次：失败 → streak=1
    assert mgr._fail_streak[sid] == 1
    assert sid in mgr._last_attempt

    # 单次失败（min_fail_streak=2）不降频 → 立即重试并成功
    asyncio.run(mgr.reconnect_all())
    assert len(calls) == 2
    assert connected["v"] is True
    assert sid not in mgr._fail_streak, "成功后应清零 fail_streak"


def test_reconnect_all_downscale_skip_then_force(mcp_db, monkeypatch):
    """连续失败降频（B7）：streak 达阈值且在冷却窗口 → 跳过；force=True 强制重试。"""
    sid = _seed(mcp_db)
    mgr = MCPClientManager()
    calls = []

    async def fake_connect(server_id):
        calls.append(server_id)
        return {"ok": False, "status": "error", "error": "unreachable"}

    monkeypatch.setattr(mgr, "connect", fake_connect)

    # 连败两次 → streak=2（达降频阈值 min_fail_streak）
    asyncio.run(mgr.reconnect_all())
    asyncio.run(mgr.reconnect_all())
    assert mgr._fail_streak[sid] == 2
    n_calls = len(calls)

    # 第三次：仍在 5min 冷却窗口内 → 降频跳过，不再尝试
    asyncio.run(mgr.reconnect_all())
    assert len(calls) == n_calls, "冷却窗口内应降频跳过"

    # force=True（启动首连语义）→ 强制重试
    asyncio.run(mgr.reconnect_all(force=True))
    assert len(calls) == n_calls + 1


async def _hold():
    """一个「运行到被取消」的常驻占位协程。"""
    await asyncio.Event().wait()


def test_old_worker_teardown_does_not_clear_new_worker():
    """B5 守卫：旧 worker 收尾时 conn._worker 已指向新 worker → 不得误清；新 worker 自身收尾才清。"""

    async def scenario():
        mgr = MCPClientManager()
        conn = _Connection(server_id=1, server_name="srv", enabled=True, user_id=1)

        # G2 维护循环已建好「新 worker」（conn._worker 指向 new_task）
        new_task = asyncio.ensure_future(_hold())
        conn._worker = new_task
        conn._queue = asyncio.Queue()
        conn._ready = asyncio.get_running_loop().create_future()

        # 旧 worker 收尾：current_task() 是旧任务（≠ new_task）→ 守卫必须保留新 worker 引用
        async def _old_settle():
            mgr._clear_worker_refs(conn)

        old_task = asyncio.ensure_future(_old_settle())
        await old_task
        assert conn._worker is new_task, "旧 worker 收尾不得误清「已重连的新 worker」"
        assert conn._queue is not None
        assert conn._ready is not None

        # 新 worker 自身收尾：让 conn._worker 指向「正在运行的收尾任务」，此时才应清空
        async def _self_clear():
            mgr._clear_worker_refs(conn)

        self_task = asyncio.ensure_future(_self_clear())
        conn._worker = self_task
        await self_task
        assert conn._worker is None and conn._queue is None and conn._ready is None

        new_task.cancel()
        try:
            await new_task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
