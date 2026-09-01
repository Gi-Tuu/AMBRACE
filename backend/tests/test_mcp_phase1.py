# -*- coding: utf-8 -*-
"""AMBRACE MCP 接入 Phase 1 测试（2026-08-26）。

覆盖：
1. 连接/发现/调用（mock stdio MCP server）
2. 工具注册进 ToolRegistry（mcp.{server}.{tool} 命名空间 + 风险推断 + execute 闭包）
3. 断开时注销工具
4. API 鉴权（未登录 401；非主账号 403）
5. CRUD + 校验（400/404）
6. /test 试连端点（不保存配置/状态）

mock server 用最小 JSON-RPC stdio 实现（与 POC mock 写法一致），由 manager 以子进程拉起。
安全要点：manager 持有的连接其 anyio 任务绑定在创建它的那个事件循环，因此每个用例内的
connect/call/disconnect 必须在同一个循环里完成（asyncio.run 一次跑整段 flow）。
"""
import asyncio
import json
import sys
import time
import warnings

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from starlette.testclient import TestClient

from app.agent.tools import _REGISTRY, get_tool
from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.mcp.manager import mcp_manager
from app.mcp.tool_adapter import infer_risk
from app.models.mcp import MCPServer

ADMIN = 1
OTHER = 2

# 测试专用 Server 名前缀（teardown 只清理该前缀，避免误删真实配置）
TEST_PREFIX = "mcp_test_"
TEST_MOCK_TOOLS = ["echo", "write_file", "get_info"]

# 最小 stdio MCP mock：纯 JSON-RPC over stdin/stdout（与 POC mock 同构）
MOCK_SERVER = r'''
import sys, json
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
INIT_RESULT = {"protocolVersion": "2024-11-05",
               "capabilities": {"tools": {}},
               "serverInfo": {"name": "mcp-test-mock", "version": "1.0.0"}}
TOOLS = [
  {"name": "echo", "description": "Echo text (read-only)",
   "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
  {"name": "write_file", "description": "Write file content (write)",
   "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
  {"name": "get_info", "description": "Get info (read)",
   "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": []}},
]
def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method")
        rid = msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": INIT_RESULT})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params", {}) or {}
            args = params.get("arguments", {}) or {}
            name = params.get("name", "")
            if name == "echo":
                text = "echo: " + str(args.get("text", ""))
            elif name == "write_file":
                text = "written:" + str(args.get("path", ""))
            elif name == "get_info":
                text = "info:" + str(args.get("query", ""))
            else:
                text = "unknown:" + name
            send({"jsonrpc": "2.0", "id": rid,
                  "result": {"content": [{"type": "text", "text": text}], "isError": False}})
        else:
            if rid is not None:
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}})
main()
'''


def _run(coro):
    """在当前用例持有的事件循环中运行协程（避免 aiosqlite 跨 loop 线程残留）。"""
    return asyncio.get_event_loop().run_until_complete(coro)


# ------------------------------------------------------------------ 基础帮助

def _make_server(user_id=ADMIN, name=None, transport="stdio", command=None, args=None, env=None,
                 enabled=True, auto_connect=True, tools_cache="[]", status="disconnected"):
    async def _do():
        async with async_session_factory() as db:
            row = MCPServer(
                user_id=user_id,
                name=name or (TEST_PREFIX + "srv"),
                transport=transport,
                command=command or sys.executable,
                args_json=json.dumps(args if args is not None else ["-c", MOCK_SERVER]),
                env_json=json.dumps(env or {}),
                enabled=enabled,
                auto_connect=auto_connect,
                tools_cache_json=tools_cache,
                status=status,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id
    # 本地开发服务器可能短暂持有 SQLite 写锁，重试 2 次避免偶发 database is locked
    for _attempt in range(3):
        try:
            return _run(_do())
        except OperationalError:
            if _attempt == 2:
                raise
            time.sleep(1.0)
    raise RuntimeError("unreachable")


async def _cleanup_mcp():
    """用例后清理：清空 manager 连接内存状态 + 注销 mcp.* 工具 + 删除测试前缀 DB 行。

    不 await 跨事件循环的 __aexit__（可能挂起）：常规路径下每个用例都已在其自身循环内
    disconnect，此处 _conns 仅为兜底。
    """
    for sid in list(mcp_manager._conns.keys()):
        mcp_manager._conns.pop(sid, None)
    for name in list(_REGISTRY.keys()):
        if name.startswith("mcp."):
            _REGISTRY.pop(name, None)
    # v3.3.6 CI 修复：SQLite 写锁偶发（aiosqlite 残留线程未完全释放文件锁），重试后仍失败降级为 warning
    for _attempt in range(4):
        try:
            async with async_session_factory() as db:
                await db.execute(delete(MCPServer).where(MCPServer.name.like(TEST_PREFIX + "%")))
                await db.commit()
            return
        except Exception as exc:
            if _attempt == 3:
                warnings.warn(f"mcp teardown 清理仍失败（SQLite 写锁）: {exc}")
                return
            await asyncio.sleep(0.8)


@pytest.fixture(autouse=True)
def _mcp_isolation():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield
        loop.run_until_complete(_cleanup_mcp())
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _make_client(user_id=ADMIN):
    from app.api.mcp import router as mcp_router

    app = FastAPI()
    app.include_router(mcp_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


# ------------------------------------------------------------------ 风险推断

def test_infer_risk():
    assert infer_risk("read_file") == "low"
    assert infer_risk("search") == "low"
    assert infer_risk("list_things") == "low"
    assert infer_risk("get_data") == "low"
    assert infer_risk("find_all") == "low"
    assert infer_risk("write_file") == "high"
    assert infer_risk("create_user") == "high"
    assert infer_risk("delete_entry") == "high"
    assert infer_risk("execute_command") == "high"
    assert infer_risk("send_message") == "high"
    assert infer_risk("update_profile") == "high"
    assert infer_risk("do_something") == "medium"


# ------------------------------------------------------------------ 连接/发现/调用（单循环 flow）

def test_connect_discover_call():
    server_id = _make_server(name=TEST_PREFIX + "echo")
    result = None
    call = None

    async def _flow():
        nonlocal result, call
        result = await mcp_manager.connect(server_id)
        call = await mcp_manager.call_tool(server_id, "echo", {"text": "hi-e"})
        await mcp_manager.disconnect(server_id)

    _run(_flow())
    assert result["ok"] is True
    assert result["status"] == "connected"
    names = [t["name"] for t in result["tools"]]
    assert set(TEST_MOCK_TOOLS).issubset(set(names))
    echo_tool = next(t for t in result["tools"] if t["name"] == "echo")
    assert echo_tool["input_schema"]["properties"]["text"]["type"] == "string"
    assert call["isError"] is False
    assert call["content"][0]["text"] == "echo: hi-e"


def test_connect_call_missing_reconnects():
    """未连接时 call_tool 自动重连并调用成功。"""
    server_id = _make_server(name=TEST_PREFIX + "auto")
    out = {}

    async def _flow():
        out["call"] = await mcp_manager.call_tool(server_id, "echo", {"text": "auto-ok"})
        await mcp_manager.disconnect(server_id)

    _run(_flow())
    assert out["call"]["content"][0]["text"] == "echo: auto-ok"


def test_connect_concurrent_only_one_worker(monkeypatch):
    """P3-A：同一 server 并发 connect 只产生一个 worker（per-server 锁 + 双检）。

    mock 慢连接（_worker_main 延迟置 connected），两路并发 connect 应串行化 ——
    第二个在锁内双检到第一个已 connected 直接返回，不重复创建 worker。
    """
    server_id = _make_server(name=TEST_PREFIX + "conc")
    invocations = {"n": 0}

    async def slow_worker_main(conn, cfg):
        invocations["n"] += 1
        await asyncio.sleep(0.2)
        conn.status = "connected"
        mcp_manager._set_ready(conn, ok=True)
        # 模拟真实 worker 常驻（工作循环），直到被取消（asyncio.run 收尾时 cancel）
        while True:
            await asyncio.sleep(0.1)

    monkeypatch.setattr(mcp_manager, "_worker_main", slow_worker_main)
    results = {}

    async def _flow():
        r1, r2 = await asyncio.gather(
            mcp_manager.connect(server_id), mcp_manager.connect(server_id),
        )
        results["r1"], results["r2"] = r1, r2

    _run(_flow())
    # 只创建了一个 worker（慢连接不会被第二个 connect 重复拉起）
    assert invocations["n"] == 1
    assert results["r1"]["ok"] is True
    assert results["r2"]["ok"] is True
    assert results["r1"]["status"] == "connected"
    assert results["r2"]["status"] == "connected"


def test_tool_registered_and_unregistered():
    server_id = _make_server(name=TEST_PREFIX + "reg")
    server_name = TEST_PREFIX + "reg"
    specs = {}

    async def _flow():
        await mcp_manager.connect(server_id)
        specs["echo"] = get_tool(f"mcp.{server_name}.echo")
        specs["write"] = get_tool(f"mcp.{server_name}.write_file")
        specs["info"] = get_tool(f"mcp.{server_name}.get_info")
        specs["echo_out"] = await specs["echo"].execute({"text": "closure-ok"})
        await mcp_manager.disconnect(server_id)
        specs["echo_after"] = get_tool(f"mcp.{server_name}.echo")

    _run(_flow())

    echo_spec = specs["echo"]
    assert echo_spec is not None
    assert echo_spec.risk_level == "medium"  # echo 无读写关键词 → medium
    assert echo_spec.idempotent is False
    assert echo_spec.scope == f"mcp_{server_name}"
    assert echo_spec.provenance == f"mcp:{server_name}"
    assert echo_spec.epistemic_status == "UNVERIFIED"
    assert callable(echo_spec.execute)
    assert echo_spec.input_schema["properties"]["text"]["type"] == "string"
    assert specs["write"].risk_level == "high"
    assert specs["info"].risk_level == "low"
    # execute 闭包：调 manager.call_tool 并提取文本
    assert specs["echo_out"]["ok"] is True
    assert specs["echo_out"]["text"] == "echo: closure-ok"
    # 断开 → 注销
    assert specs["echo_after"] is None


def test_disabled_server_tool_enabled_false():
    """server.enabled=False → 注册的工具 enabled 跟随（ToolRunner 会拦截）。"""
    server_id = _make_server(name=TEST_PREFIX + "dis", enabled=False)
    server_name = TEST_PREFIX + "dis"
    spec = {}

    async def _flow():
        await mcp_manager.connect(server_id)
        spec["s"] = get_tool(f"mcp.{server_name}.echo")
        await mcp_manager.disconnect(server_id)

    _run(_flow())
    assert spec["s"] is not None
    assert spec["s"].enabled is False


def test_list_tools_cache_when_disconnected():
    """未连接时 list_tools 回退 DB 缓存。"""
    cache = [{"name": "echo", "description": "d", "input_schema": {}}]
    server_id = _make_server(name=TEST_PREFIX + "cache", tools_cache=json.dumps(cache))
    tools = _run(mcp_manager.list_tools(server_id, refresh=False))
    assert tools == cache


def test_worker_failure_finally_unregisters_tools(monkeypatch):
    """V2-5：worker 因未捕获异常退出（非 disconnect 主动断开）时 finally 注销工具。

    修复前 finally 只置 DISCONNECTED，不调 _unregister_conn_tools → ToolRegistry 残留已死连接
    的幽灵工具。修复后 finally 注销工具（重复调用幂等）。
    """
    from app.mcp.manager import _Connection, _TransportConfig

    server_id = _make_server(name=TEST_PREFIX + "ghost")
    conn = _Connection(server_id=server_id, server_name=TEST_PREFIX + "ghost")
    # 模拟工具已注册（connect 成功过）：conn.tools 有内容，registry 有 mcp.* 声明
    conn.tools = [{"name": "echo", "description": "d", "input_schema": {}}]
    unregistered = []

    async def fake_unregister(c):
        unregistered.append(c.server_id)
        c.tools = []

    class _FailTransport:
        async def __aenter__(self):
            raise RuntimeError("simulated transport failure")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(mcp_manager, "_unregister_conn_tools", fake_unregister)
    monkeypatch.setattr(mcp_manager, "_open_transport", lambda cfg: _FailTransport())

    cfg = _TransportConfig(transport="stdio", command="node")

    async def _flow():
        # _worker_main 内部 catch 异常置 error，finally 注销工具
        await mcp_manager._worker_main(conn, cfg)

    _run(_flow())
    # worker 异常退出时 finally 注销了该连接的工具，且工具列表被清空
    assert unregistered == [server_id]
    assert conn.tools == []
    assert conn.status == "disconnected"


def test_disconnect_acquires_connect_lock(monkeypatch):
    """V2-6：disconnect 获取 per-server connect_lock（懒创建）与 connect() 互斥。

    修复前 disconnect 不获取锁，并发 connect/disconnect 可能竞态（connect 建立完成但工具已被注销）。
    修复后 disconnect 在锁内执行注销/关停，且懒创建 connect_lock。
    """
    from app.mcp.manager import _Connection

    server_id = _make_server(name=TEST_PREFIX + "lk")
    conn = _Connection(server_id=server_id, server_name=TEST_PREFIX + "lk")
    mcp_manager._conns[server_id] = conn
    lock_held = {}

    async def fake_unregister(c):
        # 在 disconnect 的 async with conn.connect_lock 内执行 → 锁应被持有
        lock_held["held"] = c.connect_lock.locked()
        c.tools = []

    async def noop_shutdown(c):
        pass

    async def noop_update(*a, **k):
        pass

    monkeypatch.setattr(mcp_manager, "_unregister_conn_tools", fake_unregister)
    monkeypatch.setattr(mcp_manager, "_request_shutdown", noop_shutdown)
    monkeypatch.setattr(mcp_manager, "_update_db_status", noop_update)

    async def _flow():
        await mcp_manager.disconnect(server_id)

    _run(_flow())
    # disconnect 注册/关停在锁内执行（与 connect 互斥），且懒创建了 connect_lock
    assert lock_held["held"] is True
    assert conn.connect_lock is not None
    assert conn.status == "disconnected"


# ------------------------------------------------------------------ API 鉴权

def test_api_unauthenticated_401():
    from app.api.mcp import router as mcp_router

    app = FastAPI()
    app.include_router(mcp_router)  # 不覆盖 get_current_user_id → 走真实 HTTPBearer，无凭据 401
    client = TestClient(app)
    assert client.get("/api/v1/mcp/servers").status_code == 401
    assert client.post("/api/v1/mcp/servers", json={"name": "x", "command": "y"}).status_code == 401


def test_api_non_admin_403(monkeypatch):
    async def _fake(uid):
        return False
    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)

    client = _make_client(OTHER)
    # 读取允许（登录可见）
    assert client.get("/api/v1/mcp/servers").status_code == 200
    # 写/管理端点 → 403
    assert client.post("/api/v1/mcp/servers", json={"name": "x", "command": "y"}).status_code == 403
    assert client.put("/api/v1/mcp/servers/1", json={"enabled": True}).status_code == 403
    assert client.delete("/api/v1/mcp/servers/1").status_code == 403
    assert client.post("/api/v1/mcp/servers/1/connect").status_code == 403
    assert client.post("/api/v1/mcp/servers/1/disconnect").status_code == 403
    assert client.post("/api/v1/mcp/servers/1/test").status_code == 403


# ------------------------------------------------------------------ API CRUD + 校验

def test_api_crud_flow(monkeypatch):
    async def _fake(uid):
        return True
    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)

    client = _make_client(ADMIN)
    # 创建
    r = client.post("/api/v1/mcp/servers", json={
        "name": TEST_PREFIX + "crud",
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-c", MOCK_SERVER],
        "enabled": True,
        "auto_connect": False,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == TEST_PREFIX + "crud"
    assert data["status"] == "disconnected"
    sid = data["id"]

    # 列表包含
    lst = client.get("/api/v1/mcp/servers").json()
    assert any(it["id"] == sid for it in lst["items"])

    # 重复名 → 400
    dup = client.post("/api/v1/mcp/servers", json={
        "name": TEST_PREFIX + "crud", "command": sys.executable, "args": ["-c", MOCK_SERVER],
    })
    assert dup.status_code == 400

    # 修改
    upd = client.put(f"/api/v1/mcp/servers/{sid}", json={"enabled": False, "auto_connect": True})
    assert upd.status_code == 200
    assert upd.json()["enabled"] is False
    assert upd.json()["auto_connect"] is True

    # 删除
    dl = client.delete(f"/api/v1/mcp/servers/{sid}")
    assert dl.status_code == 200
    assert dl.json()["deleted"] is True

    # 删除后 404
    assert client.get(f"/api/v1/mcp/servers/{sid}/tools").status_code == 404


def test_api_create_validation(monkeypatch):
    async def _fake(uid):
        return True
    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)
    client = _make_client(ADMIN)

    assert client.post("/api/v1/mcp/servers", json={"name": "bad name!", "command": "x"}).status_code == 400
    assert client.post("/api/v1/mcp/servers", json={"name": "", "command": "x"}).status_code == 400
    assert client.post("/api/v1/mcp/servers", json={"name": "a", "transport": "sse", "command": "x"}).status_code == 400
    assert client.post("/api/v1/mcp/servers", json={"name": "a", "command": ""}).status_code == 400
    # 非本用户/不存在的 server → 404
    assert client.get("/api/v1/mcp/servers/999999/tools").status_code == 404
    assert client.put("/api/v1/mcp/servers/999999", json={"enabled": True}).status_code == 404
    assert client.delete("/api/v1/mcp/servers/999999").status_code == 404
    assert client.post("/api/v1/mcp/servers/999999/connect").status_code == 404
    assert client.post("/api/v1/mcp/servers/999999/test").status_code == 404


def test_api_update_server_stdio_requires_command(monkeypatch):
    """V2-11：transport 改为 stdio 且最终 command 为空 → 400。

    修复前从 sse/streamable_http 改 stdio 且 body 不含 command 时不校验（command 沿用旧值 None），
    保存成功但连接时报 command required。修复后最终 command 为空即返回 400。
    """
    async def _fake(uid):
        return True
    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)

    async def _make_sse_server(name):
        async with async_session_factory() as db:
            row = MCPServer(
                user_id=ADMIN, name=name, transport="sse", command=None,
                args_json="[]", env_json="{}", url="http://127.0.0.1:9999",
                headers_json="{}", enabled=True, auto_connect=False,
                tools_cache_json="[]", status="disconnected",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id

    sid = _run(_make_sse_server(TEST_PREFIX + "v2_11"))
    client = _make_client(ADMIN)
    # sse → stdio，body 未提供 command（command 沿用存量 None）→ 400
    r = client.put(f"/api/v1/mcp/servers/{sid}", json={"transport": "stdio"})
    assert r.status_code == 400, r.text
    # 提供了 command → 成功
    r2 = client.put(
        f"/api/v1/mcp/servers/{sid}", json={"transport": "stdio", "command": sys.executable},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["transport"] == "stdio"
    assert r2.json()["command"] == sys.executable


# ------------------------------------------------------------------ API 连接 / 试连端点

def test_api_connect_and_tools(monkeypatch):
    async def _fake(uid):
        return True
    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)

    sid = _make_server(name=TEST_PREFIX + "apiconn")
    client = _make_client(ADMIN)
    # 连接在 TestClient 的事件循环内完成，因此断开也在同一会话内做，避免跨循环清理
    with client:
        r = client.post(f"/api/v1/mcp/servers/{sid}/connect")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["status"] == "connected"
        assert "echo" in [t["name"] for t in body["tools"]]

        tools = client.get(f"/api/v1/mcp/servers/{sid}/tools").json()
        assert tools["total"] >= len(TEST_MOCK_TOOLS)
        assert any(t["name"] == "echo" for t in tools["items"])

        d = client.post(f"/api/v1/mcp/servers/{sid}/disconnect")
        assert d.status_code == 200


def test_api_test_endpoint_no_save(monkeypatch):
    async def _fake(uid):
        return True
    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)

    sid = _make_server(name=TEST_PREFIX + "apitest", status="disconnected")
    client = _make_client(ADMIN)
    r = client.post(f"/api/v1/mcp/servers/{sid}/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert any(t["name"] == "echo" for t in body["tools"])

    async def _check():
        async with async_session_factory() as db:
            row = (await db.execute(select(MCPServer).where(MCPServer.id == sid))).scalar_one()
            return row.status
    # 试连不保存状态：DB status 仍为 disconnected、注册表无 mcp 工具
    assert _run(_check()) == "disconnected"
    assert get_tool(f"mcp.{TEST_PREFIX + 'apitest'}.echo") is None
