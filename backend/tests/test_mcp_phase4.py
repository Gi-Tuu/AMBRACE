# -*- coding: utf-8 -*-
"""AMBRACE MCP 接入 Phase 4 测试（2026-08-28）。

覆盖：
1. Resources 发现与注入：_resource_to_dict / _format_mcp_resources / resources_for_user /
   _build_mcp_resources_text（已连接资源摘要注入，无资源零行为变化）。
2. Prompts：_prompt_to_dict / list_prompts / get_prompt。
3. 工具调用日志：call_tool 落 mcp_call_logs（trigger 语义 status/ok/latency）。
4. 状态事件复用：已由 Phase 2 test_status_event_published 覆盖，此处补 resources 事件负路径可选。
5. API：GET /{id}/resources、GET /{id}/prompts、GET /logs。

安全要点：所有 DB 写入用测试前缀名 / 独立 user_id；manager 连接基元用假 worker（同事件循环）。
"""
import asyncio

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select
from starlette.testclient import TestClient

from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.mcp.manager import STATUS_CONNECTED, _Connection, mcp_manager
from app.models.mcp import McpCallLog
from app.models.mcp import MCPServer

ADMIN = 1
UID = 9200  # 独立测试用户
TEST_PREFIX = "mcp_ph4_"


def _run(coro):
    return asyncio.run(coro)


async def _cleanup():
    for sid in list(mcp_manager._conns.keys()):
        mcp_manager._conns.pop(sid, None)
    async with async_session_factory() as db:
        await db.execute(delete(McpCallLog).where(McpCallLog.user_id == UID))
        await db.execute(delete(MCPServer).where(MCPServer.name.like(TEST_PREFIX + "%")))
        await db.commit()


@pytest.fixture(autouse=True)
def _isolate():
    _run(_cleanup())
    yield
    _run(_cleanup())


def _make_server(user_id=ADMIN, name=None):
    async def _do():
        async with async_session_factory() as db:
            row = MCPServer(
                user_id=user_id,
                name=name or (TEST_PREFIX + "srv"),
                transport="stdio",
                command="python",
                args_json="[]",
                env_json="{}",
                enabled=True,
                auto_connect=False,
                tools_cache_json="[]",
                status="disconnected",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id
    return _run(_do())


def _conn(server_id, *, server_name="srv", user_id=ADMIN, resources=None, prompts=None, tools=None):
    """构造一个假的已连接 _Connection 并注册进 manager（同事件循环；is_connected 成立）。"""
    async def _do():
        c = _Connection(server_id=server_id, server_name=server_name, user_id=user_id)
        c.tools = tools or []
        c.resources = resources or []
        c.prompts = prompts or []
        c.status = STATUS_CONNECTED
        c._worker = asyncio.get_running_loop().create_future()  # 未 done → is_connected True
        mcp_manager._conns[server_id] = c
        return c
    return _run(_do())


# ------------------------------------------------------------------ 资源 dict/格式化

def test_resource_to_dict():
    from app.mcp.manager import MCPClientManager
    from mcp_types import Resource

    r = Resource(uri="file:///data/report.md", name="report", description="月度报告", mime_type="text/markdown")
    d = MCPClientManager()._resource_to_dict(r)
    assert d["uri"] == "file:///data/report.md"
    assert d["name"] == "report"
    assert d["description"] == "月度报告"
    assert d["mime_type"] == "text/markdown"

    # name 缺省（防御：真实 server 不会缺，但模型可缺） → 由 uri 兜底
    from types import SimpleNamespace
    r2 = SimpleNamespace(uri="file:///data/notes.txt", description=None, mime_type=None, name=None)
    d2 = MCPClientManager()._resource_to_dict(r2)
    assert d2["name"] == "notes.txt"
    assert d2["description"] == ""


def test_prompt_to_dict():
    from app.mcp.manager import MCPClientManager
    from mcp_types import Prompt, PromptArgument

    p = Prompt(
        name="summary", description="总结",
        arguments=[PromptArgument(name="doc", description="文档", required=True)],
    )
    d = MCPClientManager()._prompt_to_dict(p)
    assert d["name"] == "summary"
    assert d["description"] == "总结"
    assert d["arguments"][0]["name"] == "doc"
    assert d["arguments"][0]["required"] is True


def test_format_mcp_resources_empty():
    from app.agent.context_builder import _format_mcp_resources
    assert _format_mcp_resources([]) == ""
    assert _format_mcp_resources([{"server_name": "srv", "resources": []}]) == ""


def test_format_mcp_resources_groups():
    from app.agent.context_builder import _format_mcp_resources
    groups = [{
        "server_name": "files",
        "resources": [
            {"uri": "file:///a.md", "name": "a.md", "mime_type": "text/markdown", "description": "文档A"},
            {"uri": "file:///b.txt", "name": "b.txt", "mime_type": "", "description": ""},
        ],
    }]
    text = _format_mcp_resources(groups)
    assert "【files】" in text
    assert "a.md" in text
    assert "text/markdown" in text
    assert "文档A" in text
    assert "b.txt" in text


# ------------------------------------------------------------------ 注入（resources_for_user / _build_mcp_resources_text）

RESOURCES = [
    {"uri": "file:///a.md", "name": "a.md", "mime_type": "text/markdown", "description": "文档A"},
    {"uri": "file:///b.txt", "name": "b.txt", "mime_type": "", "description": ""},
]
PROMPTS = [
    {"name": "summary", "description": "总结", "arguments": []},
]


def test_resources_for_user_filters_by_user_and_connected():
    _conn(101, server_name="u1srv", user_id=UID, resources=RESOURCES)
    _conn(102, server_name="u2srv", user_id=999, resources=RESOURCES)  # 他人 server
    _conn(103, server_name="emptysrv", user_id=UID, resources=[])  # 无资源

    groups = mcp_manager.resources_for_user(UID)
    assert len(groups) == 1
    assert groups[0]["server_name"] == "u1srv"
    assert groups[0]["resources"] == RESOURCES


def test_build_mcp_resources_text(monkeypatch):
    from app.agent.context_builder import _build_mcp_resources_text

    _conn(201, server_name="files", user_id=UID, resources=RESOURCES)

    text = _run(_build_mcp_resources_text(UID))
    assert "【files】" in text
    assert "a.md" in text

    # 无资源用户 → 空串（零行为变化）
    assert _run(_build_mcp_resources_text(99999)) == ""


def test_build_mcp_resources_text_stream_mode_skips(monkeypatch):
    """V2-8：流式模式（stream=True）不注入资源摘要。

    流式路径不注入 MCP 工具声明（P2-A），若仍注入资源摘要（含"可用对应工具读取内容"），
    AI 会看到资源提示但实际无法调用工具 → 可能输出不一致的回复。故流式模式返回空串，与工具声明对齐。
    """
    from app.agent.context_builder import _build_mcp_resources_text

    _conn(202, server_name="files", user_id=UID, resources=RESOURCES)

    # 非流式：照常注入资源摘要
    assert "【files】" in _run(_build_mcp_resources_text(UID))
    # 流式：返回空串（不注入资源摘要）
    assert _run(_build_mcp_resources_text(UID, stream=True)) == ""


# ------------------------------------------------------------------ manager 公共方法（live 查询）

def test_list_resources_live(monkeypatch):
    _conn(301, server_name="srv", resources=RESOURCES)
    # 不 refresh：直接返回已缓存资源（is_connected 成立）
    items = _run(mcp_manager.list_resources(301))
    assert items == RESOURCES
    # 未连接 server → 空列表
    assert _run(mcp_manager.list_resources(999999)) == []


def test_list_prompts_live(monkeypatch):
    _conn(302, server_name="srv", prompts=PROMPTS)
    items = _run(mcp_manager.list_prompts(302))
    assert items == PROMPTS
    assert _run(mcp_manager.list_prompts(999999)) == []


def test_get_prompt_returns_messages(monkeypatch):
    c = _conn(303, server_name="srv")
    queue = asyncio.Queue()
    c._queue = queue

    async def _flow():
        async def _consumer():
            cmd = await queue.get()
            cmd["future"].set_result({"ok": True, "messages": [{"role": "user", "text": "hi"}]})
        asyncio.ensure_future(_consumer())
        return await mcp_manager.get_prompt(303, "summary", {"doc": "x"})

    res = _run(_flow())
    assert res["ok"] is True
    assert res["messages"][0]["role"] == "user"


# ------------------------------------------------------------------ 工具调用日志

def test_call_tool_writes_log(monkeypatch):
    c = _conn(401, server_name="srv", user_id=UID)
    queue = asyncio.Queue()
    c._queue = queue

    async def _flow():
        async def _consumer():
            cmd = await queue.get()
            assert cmd["kind"] == "call_tool"
            cmd["future"].set_result({"content": [{"type": "text", "text": "echo"}], "isError": False})
        asyncio.ensure_future(_consumer())
        return await mcp_manager.call_tool(401, "echo", {"text": "hi"})

    result = _run(_flow())
    assert result["isError"] is False

    # 日志落库（mcp_call_logs：trigger 语义 status/ok/latency）
    async def _load():
        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(McpCallLog).where(McpCallLog.user_id == UID).order_by(McpCallLog.id.desc())
                )
            ).scalars().first()
            return row
    row = _run(_load())
    assert row is not None
    assert row.server_id == 401
    assert row.server_name == "srv"
    assert row.tool == "mcp.srv.echo"
    assert row.ok is True
    assert row.status == "ok"
    assert row.latency_ms >= 0


def test_call_tool_logs_error(monkeypatch):
    c = _conn(402, server_name="srv", user_id=UID)
    queue = asyncio.Queue()
    c._queue = queue

    async def _flow():
        async def _consumer():
            cmd = await queue.get()
            cmd["future"].set_result({"content": [], "isError": True, "error": "boom"})
        asyncio.ensure_future(_consumer())
        return await mcp_manager.call_tool(402, "fail", {})

    result = _run(_flow())
    assert result["isError"] is True
    async def _load():
        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(McpCallLog).where(McpCallLog.user_id == UID).order_by(McpCallLog.id.desc())
                )
            ).scalars().first()
            return row
    row = _run(_load())
    assert row is not None
    assert row.ok is False
    assert row.status == "error"
    assert row.error == "boom"


# ------------------------------------------------------------------ API

def _make_client(user_id=ADMIN):
    from app.api.mcp import router as mcp_router
    app = FastAPI()
    app.include_router(mcp_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def test_api_get_resources(monkeypatch):
    async def _fake_list_resources(server_id, refresh=False):
        return RESOURCES
    monkeypatch.setattr(mcp_manager, "list_resources", _fake_list_resources)
    sid = _make_server(name=TEST_PREFIX + "res")
    client = _make_client(ADMIN)
    r = client.get(f"/api/v1/mcp/servers/{sid}/resources")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert body["items"][0]["uri"] == "file:///a.md"


def test_api_get_resources_refresh_passed_as_query(monkeypatch):
    """V2-7：refresh 是真正的 query 参数（此前误写成函数体局部变量，恒为真，每次强制刷新）。

    传 `?refresh=true` → 透传 refresh=True；不传 → refresh=False（不再恒真）。
    """
    calls: list[tuple] = []

    async def _fake_list_resources(server_id, refresh=False):
        calls.append((server_id, refresh))
        return RESOURCES

    monkeypatch.setattr(mcp_manager, "list_resources", _fake_list_resources)
    sid = _make_server(name=TEST_PREFIX + "refresh")
    client = _make_client(ADMIN)
    r = client.get(f"/api/v1/mcp/servers/{sid}/resources?refresh=true")
    assert r.status_code == 200, r.text
    assert calls[-1] == (sid, True)
    # 不传 refresh → 默认 False
    r2 = client.get(f"/api/v1/mcp/servers/{sid}/resources")
    assert r2.status_code == 200, r2.text
    assert calls[-1] == (sid, False)


def test_api_get_prompts(monkeypatch):
    async def _fake_list_prompts(server_id, refresh=False):
        return PROMPTS
    monkeypatch.setattr(mcp_manager, "list_prompts", _fake_list_prompts)
    sid = _make_server(name=TEST_PREFIX + "prm")
    client = _make_client(ADMIN)
    r = client.get(f"/api/v1/mcp/servers/{sid}/prompts")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["name"] == "summary"


def test_api_get_logs():
    # 直接插一条本用户日志，GET /logs 应返回
    async def _insert():
        async with async_session_factory() as db:
            db.add(McpCallLog(user_id=UID, server_id=9, server_name="srv", tool="mcp.srv.echo",
                              arguments_summary='{"a":1}', ok=True, status="ok", latency_ms=12))
            await db.commit()
    _run(_insert())
    client = _make_client(UID)
    r = client.get("/api/v1/mcp/servers/logs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["tool"] == "mcp.srv.echo"
    assert body["items"][0]["server_id"] == 9


def test_api_get_logs_scoped_to_user():
    # 他人日志不返回
    async def _insert():
        async with async_session_factory() as db:
            db.add(McpCallLog(user_id=9999, server_id=9, server_name="srv", tool="mcp.srv.echo", ok=True, status="ok"))
            await db.commit()
    _run(_insert())
    client = _make_client(UID)
    r = client.get("/api/v1/mcp/servers/logs")
    assert r.json()["total"] == 0
