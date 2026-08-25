# -*- coding: utf-8 -*-
"""AMBRACE MCP 接入 Phase 3 测试（2026-08-27）。

覆盖：
1. PUT /api/v1/mcp/servers/{id}/tools/{tool_name} 权限端点：
   - 鉴权（未登录 401 / 非主账号 403 / 主账号可写）
   - 校验（mode 非法 400 / 工具不存在 404 / server 不存在 404）
   - 写 ToolPermission(scope=mcp_{server}, level=mode)（新增与更新）
   - 返回 scope + mode（回显）
2. GET /{id}/tools 每项带 risk_level + 当前生效 mode（读 ToolPermission / 风险默认）。

安全要点：所有 DB 写入用测试前缀名 / 独立 user_id，teardown 清理；不真正连接。
"""
import asyncio
import json

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select
from starlette.testclient import TestClient

from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.models.mcp_server import MCPServer
from app.models.tool_permission import ToolPermission

ADMIN = 1
PERM_UID = 9101  # 独立测试用户（非主账号、无既有权限行）
TEST_PREFIX = "mcp_ph3_"

MOCK_TOOLS = [
    {"name": "read_file", "description": "读取文件", "input_schema": {"type": "object"}},
    {"name": "write_file", "description": "写文件", "input_schema": {"type": "object"}},
    {"name": "do_something", "description": "模糊操作", "input_schema": {"type": "object"}},
]


def _run(coro):
    return asyncio.run(coro)


async def _cleanup():
    async with async_session_factory() as db:
        await db.execute(delete(MCPServer).where(MCPServer.name.like(TEST_PREFIX + "%")))
        await db.execute(delete(ToolPermission).where(ToolPermission.user_id == PERM_UID))
        await db.commit()


@pytest.fixture(autouse=True)
def _isolate():
    _run(_cleanup())
    yield
    _run(_cleanup())


def _make_server(user_id=ADMIN, name=None, tools_cache=None):
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
                tools_cache_json=json.dumps(tools_cache or []),
                status="disconnected",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id

    return _run(_do())


def _make_client(user_id=ADMIN):
    from app.api.mcp import router as mcp_router

    app = FastAPI()
    app.include_router(mcp_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _get_permission(user_id, scope):
    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(ToolPermission).where(
                    ToolPermission.user_id == user_id, ToolPermission.scope == scope
                )
            )
        ).scalar_one_or_none()
        return row.level if row else None


# ------------------------------------------------------------------ 鉴权

def test_set_permission_unauthenticated_401():
    # 不覆盖 get_current_user_id → 真实 HTTP 无凭据 → 401
    from app.api.mcp import router as mcp_router

    app = FastAPI()
    app.include_router(mcp_router)
    client = TestClient(app)
    r = client.put("/api/v1/mcp/servers/1/tools/read_file", json={"mode": "allow"})
    assert r.status_code == 401


def test_set_permission_non_admin_403(monkeypatch):
    async def _fake(uid):
        return False

    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)
    sid = _make_server(name=TEST_PREFIX + "na", tools_cache=MOCK_TOOLS)
    client = _make_client(PERM_UID)  # 非主账号
    r = client.put(f"/api/v1/mcp/servers/{sid}/tools/read_file", json={"mode": "allow"})
    assert r.status_code == 403


# ------------------------------------------------------------------ 校验

def test_set_permission_invalid_mode(monkeypatch):
    async def _fake(uid):
        return True

    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)
    sid = _make_server(name=TEST_PREFIX + "bad", tools_cache=MOCK_TOOLS)
    client = _make_client(ADMIN)
    for m in ("allow?", "never", "always", "forbid!", "ask?"):
        r = client.put(f"/api/v1/mcp/servers/{sid}/tools/read_file", json={"mode": m})
        assert r.status_code == 400, f"mode={m!r}"
    # 无 body 字段 → 422（缺 mode），但 Pydantic 会报 422 而非 400；此处断言不会写权限
    assert _run(_get_permission(ADMIN, f"mcp_{TEST_PREFIX}bad")) is None


def test_set_permission_tool_not_found(monkeypatch):
    async def _fake(uid):
        return True

    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)
    sid = _make_server(name=TEST_PREFIX + "nf", tools_cache=MOCK_TOOLS)
    client = _make_client(ADMIN)
    r = client.put(f"/api/v1/mcp/servers/{sid}/tools/nonexistent", json={"mode": "allow"})
    assert r.status_code == 404


def test_set_permission_server_not_found(monkeypatch):
    async def _fake(uid):
        return True

    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)
    client = _make_client(ADMIN)
    assert client.put("/api/v1/mcp/servers/999999/tools/read_file", json={"mode": "allow"}).status_code == 404


# ------------------------------------------------------------------ 写 ToolPermission + 回显

def test_set_permission_creates_row(monkeypatch):
    async def _fake(uid):
        return True

    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)
    sid = _make_server(name=TEST_PREFIX + "w", tools_cache=MOCK_TOOLS)
    scope = f"mcp_{TEST_PREFIX}w"
    client = _make_client(ADMIN)

    r = client.put(f"/api/v1/mcp/servers/{sid}/tools/read_file", json={"mode": "forbid"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == scope
    assert body["mode"] == "forbid"
    assert body["server_id"] == sid
    assert body["tool_name"] == "read_file"
    assert _run(_get_permission(ADMIN, scope)) == "forbid"


def test_set_permission_updates_row(monkeypatch):
    async def _fake(uid):
        return True

    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)
    sid = _make_server(name=TEST_PREFIX + "u", tools_cache=MOCK_TOOLS)
    scope = f"mcp_{TEST_PREFIX}u"
    client = _make_client(ADMIN)

    client.put(f"/api/v1/mcp/servers/{sid}/tools/read_file", json={"mode": "ask"})
    assert _run(_get_permission(ADMIN, scope)) == "ask"

    r = client.put(f"/api/v1/mcp/servers/{sid}/tools/read_file", json={"mode": "allow"})
    assert r.status_code == 200
    assert r.json()["mode"] == "allow"
    assert _run(_get_permission(ADMIN, scope)) == "allow"


def test_get_tools_echoes_mode_and_risk():
    """GET /tools 每项带 risk_level + 当前生效 mode（未显式配置按风险默认）。"""
    sid = _make_server(name=TEST_PREFIX + "echo", tools_cache=MOCK_TOOLS)
    client = _make_client(ADMIN)
    tools = client.get(f"/api/v1/mcp/servers/{sid}/tools").json()
    assert tools["total"] == 3
    by_name = {t["name"]: t for t in tools["items"]}

    # 风险等级按工具名推断
    assert by_name["read_file"]["risk_level"] == "low"     # read → low
    assert by_name["write_file"]["risk_level"] == "high"   # write → high
    assert by_name["do_something"]["risk_level"] == "medium"

    # 未显式配置：低风险默认 allow，高风险默认 ask（mcp_x 无显式权限行）
    scope = f"mcp_{TEST_PREFIX}echo"
    assert by_name["read_file"]["scope"] == scope
    assert by_name["read_file"]["mode"] == "allow"
    assert by_name["write_file"]["mode"] == "ask"


def test_get_tools_echoes_explicit_mode(monkeypatch):
    """显式写入权限后，GET /tools 回显该 mode（读 ToolPermission 优先）。"""
    async def _fake(uid):
        return True

    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)
    sid = _make_server(name=TEST_PREFIX + "exp", tools_cache=MOCK_TOOLS)
    client = _make_client(ADMIN)
    r = client.put(f"/api/v1/mcp/servers/{sid}/tools/write_file", json={"mode": "allow"})
    assert r.status_code == 200

    tools = client.get(f"/api/v1/mcp/servers/{sid}/tools").json()
    write = next(t for t in tools["items"] if t["name"] == "write_file")
    assert write["mode"] == "allow"  # 显式 allow 覆盖高风险默认 ask
