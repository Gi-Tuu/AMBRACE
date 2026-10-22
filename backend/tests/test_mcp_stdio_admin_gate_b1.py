# -*- coding: utf-8 -*-
"""安全 B1-B：MCP stdio 服务器管理员执行权（SEC-P0-1）矩阵测试。

只用临时 SQLite + 假 stdio transport / 假 worker / 假 ClientSession：
- 不启动真实 MCP 子进程，不联网；
- 用真实权限判定路径（users.server_admin / disabled_at / deleted_at）而不是把 helper 桩成 True；
- 覆盖 connect、test_connect、reconnect_all、call_tool 自动重连、已连接后的下一次调用，
  以及 tools/resources/prompts 刷新和 get_resource/get_prompt 等命令队列入口；
- HTTP create/update/connect/test 的家庭管理员（is_admin）与服务器管理员（server_admin）矩阵。

测试先于修复编写：旧实现没有 stdio 闸，期望拒绝的用例会红。
"""
import asyncio
import time
from datetime import datetime
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, update
from starlette.testclient import TestClient

from app.application import permission_service
from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.mcp.connection import _Connection
from app.mcp.manager import (
    MCPClientManager,
    STATUS_CONNECTED,
    _TransportConfig,
    mcp_manager,
)
from app.models.mcp import McpCallLog, MCPServer
from app.models.user import User

PREFIX = "mcp_b1_"
UID_FAMILY = 9401      # 家庭主账号，但不是 server_admin
UID_ADMIN = 9402       # server_admin + 家庭主账号
UID_PLAIN = 9403       # 普通账号
UID_DISABLED = 9404    # server_admin，但账号被禁用
UID_DELETED = 9405     # server_admin，但账号在回收站
UID_ADMIN2 = 9406      # 另一个 server_admin（用于归属不匹配）
UIDS = (UID_FAMILY, UID_ADMIN, UID_PLAIN, UID_DISABLED, UID_DELETED, UID_ADMIN2)

_MANAGERS: list[MCPClientManager] = []


def _run(coro):
    return asyncio.run(coro)


def _mgr() -> MCPClientManager:
    m = MCPClientManager()
    _MANAGERS.append(m)
    return m


@pytest.fixture(autouse=True)
def _no_real_mcp_workdir(monkeypatch):
    """禁止测试在 backend/data/mcp 下创建真实工作目录。"""
    monkeypatch.setattr(MCPClientManager, "_workdir", lambda self, row: None)
    yield


async def _cancel_workers(mgr: MCPClientManager) -> None:
    for conn in list(mgr._conns.values()):
        worker = getattr(conn, "_worker", None)
        if worker is not None and not worker.done():
            try:
                worker.cancel()
            except Exception:
                pass
            try:
                await worker
            except BaseException:
                pass
    mgr._conns.clear()


@pytest.fixture()
def b1_db():
    async def setup():
        permission_service._admin_cache.clear()
        permission_service._server_admin_cache.clear()
        permission_service._account_state_cache.clear()
        disabled = datetime(2026, 1, 1, 0, 0, 0)
        async with async_session_factory() as db:
            db.add_all([
                User(id=UID_FAMILY, username=PREFIX + "family", nickname="family",
                     is_admin=True, server_admin=False),
                User(id=UID_ADMIN, username=PREFIX + "admin", nickname="admin",
                     is_admin=True, server_admin=True),
                User(id=UID_PLAIN, username=PREFIX + "plain", nickname="plain",
                     is_admin=False, server_admin=False),
                User(id=UID_DISABLED, username=PREFIX + "disabled", nickname="disabled",
                     is_admin=True, server_admin=True, disabled_at=disabled),
                User(id=UID_DELETED, username=PREFIX + "deleted", nickname="deleted",
                     is_admin=True, server_admin=True, disabled_at=disabled, deleted_at=disabled),
                User(id=UID_ADMIN2, username=PREFIX + "admin2", nickname="admin2",
                     is_admin=True, server_admin=True),
            ])
            await db.commit()
            specs = (
                (UID_FAMILY, "stdio_family", "stdio"),
                (UID_ADMIN, "stdio_admin", "stdio"),
                (UID_DISABLED, "stdio_disabled", "stdio"),
                (UID_DELETED, "stdio_deleted", "stdio"),
                (UID_ADMIN2, "stdio_admin2", "stdio"),
                (UID_FAMILY, "sse_family", "sse"),
                (UID_ADMIN, "sse_admin", "sse"),
            )
            ids = {}
            for owner, suffix, transport in specs:
                row = MCPServer(
                    user_id=owner,
                    name=PREFIX + suffix,
                    transport=transport,
                    command="python" if transport == "stdio" else None,
                    args_json="[]",
                    env_json="{}",
                    url="https://mcp.example.com/sse" if transport != "stdio" else None,
                    headers_json="{}",
                    enabled=True,
                    auto_connect=False,
                    tools_cache_json="[]",
                    status="disconnected",
                )
                db.add(row)
                await db.commit()
                await db.refresh(row)
                ids[suffix] = row.id
            return ids

    ids = _run(setup())
    yield ids

    async def cleanup():
        for m in _MANAGERS:
            await _cancel_workers(m)
        _MANAGERS.clear()
        await _cancel_workers(mcp_manager)
        async with async_session_factory() as db:
            await db.execute(delete(McpCallLog).where(McpCallLog.user_id.in_(UIDS)))
            await db.execute(delete(McpCallLog).where(McpCallLog.server_id.in_(list(ids.values()))))
            await db.execute(delete(MCPServer).where(MCPServer.name.like(PREFIX + "%")))
            await db.execute(delete(User).where(User.id.in_(UIDS)))
            await db.commit()
        permission_service._admin_cache.clear()
        permission_service._server_admin_cache.clear()
        permission_service._account_state_cache.clear()

    _run(cleanup())


def _set_user(user_id: int, **values) -> None:
    async def _do():
        async with async_session_factory() as db:
            await db.execute(update(User).where(User.id == user_id).values(**values))
            await db.commit()
    _run(_do())


def _set_server(server_id: int, **values) -> None:
    async def _do():
        async with async_session_factory() as db:
            await db.execute(update(MCPServer).where(MCPServer.id == server_id).values(**values))
            await db.commit()
    _run(_do())


class _FakeWorker:
    """假 worker：记录是否被启动；连接后循环处理命令，便于 call_tool 用例结束。"""

    def __init__(self, manager: MCPClientManager):
        self.manager = manager
        self.calls = []

    async def __call__(self, conn, cfg):
        self.calls.append({
            "server_id": conn.server_id,
            "transport": cfg.transport,
            "command": getattr(cfg, "command", ""),
        })
        conn.status = STATUS_CONNECTED
        self.manager._set_ready(conn, ok=True)
        try:
            while True:
                cmd = await conn._queue.get()
                if cmd.get("kind") == "shutdown":
                    fut = cmd.get("future")
                    if fut is not None and not fut.done():
                        fut.set_result(True)
                    break
                fut = cmd.get("future")
                if fut is not None and not fut.done():
                    fut.set_result({"content": [], "isError": False})
        finally:
            conn.status = "disconnected"


def _install_fake_worker(monkeypatch, mgr: MCPClientManager) -> _FakeWorker:
    fake = _FakeWorker(mgr)
    monkeypatch.setattr(mgr, "_worker_main", fake)
    return fake


class _RecordingTransport:
    def __init__(self):
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        return (object(), object())

    async def __aexit__(self, *args):
        return False


class _FakeClientSession:
    def __init__(self, read, write, message_handler=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=[])

    async def list_resources(self):
        return SimpleNamespace(resources=[])

    async def list_prompts(self):
        return SimpleNamespace(prompts=[])


def _run_worker(monkeypatch, mgr, conn, cfg, transport: _RecordingTransport) -> dict:
    monkeypatch.setattr("mcp.ClientSession", _FakeClientSession)
    monkeypatch.setattr(mgr, "_open_transport", lambda _cfg: transport)

    async def flow():
        loop = asyncio.get_running_loop()
        conn._queue = asyncio.Queue()
        conn._ready = loop.create_future()
        task = asyncio.create_task(mgr._worker_main(conn, cfg))
        try:
            await asyncio.wait_for(asyncio.shield(conn._ready), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        state = {"status_at_ready": conn.status, "last_error": conn.last_error}
        if not task.done():
            fut = loop.create_future()
            await conn._queue.put({"kind": "shutdown", "future": fut})
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
        state["final_status"] = conn.status
        return state

    return _run(flow())


def _stdio_cfg(server_id: int, owner_id: int) -> _TransportConfig:
    cfg = _TransportConfig(transport="stdio", command="python")
    # 动态补字段：旧实现还没有这些字段时，测试仍然可以构造（红测失败点落在行为而非 TypeError）。
    cfg.server_id = server_id
    cfg.owner_user_id = owner_id
    cfg.stdio_permit = None
    return cfg


def _fake_connected(mgr: MCPClientManager, server_id: int, *, transport="stdio",
                    user_id=UID_FAMILY) -> _Connection:
    async def _do():
        loop = asyncio.get_running_loop()
        conn = _Connection(server_id=server_id, server_name="srv", user_id=user_id)
        conn.transport = transport
        conn.status = STATUS_CONNECTED
        conn._worker = loop.create_future()  # pending → is_connected 成立
        conn._queue = asyncio.Queue()
        mgr._conns[server_id] = conn
        return conn

    return _run(_do())


def _probe_queue(coro, queue: asyncio.Queue) -> int:
    async def flow():
        task = asyncio.create_task(coro)
        await asyncio.sleep(0.2)
        size = queue.qsize()
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        return size

    return _run(flow())


def _call_once(mgr: MCPClientManager, server_id: int):
    async def flow():
        queue = mgr._conns[server_id]._queue

        async def consume():
            try:
                cmd = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                return None
            fut = cmd.get("future")
            if fut is not None and not fut.done():
                fut.set_result({"content": [{"type": "text", "text": "ok"}], "isError": False})
            return cmd

        consumer = asyncio.create_task(consume())
        result = await mgr._do_call_tool(server_id, "echo", {})
        cmd = await consumer
        return result, cmd

    return _run(flow())


# ------------------------------------------------------------------ manager：connect

def test_connect_denies_family_admin_stdio_without_spawn(b1_db, monkeypatch):
    mgr = _mgr()
    fake = _install_fake_worker(monkeypatch, mgr)

    res = _run(mgr.connect(b1_db["stdio_family"]))

    assert res["ok"] is False, res
    assert "server administrator" in (res.get("error") or "").lower()
    assert fake.calls == [], "未授权时不得创建 worker/子进程"


def test_connect_allows_server_admin_stdio(b1_db, monkeypatch):
    mgr = _mgr()
    fake = _install_fake_worker(monkeypatch, mgr)

    res = _run(mgr.connect(b1_db["stdio_admin"]))

    assert res["ok"] is True, res
    assert len(fake.calls) == 1, "合法服务器管理员必须真的进入连接路径（防全员拒绝假绿）"
    assert fake.calls[0]["transport"] == "stdio"


@pytest.mark.parametrize("suffix", ["stdio_disabled", "stdio_deleted"])
def test_connect_denies_disabled_or_deleted_owner(b1_db, monkeypatch, suffix):
    mgr = _mgr()
    fake = _install_fake_worker(monkeypatch, mgr)

    res = _run(mgr.connect(b1_db[suffix]))

    assert res["ok"] is False, res
    assert fake.calls == []


def test_connect_denies_owner_revoked_after_row_load(b1_db, monkeypatch):
    mgr = _mgr()
    fake = _install_fake_worker(monkeypatch, mgr)
    _set_user(UID_ADMIN, server_admin=False)

    res = _run(mgr.connect(b1_db["stdio_admin"]))

    assert res["ok"] is False, res
    assert fake.calls == []


# ------------------------------------------------------------------ manager：test_connect / probe

def test_test_connect_denies_family_admin_without_probe(b1_db, monkeypatch):
    mgr = _mgr()
    calls = []

    async def _probe(cfg):
        calls.append(cfg)
        return [{"name": "echo"}]

    monkeypatch.setattr(mgr, "_probe_connect", _probe)
    res = _run(mgr.test_connect(b1_db["stdio_family"]))

    assert res["ok"] is False, res
    assert "server administrator" in (res.get("error") or "").lower()
    assert calls == [], "未授权不得进入试连/transport"


def test_test_connect_allows_server_admin(b1_db, monkeypatch):
    mgr = _mgr()
    calls = []

    async def _probe(cfg):
        calls.append(cfg)
        return [{"name": "echo"}]

    monkeypatch.setattr(mgr, "_probe_connect", _probe)
    res = _run(mgr.test_connect(b1_db["stdio_admin"]))

    assert res["ok"] is True, res
    assert len(calls) == 1


# ------------------------------------------------------------------ manager：worker 最后一道闸

def test_worker_main_denies_before_open_transport(b1_db, monkeypatch):
    mgr = _mgr()
    transport = _RecordingTransport()
    conn = _Connection(server_id=b1_db["stdio_family"], server_name="srv", user_id=UID_FAMILY)
    cfg = _stdio_cfg(b1_db["stdio_family"], UID_FAMILY)

    state = _run_worker(monkeypatch, mgr, conn, cfg, transport)

    assert transport.entered is False, "worker 不得在未授权时进入 stdio transport"
    assert state["status_at_ready"] != STATUS_CONNECTED
    assert "server administrator" in (state["last_error"] or "").lower()


def test_worker_main_allows_server_admin(b1_db, monkeypatch):
    mgr = _mgr()
    transport = _RecordingTransport()
    conn = _Connection(server_id=b1_db["stdio_admin"], server_name="srv", user_id=UID_ADMIN)
    cfg = _stdio_cfg(b1_db["stdio_admin"], UID_ADMIN)

    state = _run_worker(monkeypatch, mgr, conn, cfg, transport)

    assert transport.entered is True, "合法服务器管理员必须真的进入 transport（防全员拒绝假绿）"
    assert state["status_at_ready"] == STATUS_CONNECTED


# ------------------------------------------------------------------ _open_transport 最终许可

def _fake_stdio_module(monkeypatch) -> list:
    calls = []
    mod = ModuleType("mcp.client.stdio")
    mod.StdioServerParameters = lambda **kw: SimpleNamespace(**kw)

    class _CM(_RecordingTransport):
        pass

    def _stdio_client(params):
        calls.append(params)
        return _CM()

    mod.stdio_client = _stdio_client
    monkeypatch.setitem(__import__("sys").modules, "mcp.client.stdio", mod)
    return calls


def test_open_transport_stdio_requires_permit(b1_db, monkeypatch):
    mgr = _mgr()
    calls = _fake_stdio_module(monkeypatch)
    cfg = _stdio_cfg(b1_db["stdio_admin"], UID_ADMIN)

    with pytest.raises(ValueError):
        mgr._open_transport(cfg)
    assert calls == [], "没有本次许可时不得调用 stdio_client"


def test_open_transport_stdio_permit_bound_and_one_shot(b1_db, monkeypatch):
    mgr = _mgr()
    calls = _fake_stdio_module(monkeypatch)
    cfg = _stdio_cfg(b1_db["stdio_admin"], UID_ADMIN)
    cfg.stdio_permit = SimpleNamespace(server_id=cfg.server_id, owner_user_id=cfg.owner_user_id)

    mgr._open_transport(cfg)
    assert calls, "合法许可必须调用 stdio_client"
    assert getattr(cfg, "stdio_permit", None) is None, "许可必须一次性消费，不能永久复用"
    with pytest.raises(ValueError):
        mgr._open_transport(cfg)


def test_open_transport_stdio_rejects_wrong_server_or_owner(b1_db, monkeypatch):
    mgr = _mgr()
    calls = _fake_stdio_module(monkeypatch)
    cfg = _stdio_cfg(b1_db["stdio_admin"], UID_ADMIN)

    cfg.stdio_permit = SimpleNamespace(server_id=b1_db["stdio_admin2"], owner_user_id=UID_ADMIN)
    with pytest.raises(ValueError):
        mgr._open_transport(cfg)
    cfg.stdio_permit = SimpleNamespace(server_id=cfg.server_id, owner_user_id=UID_ADMIN2)
    with pytest.raises(ValueError):
        mgr._open_transport(cfg)
    assert calls == []


# ------------------------------------------------------------------ 已连接后的调用/刷新

def test_connected_stdio_call_denied_after_revocation(b1_db):
    mgr = _mgr()
    sid = b1_db["stdio_admin"]

    async def flow():
        loop = asyncio.get_running_loop()
        conn = _Connection(server_id=sid, server_name="srv", user_id=UID_ADMIN)
        conn.transport = "stdio"
        conn.status = STATUS_CONNECTED
        conn._worker = loop.create_future()  # pending → is_connected 成立
        conn._queue = asyncio.Queue()
        mgr._conns[sid] = conn

        async def call_once():
            async def consume():
                try:
                    cmd = await asyncio.wait_for(conn._queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    return None
                fut = cmd.get("future")
                if fut is not None and not fut.done():
                    fut.set_result({"content": [{"type": "text", "text": "ok"}], "isError": False})
                return cmd

            consumer = asyncio.create_task(consume())
            result = await mgr._do_call_tool(sid, "echo", {})
            cmd = await consumer
            return result, cmd

        first, first_cmd = await call_once()
        async with async_session_factory() as db:
            await db.execute(update(User).where(User.id == UID_ADMIN).values(server_admin=False))
            await db.commit()
        second, second_cmd = await call_once()
        return first, first_cmd, second, second_cmd

    first, first_cmd, second, second_cmd = _run(flow())
    assert first["isError"] is False and first_cmd is not None, "撤权前合法调用应真的进入队列"
    assert second["isError"] is True, "撤权后的下一次调用必须拒绝"
    assert "server administrator" in (second.get("error") or "").lower()
    assert second_cmd is None, "撤权后不得把命令写入 stdio 队列"


@pytest.mark.parametrize("suffix", ["stdio_disabled", "stdio_deleted"])
def test_connected_stdio_call_denied_for_disabled_or_deleted_owner(b1_db, suffix):
    mgr = _mgr()
    sid = b1_db[suffix]
    _fake_connected(mgr, sid, transport="stdio", user_id=UID_DISABLED)

    result, cmd = _call_once(mgr, sid)

    assert result["isError"] is True
    assert cmd is None


def test_connected_stdio_call_db_error_fails_closed(b1_db, monkeypatch):
    mgr = _mgr()
    sid = b1_db["stdio_admin"]
    _fake_connected(mgr, sid, transport="stdio", user_id=UID_ADMIN)

    import app.db.database as db_mod

    class _Boom:
        def __call__(self):
            raise RuntimeError("injected db failure")

    monkeypatch.setattr(db_mod, "async_session_factory", _Boom())

    result, cmd = _call_once(mgr, sid)

    assert result["isError"] is True, "DB 查询错误必须拒绝，不得回退 env 放行"
    assert cmd is None


@pytest.mark.parametrize("entry", ["list_tools", "list_resources", "list_prompts", "get_resource", "get_prompt"])
def test_stdio_refresh_and_read_entries_denied_after_revocation(b1_db, entry):
    mgr = _mgr()
    sid = b1_db["stdio_family"]
    conn = _fake_connected(mgr, sid, transport="stdio", user_id=UID_FAMILY)
    queue = conn._queue

    if entry == "list_tools":
        coro = mgr.list_tools(sid, refresh=True)
    elif entry == "list_resources":
        coro = mgr.list_resources(sid, refresh=True)
    elif entry == "list_prompts":
        coro = mgr.list_prompts(sid, refresh=True)
    elif entry == "get_resource":
        coro = mgr.get_resource(sid, "file:///x")
    else:
        coro = mgr.get_prompt(sid, "summary", {})

    assert _probe_queue(coro, queue) == 0, f"{entry} 不得在撤权后向 stdio 队列写命令"


def test_sse_connect_not_gated_by_server_admin(b1_db, monkeypatch):
    mgr = _mgr()
    fake = _install_fake_worker(monkeypatch, mgr)
    monkeypatch.setattr("app.mcp.manager.validate_mcp_url", lambda url, allow_loopback=False: None)

    res = _run(mgr.connect(b1_db["sse_family"]))

    assert res["ok"] is True, res
    assert len(fake.calls) == 1, "SSE 正常家庭用户必须保持可用"


def test_live_stdio_connection_cannot_be_relabelled_as_sse(b1_db):
    """DB 传输类型被改成 SSE 时，已连接的 stdio worker 不得被重贴标签绕过闸。"""
    mgr = _mgr()
    sid = b1_db["stdio_admin"]
    conn = _fake_connected(mgr, sid, transport="stdio", user_id=UID_ADMIN)
    _set_server(sid, transport="sse", url="https://mcp.example.com/sse")

    res = _run(mgr.connect(sid))

    assert res["ok"] is False, res
    assert "transport" in (res.get("error") or "").lower()
    assert getattr(conn, "transport", "") == "stdio", "不得把存活的 stdio worker 标成 SSE"
    result, cmd = _call_once(mgr, sid)
    assert result["isError"] is True and cmd is None


# ------------------------------------------------------------------ auto_connect / call_tool 自动重连

def test_reconnect_all_denies_unauthorized_auto_connect_row(b1_db, monkeypatch):
    mgr = _mgr()
    fake = _install_fake_worker(monkeypatch, mgr)
    sid = b1_db["stdio_family"]
    _set_server(sid, auto_connect=True)

    _run(mgr.reconnect_all(force=True))

    assert fake.calls == [], "auto_connect=True 的旧行也不能绕过闸"
    assert mgr._fail_streak.get(sid) == 1


def test_call_tool_auto_reconnect_denied(b1_db, monkeypatch):
    mgr = _mgr()
    fake = _install_fake_worker(monkeypatch, mgr)
    sid = b1_db["stdio_family"]

    res = _run(mgr.call_tool(sid, "echo", {}))

    assert res["isError"] is True
    assert fake.calls == [], "_do_call_tool 的自动重连不得绕过闸"


# ------------------------------------------------------------------ HTTP 身份矩阵

def _client(user_id: int) -> TestClient:
    from app.api.mcp import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _patch_manager_noops(monkeypatch):
    async def _connect(server_id):
        return {"ok": True, "status": "connected", "tools": []}

    async def _test(server_id):
        return {"ok": True, "tools": []}

    async def _disconnect(server_id):
        return {"ok": True}

    monkeypatch.setattr(mcp_manager, "connect", _connect)
    monkeypatch.setattr(mcp_manager, "test_connect", _test)
    monkeypatch.setattr(mcp_manager, "disconnect", _disconnect)


def _create_stdio(client: TestClient, name: str):
    return client.post("/api/v1/mcp/servers", json={
        "name": name, "transport": "stdio", "command": "python", "args": [],
        "enabled": True, "auto_connect": False,
    })


def test_api_create_stdio_403_for_family_admin(b1_db):
    client = _client(UID_FAMILY)
    r = _create_stdio(client, PREFIX + "api_family")
    assert r.status_code == 403, r.text


def test_api_create_stdio_200_for_server_admin(b1_db):
    client = _client(UID_ADMIN)
    r = _create_stdio(client, PREFIX + "api_admin")
    assert r.status_code == 200, r.text
    assert r.json()["transport"] == "stdio"


def test_api_create_stdio_403_for_disabled_server_admin(b1_db):
    client = _client(UID_DISABLED)
    r = _create_stdio(client, PREFIX + "api_disabled")
    assert r.status_code == 403, r.text


def test_api_create_sse_200_for_family_admin(b1_db, monkeypatch):
    monkeypatch.setattr("app.mcp.manager.validate_mcp_url", lambda url, allow_loopback=False: None)
    client = _client(UID_FAMILY)
    r = client.post("/api/v1/mcp/servers", json={
        "name": PREFIX + "api_sse_family", "transport": "sse",
        "url": "https://mcp.example.com/sse", "enabled": True, "auto_connect": False,
    })
    assert r.status_code == 200, r.text


def test_api_update_stdio_403_for_family_admin(b1_db, monkeypatch):
    _patch_manager_noops(monkeypatch)
    client = _client(UID_FAMILY)
    r = client.put(f"/api/v1/mcp/servers/{b1_db['stdio_family']}", json={"enabled": False})
    assert r.status_code == 403, r.text


def test_api_switch_sse_to_stdio_403_for_family_admin(b1_db, monkeypatch):
    _patch_manager_noops(monkeypatch)
    client = _client(UID_FAMILY)
    r = client.put(f"/api/v1/mcp/servers/{b1_db['sse_family']}", json={
        "transport": "stdio", "command": "python",
    })
    assert r.status_code == 403, r.text


def test_api_connect_and_test_stdio_403_for_family_admin(b1_db, monkeypatch):
    _patch_manager_noops(monkeypatch)
    client = _client(UID_FAMILY)
    for action in ("connect", "test"):
        r = client.post(f"/api/v1/mcp/servers/{b1_db['stdio_family']}/{action}")
        assert r.status_code == 403, f"{action}: {r.text}"


def test_api_connect_and_test_stdio_200_for_server_admin(b1_db, monkeypatch):
    _patch_manager_noops(monkeypatch)
    client = _client(UID_ADMIN)
    for action in ("connect", "test"):
        r = client.post(f"/api/v1/mcp/servers/{b1_db['stdio_admin']}/{action}")
        assert r.status_code == 200, f"{action}: {r.text}"


def test_api_connect_sse_200_for_family_admin(b1_db, monkeypatch):
    _patch_manager_noops(monkeypatch)
    client = _client(UID_FAMILY)
    r = client.post(f"/api/v1/mcp/servers/{b1_db['sse_family']}/connect")
    assert r.status_code == 200, r.text


def test_api_gate_ignores_permission_service_cache(b1_db):
    permission_service._server_admin_cache[UID_FAMILY] = (True, time.time() + 3600)
    permission_service._server_admin_cache[UID_ADMIN] = (False, time.time() + 3600)
    client = _client(UID_FAMILY)
    r = _create_stdio(client, PREFIX + "api_cache_family")
    assert r.status_code == 403, "闸必须新鲜读库，不得被 30s 缓存放行"

    client2 = _client(UID_ADMIN)
    r2 = _create_stdio(client2, PREFIX + "api_cache_admin")
    assert r2.status_code == 200, r2.text


def test_api_owner_mismatch_still_404(b1_db, monkeypatch):
    _patch_manager_noops(monkeypatch)
    client = _client(UID_ADMIN2)
    r = client.post(f"/api/v1/mcp/servers/{b1_db['stdio_family']}/connect")
    assert r.status_code == 404, r.text
