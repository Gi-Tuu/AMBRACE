# -*- coding: utf-8 -*-
"""P3-3（2026-09-18）：MCP ClientSession 的 notification_callback（本 SDK 形参 message_handler）。

覆盖（不连真实 MCP 服务；假 conn / 假 session / monkeypatch）：
- tools/list_changed：重列工具并刷新 conn.tools 内存缓存 + 回写既有 tools_cache_json；
- progress：只记日志、不抛异常；
- 其它通知：落 debug 日志；
- 回调内 list_tools 抛异常：被 try/except 吞掉并 warning，绝不影响调用方（不向外抛）。

沿用项目约定：sync test + asyncio.run。
"""
import asyncio
from types import SimpleNamespace

import pytest
from mcp import types as mcp_types

from app.mcp.manager import MCPClientManager


def _fake_session(tools, *, raise_on_list=False):
    class _FakeSession:
        async def list_tools(self):
            if raise_on_list:
                raise RuntimeError("injected list_tools failure")
            return SimpleNamespace(tools=tools)

    return _FakeSession()


@pytest.fixture
def mgr(monkeypatch):
    m = MCPClientManager()
    conn = SimpleNamespace(server_id=9091, server_name="t", tools=[{"name": "old"}])
    m._conns[9091] = conn
    # 避免回写真实库：记录调用即可
    calls = []

    async def _fake_cache(db_server_id, tools):
        calls.append((db_server_id, tools))

    monkeypatch.setattr(m, "_cache_tools_db", _fake_cache)
    return m, conn, calls


def test_list_changed_refreshes_cache(mgr):
    m, conn, calls = mgr
    fake_sess = _fake_session(
        [SimpleNamespace(name="new1"), SimpleNamespace(name="new2")]
    )
    asyncio.run(
        m._notification_handler(9091, fake_sess, mcp_types.ToolListChangedNotification())
    )
    expected = [
        {"name": "new1", "description": "", "input_schema": {}},
        {"name": "new2", "description": "", "input_schema": {}},
    ]
    assert conn.tools == expected
    assert calls == [(9091, expected)]


def test_list_changed_unknown_server_only_logs(mgr):
    m, conn, calls = mgr
    fake_sess = _fake_session([])
    # server_id 不在 _conns 中
    asyncio.run(
        m._notification_handler(0, fake_sess, mcp_types.ToolListChangedNotification())
    )
    assert conn.tools == [{"name": "old"}]  # 未被改动
    assert calls == []  # 无缓存回写


def test_progress_notification_no_raise(mgr):
    m, conn, calls = mgr
    fake_sess = _fake_session([])
    asyncio.run(
        m._notification_handler(
            9091,
            fake_sess,
            mcp_types.ProgressNotification(
                params=mcp_types.ProgressNotificationParams(progress_token="tok", progress=1.0)
            ),
        )
    )
    assert conn.tools == [{"name": "old"}]


def test_other_notification_logs_debug(mgr):
    m, conn, calls = mgr
    fake_sess = _fake_session([])
    asyncio.run(
        m._notification_handler(9091, fake_sess, mcp_types.PromptListChangedNotification())
    )
    assert conn.tools == [{"name": "old"}]


def test_list_changed_swallows_list_tools_error(mgr):
    m, conn, calls = mgr
    fake_sess = _fake_session([], raise_on_list=True)
    # 回调内 list_tools 抛异常：必须被吞掉，不向外传播
    asyncio.run(
        m._notification_handler(9091, fake_sess, mcp_types.ToolListChangedNotification())
    )
    assert conn.tools == [{"name": "old"}]  # 刷新失败，保持旧缓存
    assert calls == []  # 未回写
