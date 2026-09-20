# -*- coding: utf-8 -*-
"""P3-4（2026-09-19）MCP 本地回环细粒度放行测试。

安全契约（对应任务 6 条 + 防退化项）：
1. loopback（127.0.0.1 / ::1 / localhost）+ 该 Server 显式标记 allow_loopback=True → 放行；
2. loopback + 未标记（默认 False）→ 拒；
3. 非 loopback 私网（192.168.1.1 / 10.0.0.5 / 172.20.1.1 / 169.254.169.254）+ 标记=True → 仍拒；
4. 公网 IP → 照旧放行（与标记无关）；
5. 域名解析到私网/元数据 → 拒（标记了也拒）；解析到 loopback 时只有标记才放行；
6. mcp_http_allow_private=True 的旧全局行为不变（且不 pin IP，返回 ""）；
7. 混合解析（loopback + 公网/私网）→ 拒（要求【全部】解析结果都是 loopback）；
8. loopback 判定走 ipaddress 语义（127.0.0.0/8），非字符串前缀；
9. 服务层/接口串联：_build_config 读行内标记、_open_transport 透传给 pin-IP 客户端、
   API 创建/更新写入并回显 allow_loopback（保存校验与连接校验同口径）。

全部 DNS 走 monkeypatch 打桩（离线、确定性），不触网、不连真实 MCP。
"""
import asyncio
import socket

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select
from starlette.testclient import TestClient

from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.models.mcp import MCPServer

PREFIX = "mcp_p34_"
TEST_UID = 9301

LOOPBACK_URLS = ("http://127.0.0.1:8899/sse", "http://localhost:8899/sse", "http://[::1]:8899/sse")
PRIVATE_URLS = (
    "http://192.168.1.1:8899/sse",
    "http://10.0.0.5:8899/sse",
    "http://172.20.1.1:8899/sse",
    "http://169.254.169.254/latest/meta-data",
)


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ DNS 打桩

def _patch_dns(monkeypatch, mapping):
    """把 getaddrinfo 打桩成确定性映射：host → [ip, ...]（未收录 host 视作其本身是 IP）。"""
    def _fake(host, port, *args, **kwargs):
        ips = mapping.get(host, [host])
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    monkeypatch.setattr("app.mcp.transport.socket.getaddrinfo", _fake)


# ------------------------------------------------------------------ 夹具/清理

async def _cleanup():
    from app.models.user import User
    async with async_session_factory() as db:
        await db.execute(delete(MCPServer).where(MCPServer.name.like(PREFIX + "%")))
        await db.execute(delete(User).where(User.id == TEST_UID))
        await db.commit()


async def _ensure_user():
    from app.models.user import User
    async with async_session_factory() as db:
        if await db.get(User, TEST_UID) is None:
            db.add(User(id=TEST_UID, username="mcp_p34_user", nickname="MCP P3-4 测试"))
            await db.commit()


@pytest.fixture(autouse=True)
def _isolate():
    _run(_cleanup())
    _run(_ensure_user())
    yield
    _run(_cleanup())


def _make_server(name, *, url=None, allow_loopback=False, transport="sse"):
    async def _do():
        async with async_session_factory() as db:
            row = MCPServer(
                user_id=TEST_UID, name=name, transport=transport, command=None,
                args_json="[]", env_json="{}", url=url, headers_json="{}",
                enabled=True, auto_connect=False, allow_loopback=allow_loopback,
                tools_cache_json="[]", status="disconnected",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id
    return _run(_do())


async def _load_row(sid):
    async with async_session_factory() as db:
        return (await db.execute(select(MCPServer).where(MCPServer.id == sid))).scalar_one()


# ------------------------------------------------------------------ 1/2：loopback ± 标记

def test_loopback_allowed_when_marked(monkeypatch):
    """① loopback + allow_loopback=True → 放行（并返回可 pin 的 loopback IP）。"""
    _patch_dns(monkeypatch, {"localhost": ["127.0.0.1", "::1"]})
    from app.mcp.transport import _resolve_mcp_ip, validate_mcp_url

    for url in LOOPBACK_URLS:
        assert validate_mcp_url(url, allow_loopback=True) is None, url
    assert _resolve_mcp_ip("http://127.0.0.1:8899/sse", allow_loopback=True) == "127.0.0.1"
    assert _resolve_mcp_ip("http://[::1]:8899/sse", allow_loopback=True) == "::1"


def test_loopback_rejected_without_mark(monkeypatch):
    """② loopback + 未标记（默认 False）→ 拒。"""
    _patch_dns(monkeypatch, {"localhost": ["127.0.0.1", "::1"]})
    from app.mcp.transport import _resolve_mcp_ip, validate_mcp_url

    for url in LOOPBACK_URLS:
        with pytest.raises(ValueError):
            validate_mcp_url(url)
        with pytest.raises(ValueError):
            validate_mcp_url(url, allow_loopback=False)
        with pytest.raises(ValueError):
            _resolve_mcp_ip(url)


# ------------------------------------------------------------------ 3：其余私网标记也不放行

def test_non_loopback_private_rejected_even_marked(monkeypatch):
    """③ 192.168/10./172.16-31/169.254 元数据 + allow_loopback=True → 仍拒。"""
    _patch_dns(monkeypatch, {})
    from app.mcp.transport import _resolve_mcp_ip, validate_mcp_url

    for url in PRIVATE_URLS:
        with pytest.raises(ValueError):
            validate_mcp_url(url)
        with pytest.raises(ValueError):
            validate_mcp_url(url, allow_loopback=True)
        with pytest.raises(ValueError):
            _resolve_mcp_ip(url, allow_loopback=True)


# ------------------------------------------------------------------ 4：公网照旧

def test_public_ip_still_allowed(monkeypatch):
    """④ 公网 IP → 照旧放行（与标记无关）。"""
    _patch_dns(monkeypatch, {})
    from app.mcp.transport import _resolve_mcp_ip, validate_mcp_url

    for url, ip in (("http://93.184.216.34:8899/sse", "93.184.216.34"), ("https://8.8.8.8/sse", "8.8.8.8")):
        assert validate_mcp_url(url) is None
        assert validate_mcp_url(url, allow_loopback=True) is None
        assert _resolve_mcp_ip(url) == ip


# ------------------------------------------------------------------ 5：域名解析规则

def test_domain_resolution_rules(monkeypatch):
    """⑤ 域名解析到私网/元数据拒；解析到 loopback 需标记；解析到公网照旧放行。"""
    _patch_dns(monkeypatch, {
        "mcp.private.test": ["192.168.1.7"],
        "mcp.metadata.test": ["169.254.169.254"],
        "mcp.loopback.test": ["127.0.0.1"],
        "mcp.public.test": ["93.184.216.34"],
    })
    from app.mcp.transport import validate_mcp_url

    for host in ("mcp.private.test", "mcp.metadata.test"):
        with pytest.raises(ValueError):
            validate_mcp_url(f"http://{host}/sse")
        with pytest.raises(ValueError):
            validate_mcp_url(f"http://{host}/sse", allow_loopback=True)

    with pytest.raises(ValueError):
        validate_mcp_url("http://mcp.loopback.test/sse")
    assert validate_mcp_url("http://mcp.loopback.test/sse", allow_loopback=True) is None

    assert validate_mcp_url("http://mcp.public.test/sse") is None


# ------------------------------------------------------------------ 6：全局开关旧行为

def test_global_allow_private_flag_unchanged(monkeypatch):
    """⑥ mcp_http_allow_private=True 全局放行不变（loopback/私网/元数据全放，且不 pin IP）。"""
    from app.config import settings
    from app.mcp.transport import _resolve_mcp_ip, validate_mcp_url

    monkeypatch.setattr(settings, "mcp_http_allow_private", True)
    for url in LOOPBACK_URLS + PRIVATE_URLS:
        assert validate_mcp_url(url) is None
        assert validate_mcp_url(url, allow_loopback=False) is None
        assert validate_mcp_url(url, allow_loopback=True) is None
    # 全局放行时直接返回 ""（不绑定），连接层保持既有普通 client 行为
    assert _resolve_mcp_ip("http://192.168.1.1:8899/sse") == ""


# ------------------------------------------------------------------ 7：混合解析 / 防 rebinding 退化

def test_mixed_resolution_rejected(monkeypatch):
    """⑦ 只要有一个解析结果不是 loopback，就不得按 loopback 放行（DNS rebinding 防退化）。"""
    _patch_dns(monkeypatch, {
        "mixed.public.test": ["127.0.0.1", "93.184.216.34"],
        "mixed.private.test": ["127.0.0.1", "192.168.1.1"],
    })
    from app.mcp.transport import validate_mcp_url

    for host in ("mixed.public.test", "mixed.private.test"):
        with pytest.raises(ValueError):
            validate_mcp_url(f"http://{host}/sse", allow_loopback=True)


def test_loopback_detection_is_not_prefix_based():
    """⑧ loopback 判定用 ipaddress 语义（127.0.0.0/8），不是字符串前缀。"""
    from app.mcp.transport import _is_loopback_ip

    assert _is_loopback_ip("127.0.0.1") is True
    assert _is_loopback_ip("127.0.0.2") is True
    assert _is_loopback_ip("::1") is True
    assert _is_loopback_ip("127.0.0.1.evil.com") is False
    assert _is_loopback_ip("1127.0.0.1") is False
    assert _is_loopback_ip("8.8.8.8") is False
    assert _is_loopback_ip("192.168.1.1") is False
    assert _is_loopback_ip("169.254.169.254") is False


# ------------------------------------------------------------------ 9：服务层/接口串联

def test_build_config_threads_allow_loopback(monkeypatch):
    """_build_config 从 DB 行读 allow_loopback：标记放行、未标记拦截。"""
    _patch_dns(monkeypatch, {"localhost": ["127.0.0.1"]})
    from app.mcp.manager import MCPClientManager

    marked = _make_server(PREFIX + "marked", url="http://127.0.0.1:8899/sse", allow_loopback=True)
    cfg = MCPClientManager()._build_config(_run(_load_row(marked)))
    assert cfg.transport == "sse"
    assert cfg.url == "http://127.0.0.1:8899/sse"
    assert cfg.allow_loopback is True

    plain = _make_server(PREFIX + "plain", url="http://127.0.0.1:8899/sse", allow_loopback=False)
    with pytest.raises(ValueError):
        MCPClientManager()._build_config(_run(_load_row(plain)))


def test_open_transport_passes_allow_loopback(monkeypatch):
    """_open_transport 把 cfg.allow_loopback 透传给 pin-IP 客户端构造。"""
    from app.mcp import manager as mgr

    captured = {}

    class _CM:
        async def __aenter__(self):
            return ("r", "w")

        async def __aexit__(self, *a):
            return None

    def fake_pinned(url, headers=None, timeout=None, auth=None, allow_loopback=False):
        captured["url"] = url
        captured["allow_loopback"] = allow_loopback
        return object()

    monkeypatch.setattr(mgr, "_build_pinned_http_client", fake_pinned)
    monkeypatch.setattr(
        "mcp.client.streamable_http.streamable_http_client",
        lambda url, http_client=None: _CM(),
    )
    cfg = mgr._TransportConfig(
        transport="streamable_http", url="http://127.0.0.1:8899/mcp", allow_loopback=True,
    )
    mgr.MCPClientManager()._open_transport(cfg)
    assert captured["url"] == "http://127.0.0.1:8899/mcp"
    assert captured["allow_loopback"] is True


def _make_client(user_id=TEST_UID):
    from app.api.mcp import router as mcp_router

    app = FastAPI()
    app.include_router(mcp_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _fake_admin_true(monkeypatch):
    async def _fake(uid):
        return True
    monkeypatch.setattr("app.application.permission_service.is_admin_user", _fake)


def test_api_create_requires_and_exposes_allow_loopback(monkeypatch):
    """API：未标记的 loopback 创建被 400 拦截；标记后创建成功并回显；私网标记也 400；更新同口径。"""
    _fake_admin_true(monkeypatch)
    _patch_dns(monkeypatch, {"localhost": ["127.0.0.1"]})
    client = _make_client()

    body = {
        "name": PREFIX + "api", "transport": "sse", "url": "http://127.0.0.1:8899/sse",
        "auto_connect": False,
    }
    r = client.post("/api/v1/mcp/servers", json=body)
    assert r.status_code == 400, r.text  # 未标记 → loopback 被 SSRF 拦截

    r = client.post("/api/v1/mcp/servers", json={**body, "allow_loopback": True})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["allow_loopback"] is True
    sid = data["id"]
    items = client.get("/api/v1/mcp/servers").json()["items"]
    assert [it["allow_loopback"] for it in items if it["id"] == sid] == [True]

    # 私网/元数据即便标记也拒（169.254.169.254）
    r = client.post("/api/v1/mcp/servers", json={
        "name": PREFIX + "meta", "transport": "sse",
        "url": "http://169.254.169.254/latest", "allow_loopback": True,
    })
    assert r.status_code == 400, r.text

    # 更新：关闭标记 + loopback 地址 → 400（保存校验与连接校验同口径）
    r = client.put(f"/api/v1/mcp/servers/{sid}", json={"allow_loopback": False})
    assert r.status_code == 400, r.text
    # 保持标记 → 200，且 auto_connect=False 不触发实际重连
    r = client.put(f"/api/v1/mcp/servers/{sid}", json={"allow_loopback": True})
    assert r.status_code == 200, r.text
    assert r.json()["allow_loopback"] is True
