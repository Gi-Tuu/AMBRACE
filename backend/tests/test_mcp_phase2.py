# -*- coding: utf-8 -*-
"""AMBRACE MCP 接入 Phase 2 测试（2026-08-26）。

覆盖：
1. SSE / streamable_http 传输：_build_config（stdio/sse/streamable_http 分支）、SSRF 校验、
   _open_transport（sse 传 url+headers / streamable_http 建 httpx client）、API 传输/url 校验。
2. 权限三档：permission_service.check_mcp_mode（高风险默认 ask / 低风险默认 allow /
   显式覆盖 / 全局收紧）、tool_runner.check_tool_permission 对 mcp scope 生效。
3. context_builder 工具声明注入：有/无 MCP 工具时的声明收集与格式化。
4. actions 的 MCP 标记解析 + strip；mcp_tools.run_mcp_tool_stage 经 ToolRunner 执行。
5. 状态 Event Bus：_update_db_status 广播 mcp.server_status。

安全要点：所有 DB 写用测试前缀/独立 user_id，teardown 清理；manager 连接基元必须在同一事件循环完成。
"""
import asyncio
import json
import sys

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select
from starlette.testclient import TestClient

from app.agent.tools import (
    RISK_HIGH,
    RISK_LOW,
    ToolSpec,
    _REGISTRY,
    register_tool,
)
from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.events.bus import event_bus
from app.events.types import EventType
from app.mcp.manager import mcp_manager
from app.models.mcp import MCPServer
from app.models.agent import ToolPermission

ADMIN = 1
TEST_PREFIX = "mcp_ph2_"

# 独立测试 user_id（非主账号、无既有权限行）
PERM_UID = 9100


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ 清理

async def _cleanup():
    for sid in list(mcp_manager._conns.keys()):
        mcp_manager._conns.pop(sid, None)
    for name in list(_REGISTRY.keys()):
        if name.startswith("mcp."):
            _REGISTRY.pop(name, None)
    # 清空归属缓存，避免跨用例的 30s TTL 污染（P1 归属 filter 依赖该查询）
    from app.mcp import ownership as _ownership
    _ownership._CACHE.clear()
    async with async_session_factory() as db:
        await db.execute(delete(MCPServer).where(MCPServer.name.like(TEST_PREFIX + "%")))
        await db.execute(delete(ToolPermission).where(ToolPermission.user_id == PERM_UID))
        await db.commit()


@pytest.fixture(autouse=True)
def _isolate():
    _run(_cleanup())
    yield
    _run(_cleanup())


def _make_server(user_id=ADMIN, name=None, transport="stdio", command=None, args=None, env=None,
                 url=None, headers=None, enabled=True, auto_connect=True):
    async def _do():
        async with async_session_factory() as db:
            row = MCPServer(
                user_id=user_id,
                name=name or (TEST_PREFIX + "srv"),
                transport=transport,
                command=command or sys.executable,
                args_json=json.dumps(args if args is not None else ["-c", "print(1)"]),
                env_json=json.dumps(env or {}),
                url=url,
                headers_json=json.dumps(headers or {}),
                enabled=enabled,
                auto_connect=auto_connect,
                tools_cache_json="[]",
                status="disconnected",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id
    return _run(_do())


# ------------------------------------------------------------------ SSRF / 传输配置

def test_validate_url_blocks_private():
    from app.mcp.manager import validate_mcp_url

    for bad in ("http://127.0.0.1:8080", "http://localhost:3000", "http://169.254.169.254/latest"):
        with pytest.raises(ValueError):
            validate_mcp_url(bad)
    with pytest.raises(ValueError):
        validate_mcp_url("ftp://example.com/x")
    with pytest.raises(ValueError):
        validate_mcp_url("http://")  # 无 host


def test_validate_url_allow_private_flag(monkeypatch):
    from app.config import settings
    from app.mcp.manager import validate_mcp_url

    monkeypatch.setattr(settings, "mcp_http_allow_private", True)
    # 放行内网后不再抛错
    assert validate_mcp_url("http://127.0.0.1:8080") is None


def test_build_config_sse(monkeypatch):
    from app.config import settings
    from app.mcp.manager import MCPClientManager

    # 放行内网，避免 DNS/拦截；验证 sse 分支解析 url+headers
    monkeypatch.setattr(settings, "mcp_http_allow_private", True)
    sid = _make_server(transport="sse", url="http://127.0.0.1:8899", headers={"Authorization": "Bearer x"})
    row = None

    async def _load():
        nonlocal row
        async with async_session_factory() as db:
            row = (await db.execute(select(MCPServer).where(MCPServer.id == sid))).scalar_one()

    _run(_load())
    cfg = MCPClientManager()._build_config(row)
    assert cfg.transport == "sse"
    assert cfg.url == "http://127.0.0.1:8899"
    assert cfg.headers == {"Authorization": "Bearer x"}


def test_open_transport_sse(monkeypatch):
    from app.mcp.manager import MCPClientManager, _TransportConfig

    called = {}

    class _CM:
        async def __aenter__(self):
            return ("r", "w")

        async def __aexit__(self, *a):
            return None

    def fake_sse(url, headers=None, **kw):
        called["url"] = url
        called["headers"] = headers
        return _CM()

    monkeypatch.setattr("mcp.client.sse.sse_client", fake_sse)
    cfg = _TransportConfig(transport="sse", url="http://x", headers={"Authorization": "a"})
    cm = MCPClientManager()._open_transport(cfg)
    read, write = _run(cm.__aenter__())
    assert read == "r" and write == "w"
    assert called["url"] == "http://x"
    assert called["headers"] == {"Authorization": "a"}


def test_open_transport_streamable_http(monkeypatch):
    from app.mcp.manager import MCPClientManager, _TransportConfig

    called = {}

    class _CM:
        async def __aenter__(self):
            return ("r", "w")

        async def __aexit__(self, *a):
            return None

    class _Client:
        def __init__(self, **kwargs):
            called["client_kwargs"] = kwargs

        async def aclose(self):
            called["closed"] = True

    def fake_shc(url, http_client=None):
        called["url"] = url
        called["client"] = http_client
        return _CM()

    monkeypatch.setattr("mcp.client.streamable_http.streamable_http_client", fake_shc)
    monkeypatch.setattr("httpx.AsyncClient", _Client)

    cfg = _TransportConfig(transport="streamable_http", url="https://mcp.example.com", headers={"X": "1"})
    cm = MCPClientManager()._open_transport(cfg)
    read, write = _run(cm.__aenter__())
    _run(cm.__aexit__(None, None, None))
    assert read == "r" and write == "w"
    assert called["url"] == "https://mcp.example.com"
    assert isinstance(called["client"], _Client)
    assert called["closed"] is True  # _ManagedHttpTransport 退出时关闭 httpx client


# ------------------------------------------------------------------ 权限三档

def test_check_mcp_mode_risk_default():
    from app.services.permission_service import check_mcp_mode

    assert _run(check_mcp_mode(PERM_UID, "mcp_srv", "high")) == "ask"
    assert _run(check_mcp_mode(PERM_UID, "mcp_srv", "low")) == "allow"
    assert _run(check_mcp_mode(PERM_UID, "mcp_srv", "medium")) == "allow"


def test_check_mcp_mode_explicit_override():
    from app.services.permission_service import check_mcp_mode

    async def _do():
        async with async_session_factory() as db:
            db.add(ToolPermission(user_id=PERM_UID, scope="mcp_srv", level="allow"))
            await db.commit()
        return await check_mcp_mode(PERM_UID, "mcp_srv", "high")

    # 显式 allow 覆盖高风险默认 ask
    assert _run(_do()) == "allow"


def test_check_mcp_mode_follows_global_forbid():
    from app.services.permission_service import check_mcp_mode

    async def _do():
        async with async_session_factory() as db:
            db.add(ToolPermission(user_id=PERM_UID, scope="__global__", level="forbid"))
            await db.commit()
        return await check_mcp_mode(PERM_UID, "mcp_srv", "low")

    assert _run(_do()) == "forbid"


def test_check_tool_permission_mcp_scope():
    from app.agent.tool_runner import check_tool_permission

    # P1：工具需归属当前用户（server_id 属于 PERM_UID）才进入权限三档
    sid = _make_server(user_id=PERM_UID, name=TEST_PREFIX + "perm")

    async def _flow():
        hi = ToolSpec(name="mcp.srv.write_file", description="d", risk_level=RISK_HIGH, scope="mcp_srv", server_id=sid)
        lo = ToolSpec(name="mcp.srv.read_file", description="d", risk_level=RISK_LOW, scope="mcp_srv", server_id=sid)
        return await check_tool_permission(hi, user_id=PERM_UID), await check_tool_permission(lo, user_id=PERM_UID)

    high, low = _run(_flow())
    assert high == "ask"   # 高风险默认 ASK
    assert low == "allow"  # 低风险默认 ALLOW


# ------------------------------------------------------------------ context_builder 声明注入

def _reg_mcp_tool(name, *, risk=RISK_LOW, enabled=True, scope=None, desc="d", server_id=None):
    spec = ToolSpec(
        name=name, description=desc, risk_level=risk,
        scope=scope or "mcp_srv", enabled=enabled, input_schema={"type": "object"},
        server_id=server_id,
    )
    register_tool(spec)
    return spec


def test_mcp_declarations_with_tool(monkeypatch):
    from app.agent.context_builder import _build_mcp_tool_declarations, _format_mcp_declarations

    async def fake_mode(uid, scope, risk="medium"):
        return "allow"

    monkeypatch.setattr("app.services.permission_service.check_mcp_mode", fake_mode)
    # P1：工具需归属当前用户（server 行属于 PERM_UID）
    sid = _make_server(user_id=PERM_UID, name=TEST_PREFIX + "srv")
    _reg_mcp_tool("mcp.srv.read_file", desc="读取文件", server_id=sid)
    _reg_mcp_tool("mcp.srv.write_file", risk=RISK_HIGH, desc="写文件", server_id=sid)

    decls = _run(_build_mcp_tool_declarations(PERM_UID))
    names = {d["name"] for d in decls}
    assert "mcp.srv.read_file" in names
    assert "mcp.srv.write_file" in names
    assert all(decl["parameters"] == {"type": "object"} for decl in decls)

    text = _format_mcp_declarations(decls)
    assert "mcp.srv.read_file" in text
    assert "description" in text
    assert "[mcp.<server>.<tool>]" in text


def test_mcp_declarations_empty():
    from app.agent.context_builder import _build_mcp_tool_declarations, _format_mcp_declarations

    assert _format_mcp_declarations([]) == ""
    assert _run(_build_mcp_tool_declarations(PERM_UID)) == []


def test_mcp_declarations_excludes_forbid(monkeypatch):
    from app.agent.context_builder import _build_mcp_tool_declarations

    async def fake_mode(uid, scope, risk="medium"):
        return "forbid" if scope == "mcp_forbidden" else "allow"

    monkeypatch.setattr("app.services.permission_service.check_mcp_mode", fake_mode)
    sid_keep = _make_server(user_id=PERM_UID, name=TEST_PREFIX + "keep")
    sid_forbid = _make_server(user_id=PERM_UID, name=TEST_PREFIX + "forbidden")
    _reg_mcp_tool("mcp.srv.keep", scope="mcp_keep", server_id=sid_keep)
    _reg_mcp_tool("mcp.forbidden.drop", scope="mcp_forbidden", server_id=sid_forbid)

    decls = _run(_build_mcp_tool_declarations(PERM_UID))
    names = {d["name"] for d in decls}
    assert "mcp.srv.keep" in names
    assert "mcp.forbidden.drop" not in names


def test_mcp_declarations_excludes_disabled():
    from app.agent.context_builder import _build_mcp_tool_declarations

    sid = _make_server(user_id=PERM_UID, name=TEST_PREFIX + "srv2")
    _reg_mcp_tool("mcp.srv.on", enabled=True, scope="mcp_srv", server_id=sid)
    _reg_mcp_tool("mcp.srv.off", enabled=False, scope="mcp_srv", server_id=sid)

    decls = _run(_build_mcp_tool_declarations(PERM_UID))
    names = {d["name"] for d in decls}
    assert "mcp.srv.on" in names
    assert "mcp.srv.off" not in names


# ------------------------------------------------------------------ P1 多用户隔离（2026-08-29）

def test_mcp_declarations_isolated_by_owner():
    """P1：用户 A 配置的 server 工具不应注入到用户 B 的上下文（归属过滤）。"""
    from app.agent.context_builder import _build_mcp_tool_declarations

    user_a, user_b = 9201, 9202
    sid_a = _make_server(user_id=user_a, name=TEST_PREFIX + "iso")
    _reg_mcp_tool("mcp.iso.read_file", scope="mcp_iso", server_id=sid_a)

    # A 拥有该 server → 声明含其工具
    decls_a = _run(_build_mcp_tool_declarations(user_a))
    assert {d["name"] for d in decls_a} == {"mcp.iso.read_file"}
    # B 不拥有该 server → 声明不含 A 的工具
    decls_b = _run(_build_mcp_tool_declarations(user_b))
    assert "mcp.iso.read_file" not in {d["name"] for d in decls_b}


def test_check_tool_permission_forbid_foreign_mcp():
    """P1：用户 B 调用用户 A 的 MCP 工具应被 forbid（tool_runner 防御纵深）。"""
    from app.agent.tool_runner import check_tool_permission

    user_a, user_b = 9203, 9204
    sid_a = _make_server(user_id=user_a, name=TEST_PREFIX + "forb")
    spec = ToolSpec(name="mcp.forb.read_file", description="d", risk_level=RISK_LOW, scope="mcp_forb", server_id=sid_a)

    async def _flow():
        # B 调用 A 的工具 → 归属校验失败 → forbid
        return await check_tool_permission(spec, user_id=user_b)

    assert _run(_flow()) == "forbid"


# ------------------------------------------------------------------ P2-A 流式不注入声明（2026-08-29）

def test_mcp_declarations_stream_empty():
    """P2-A：流式模式不注入 MCP 工具声明（流式路径不执行工具），非流式保持注入。"""
    from app.agent.context_builder import _build_mcp_tool_declarations

    sid = _make_server(user_id=PERM_UID, name=TEST_PREFIX + "st")
    _reg_mcp_tool("mcp.st.read_file", scope="mcp_st", server_id=sid)

    assert _run(_build_mcp_tool_declarations(PERM_UID, stream=True)) == []
    decls = _run(_build_mcp_tool_declarations(PERM_UID, stream=False))
    assert "mcp.st.read_file" in {d["name"] for d in decls}


# ------------------------------------------------------------------ P2-B observation 截断上限（2026-08-29）

def test_make_observation_respects_max_observation_chars():
    """P2-B：默认截断 120；MCP 工具按 max_observation_chars=4000 放宽。"""
    from app.agent.tool_runner import _make_observation
    from app.agent.tools import ToolSpec

    long_text = "x" * 500
    default_spec = ToolSpec(name="t", description="d")
    obs_default = _make_observation(default_spec, {"text": long_text}, "ok")
    assert len(obs_default["summary"]) == 120

    mcp_spec = ToolSpec(name="mcp.srv.r", description="d", max_observation_chars=4000)
    obs_mcp = _make_observation(mcp_spec, {"text": long_text}, "ok")
    assert len(obs_mcp["summary"]) == 500


def test_mcp_tool_to_spec_sets_max_observation_chars():
    """P2-B：MCP 工具适配器默认给 4000 字符观察上限。"""
    from app.mcp.tool_adapter import mcp_tool_to_spec

    spec = mcp_tool_to_spec("srv", {"name": "read_file", "description": "d"}, 99)
    assert spec.max_observation_chars == 4000


# ------------------------------------------------------------------ actions 标记 + 执行

def test_parse_mcp_actions():
    from app.agent.actions import parse_mcp_actions, parse_actions, strip_actions

    text = "要用工具了 [mcp.srv.echo]{\"text\":\"hi\"}[/mcp.srv.echo] 完了"
    acts = parse_mcp_actions(text)
    assert len(acts) == 1
    assert acts[0].action_type == "mcp.srv.echo"
    assert acts[0].payload == {"text": "hi"}

    # parse_actions 也收集 mcp 动作
    assert any(a.action_type == "mcp.srv.echo" for a in parse_actions(text))
    # strip_actions 剥离 mcp 标记，不泄漏到正文
    assert "mcp." not in strip_actions(text)


def test_run_mcp_tool_stage():
    from app.agent.mcp_tools import run_mcp_tool_stage

    async def _exec(payload):
        return {"ok": True, "text": "echo: " + str(payload.get("text", ""))}

    # P1：工具需归属当前用户（server 行属于 PERM_UID）才可执行
    sid = _make_server(user_id=PERM_UID, name=TEST_PREFIX + "exe")
    spec = ToolSpec(
        name="mcp.srv.echo", description="echo", risk_level=RISK_LOW,
        scope="mcp_srv", execute=_exec, server_id=sid,
    )
    register_tool(spec)
    state = {
        "ai_response": "调用一下 [mcp.srv.echo]{\"text\":\"hi\"}[/mcp.srv.echo]",
        "context_messages": [],
    }
    steps = []
    executed = _run(run_mcp_tool_stage(state, steps, user_id=PERM_UID, character_id=1, session_id=1))
    assert executed is True
    assert steps and steps[0]["ok"] is True
    assert "【工具结果】MCP 工具 mcp.srv.echo" in state["context_messages"][-1]["content"]


def test_run_mcp_tool_stage_noop_without_mcp():
    from app.agent.mcp_tools import run_mcp_tool_stage

    state = {"ai_response": "普通回复，无工具", "context_messages": []}
    steps = []
    executed = _run(run_mcp_tool_stage(state, steps, user_id=PERM_UID, character_id=1, session_id=1))
    assert executed is False
    assert steps == []


def test_run_stream_mcp_tool_stage_from_raw_response():
    """A1（#59）流式路径 MCP 工具循环：从 raw_response（未剥离标记）解析并执行 mcp.* 标记，
    返回 (executed, results) 结果详情供流尾 tool_result 事件推送。"""
    from app.agent.mcp_tools import run_stream_mcp_tool_stage

    async def _exec(payload):
        return {"ok": True, "text": "echo: " + str(payload.get("text", ""))}

    sid = _make_server(user_id=PERM_UID, name=TEST_PREFIX + "stm")
    spec = ToolSpec(
        name="mcp.srv.echo2", description="echo", risk_level=RISK_LOW,
        scope="mcp_srv", execute=_exec, server_id=sid,
    )
    register_tool(spec)
    state = {
        # 流式路径：ai_response 已剥离标记，raw_response 仍含全部原始标记
        "ai_response": "查一下",
        "raw_response": "查一下 [mcp.srv.echo2]{\"text\":\"hi\"}[/mcp.srv.echo2]",
        "context_messages": [],
    }
    steps = []
    executed, results = _run(run_stream_mcp_tool_stage(state, steps, user_id=PERM_UID, character_id=1, session_id=1))
    assert executed is True
    assert len(results) == 1
    assert results[0]["tool"] == "mcp.srv.echo2"
    assert results[0]["ok"] is True
    assert results[0]["summary"] == "echo: hi"
    assert steps and steps[0]["ok"] is True
    assert "【工具结果】MCP 工具 mcp.srv.echo2" in state["context_messages"][-1]["content"]


def test_run_stream_mcp_tool_stage_ignores_stripped_ai_response():
    """流式路径已剥离标记的 ai_response 不应误触发工具（标记只在 raw_response）。"""
    from app.agent.mcp_tools import run_stream_mcp_tool_stage

    state = {"ai_response": "查一下 [mcp.srv.x]{\"a\":1}[/mcp.srv.x]", "raw_response": "", "context_messages": []}
    steps = []
    executed, results = _run(run_stream_mcp_tool_stage(state, steps, user_id=PERM_UID, character_id=1, session_id=1))
    assert executed is False
    assert results == []
    assert steps == []


# ------------------------------------------------------------------ 状态 Event Bus

def test_status_event_published():
    sid = _make_server(name=TEST_PREFIX + "evt")

    async def _flow():
        fut = asyncio.get_running_loop().create_future()

        def handler(payload):
            if not fut.done():
                fut.set_result(payload)

        event_bus.subscribe(EventType.MCP_SERVER_STATUS.value, handler)
        try:
            await mcp_manager._update_db_status(sid, "connected", None)
            return await asyncio.wait_for(fut, timeout=2.0)
        finally:
            event_bus.unsubscribe(EventType.MCP_SERVER_STATUS.value, handler)

    payload = _run(_flow())
    assert payload["server_id"] == sid
    assert payload["status"] == "connected"
    assert payload["server_name"] == TEST_PREFIX + "evt"
    assert payload["ok"] is True


# ------------------------------------------------------------------ API：传输 / url 校验

def _make_client(user_id=ADMIN):
    from app.api.mcp import router as mcp_router

    app = FastAPI()
    app.include_router(mcp_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def test_api_create_sse_requires_url(monkeypatch):
    async def _fake(uid):
        return True

    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)
    client = _make_client(ADMIN)
    r = client.post("/api/v1/mcp/servers", json={"name": TEST_PREFIX + "sse", "transport": "sse"})
    assert r.status_code == 400
    r = client.post("/api/v1/mcp/servers", json={"name": TEST_PREFIX + "sh", "transport": "streamable_http"})
    assert r.status_code == 400
    # 不支持传输 → 400
    r = client.post("/api/v1/mcp/servers", json={"name": "x", "transport": "grpc"})
    assert r.status_code == 400


def test_api_create_sse_with_url(monkeypatch):
    from app.mcp import manager as mgr

    def fake_validate(url):
        return None

    async def _fake(uid):
        return True

    monkeypatch.setattr("app.services.permission_service.is_admin_user", _fake)
    monkeypatch.setattr(mgr, "validate_mcp_url", fake_validate)
    client = _make_client(ADMIN)
    r = client.post("/api/v1/mcp/servers", json={
        "name": TEST_PREFIX + "sseok", "transport": "sse",
        "url": "https://mcp.example.com/sse", "headers": {"Authorization": "Bearer x"},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transport"] == "sse"
    assert body["url"] == "https://mcp.example.com/sse"
    assert body["headers"] == {"Authorization": "Bearer x"}
