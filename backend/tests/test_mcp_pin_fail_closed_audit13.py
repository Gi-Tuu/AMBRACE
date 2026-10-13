# -*- coding: utf-8 -*-
"""audit13 P3-1（2026-10-13）：MCP 连接期 pin-IP 失败改 fail-closed 的回归测试。

安全契约（对应 transport.py::_build_pinned_http_client）：
1. 默认安全档：DNS 解析异常 / 解析结果为空 / host 缺失 / scheme 不支持 / 命中私网·链路本地·
   云元数据 → 一律抛 ValueError 拒绝连接，**绝不**返回不钉 IP 的普通 client；
2. 钉 IP 传输或 client 构造失败 → 同样拒绝（ValueError），不降级普通 client；
3. settings.mcp_http_allow_private=True 是唯一的显式授权例外，只有它走原 plain client；
   「解析结果为空 IP」不再被当成例外；
4. allow_loopback=True 仍只放「全部解析结果都是 loopback」，且照样钉 IP、保持证书校验（SNI/
   Host/证书仍按原域名，无任意私网放开）；headers/timeout/auth 透传口径不变；
5. 实际挂点：MCPClientManager._open_transport（streamable_http 直接构造 client、sse 由
   mcp SDK 回调 httpx_client_factory）两条路径同样 fail-closed。

全部 DNS/网络打桩（离线、确定性），不触网、不连真实 MCP Server、不调用 LLM。
"""
import socket

import httpx
import pytest

from app.config import settings
from app.mcp import transport as tp


# ------------------------------------------------------------------ DNS 打桩

def _patch_dns(monkeypatch, mapping):
    """host → [ip, ...]（未收录 host 视作其本身是 IP），与 test_mcp_loopback_p34 同口径。"""
    def _fake(host, port, *args, **kwargs):
        ips = mapping.get(host, [host])
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    monkeypatch.setattr("app.mcp.transport.socket.getaddrinfo", _fake)


def _patch_dns_broken(monkeypatch):
    """模拟 DNS 临时失败：getaddrinfo 抛 gaierror（原代码在这里被 except 吞掉后走 plain client）。"""
    def _boom(host, port, *args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr("app.mcp.transport.socket.getaddrinfo", _boom)


def _patch_dns_empty(monkeypatch):
    """模拟解析成功但结果列表为空。"""
    monkeypatch.setattr(
        "app.mcp.transport.socket.getaddrinfo", lambda host, port, *a, **k: [])


def _plain_client(client):
    """判定是否「不钉 IP 的普通 client」（回退路径的产物，安全档下不该出现）。"""
    return not isinstance(getattr(client, "_transport", None), tp._PinnedIPHTTPTransport)


def _close(client):
    import asyncio
    asyncio.run(client.aclose())


# ------------------------------------------------------------------ 1：解析/校验异常 fail-closed

def test_dns_failure_rejected_not_plain_client(monkeypatch):
    """① DNS 异常：默认档拒绝连接（ValueError），不再吞掉后构造 plain client。"""
    _patch_dns_broken(monkeypatch)
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)

    with pytest.raises(ValueError):
        tp._build_pinned_http_client("http://mcp.flaky-dns.test:8899/mcp")


def test_empty_resolution_rejected(monkeypatch):
    """① 解析结果为空：同样 fail-closed。"""
    _patch_dns_empty(monkeypatch)
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)

    with pytest.raises(ValueError):
        tp._build_pinned_http_client("http://mcp.empty-dns.test:8899/mcp")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8899/mcp",          # loopback 未标记
    "http://192.168.1.1:8899/mcp",        # 私网
    "http://10.0.0.5:8899/mcp",           # 私网
    "http://169.254.169.254/mcp",         # 云元数据
    "http://",                            # host 缺失
    "ftp://example.com/mcp",              # scheme 不支持
])
def test_ssrf_and_malformed_rejected(monkeypatch, url):
    """① 命中私网/元数据/loopback 未标记/host 缺失/scheme 不支持：一律 ValueError。"""
    _patch_dns(monkeypatch, {})
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)

    with pytest.raises(ValueError):
        tp._build_pinned_http_client(url)


def test_mixed_resolution_rejected_after_mark(monkeypatch):
    """① 混合解析（loopback+公网）即便标了 allow_loopback 也拒，且不落 plain client。"""
    _patch_dns(monkeypatch, {"mixed.public.test": ["127.0.0.1", "93.184.216.34"]})
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)

    with pytest.raises(ValueError):
        tp._build_pinned_http_client(
            "http://mixed.public.test:8899/mcp", allow_loopback=True)


# ------------------------------------------------------------------ 2：钉 IP 构造失败也拒绝

def test_pin_transport_construction_failure_rejected(monkeypatch):
    """② 钉 IP 传输构造失败 → 拒绝连接（原代码此处 warning 后回退 plain client）。"""
    _patch_dns(monkeypatch, {"mcp.public.test": ["93.184.216.34"]})
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)

    class _Boom:
        def __init__(self, pinned_ip):
            raise RuntimeError("httpcore unavailable")

    monkeypatch.setattr(tp, "_PinnedIPHTTPTransport", _Boom)
    with pytest.raises(ValueError):
        tp._build_pinned_http_client("http://mcp.public.test:8899/mcp")


def test_empty_pin_ip_is_not_an_exception(monkeypatch):
    """③ 空 IP 不再被当成例外：显式打桩成返回 ""（非 allow_private），也必须拒绝。"""
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)
    monkeypatch.setattr(tp, "_resolve_mcp_ip", lambda url, allow_loopback=False: "")

    with pytest.raises(ValueError):
        tp._build_pinned_http_client("http://mcp.public.test:8899/mcp")


# ------------------------------------------------------------------ 3：唯一显式全局例外

def test_allow_private_true_is_the_only_plain_branch(monkeypatch):
    """③ mcp_http_allow_private=True：走原 plain client（不绑定），headers 口径不变。"""
    _patch_dns_broken(monkeypatch)  # 该分支不该依赖解析
    monkeypatch.setattr(settings, "mcp_http_allow_private", True, raising=False)

    client = tp._build_pinned_http_client(
        "http://mcp.internal.test:8899/mcp", headers={"Authorization": "Bearer x"})
    try:
        assert _plain_client(client)
        assert client.headers["Authorization"] == "Bearer x"
    finally:
        _close(client)


def test_allow_private_false_never_plain_for_public_host(monkeypatch):
    """③ 反向确认：同一 host 关掉全局开关后必须是钉 IP 的 client（不是 plain）。"""
    _patch_dns(monkeypatch, {"mcp.internal.test": ["93.184.216.34"]})
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)

    client = tp._build_pinned_http_client("http://mcp.internal.test:8899/mcp")
    try:
        assert isinstance(client._transport, tp._PinnedIPHTTPTransport)
        assert client._transport._pool._network_backend._pinned_ip == "93.184.216.34"
    finally:
        _close(client)


# ------------------------------------------------------------------ 4：loopback 放行口径不变（仍 pin＋证书校验）

def test_loopback_marked_still_pinned_with_cert_validation(monkeypatch):
    """④ allow_loopback=True 只放全 loopback：照样钉 IP、SSL 仍 CERT_REQUIRED（SNI/Host/证书不改）。"""
    import ssl

    _patch_dns(monkeypatch, {"mcp.loopback.test": ["127.0.0.1"]})
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)

    client = tp._build_pinned_http_client(
        "http://mcp.loopback.test:8899/sse", headers={"X-T": "1"},
        timeout=httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0), allow_loopback=True)
    try:
        tr = client._transport
        assert isinstance(tr, tp._PinnedIPHTTPTransport)
        assert tr._pool._network_backend._pinned_ip == "127.0.0.1"
        assert tr._pool._ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert client.headers["X-T"] == "1"
        assert client.timeout.connect == 5.0
    finally:
        _close(client)


def test_partial_loopback_resolution_not_allowed(monkeypatch):
    """④ 解析结果一半 loopback：不放行（fail-closed），不落 plain client。"""
    _patch_dns(monkeypatch, {"half.loopback.test": ["127.0.0.1", "192.168.1.7"]})
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)

    with pytest.raises(ValueError):
        tp._build_pinned_http_client(
            "http://half.loopback.test:8899/sse", allow_loopback=True)


# ------------------------------------------------------------------ 5：实际挂点（manager._open_transport）

def _capture_streamable(monkeypatch):
    """打桩 mcp SDK 的 streamable_http_client，只观察是否被调用（不发起连接）。"""
    called = {}

    class _CM:
        async def __aenter__(self):
            return ("r", "w")

        async def __aexit__(self, *a):
            return None

    def _fake(url, http_client=None):
        called["url"] = url
        called["client"] = http_client
        return _CM()

    monkeypatch.setattr("mcp.client.streamable_http.streamable_http_client", _fake)
    return called


def test_open_transport_streamable_failclosed(monkeypatch):
    """⑤ 挂点：streamable_http 连接期解析失败 → _open_transport 抛错，不会带着 plain client 进 SDK。"""
    from app.mcp.manager import MCPClientManager

    _patch_dns_broken(monkeypatch)
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)
    called = _capture_streamable(monkeypatch)

    cfg = tp._TransportConfig(transport="streamable_http", name="t",
                              url="http://mcp.flaky-dns.test:8899/mcp")
    with pytest.raises(ValueError):
        MCPClientManager()._open_transport(cfg)
    assert called == {}  # 没走到 SDK


def test_open_transport_streamable_pins_public_host(monkeypatch):
    """⑤ 挂点正向：解析成功仍走钉 IP client 进 SDK。"""
    from app.mcp.manager import MCPClientManager

    _patch_dns(monkeypatch, {"mcp.public.test": ["93.184.216.34"]})
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)
    called = _capture_streamable(monkeypatch)

    cfg = tp._TransportConfig(transport="streamable_http", name="t",
                              url="http://mcp.public.test:8899/mcp", headers={"X-A": "b"})
    cm = MCPClientManager()._open_transport(cfg)
    client = called["client"]
    try:
        assert isinstance(client._transport, tp._PinnedIPHTTPTransport)
        assert client._transport._pool._network_backend._pinned_ip == "93.184.216.34"
        assert client.headers["X-A"] == "b"
        assert cm is not None
    finally:
        _close(client)


def test_open_transport_sse_factory_failclosed(monkeypatch):
    """⑤ 挂点：SSE 的 httpx_client_factory（mcp SDK 在连接时回调）同样 fail-closed。"""
    from app.mcp.manager import MCPClientManager

    _patch_dns_broken(monkeypatch)
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)

    captured = {}

    def _fake_sse(url, headers=None, httpx_client_factory=None):
        captured["factory"] = httpx_client_factory
        return object()

    monkeypatch.setattr("mcp.client.sse.sse_client", _fake_sse)
    cfg = tp._TransportConfig(transport="sse", name="t", url="http://mcp.flaky-dns.test:8899/sse")
    MCPClientManager()._open_transport(cfg)

    factory = captured["factory"]
    assert factory is not None
    with pytest.raises(ValueError):
        factory(headers={"X-T": "1"}, timeout=None, auth=None)


def test_open_transport_sse_factory_pins_when_resolvable(monkeypatch):
    """⑤ 挂点正向：SSE 工厂在解析成功时返回钉 IP client，并保留 mcp 传入的 headers。"""
    from app.mcp.manager import MCPClientManager

    _patch_dns(monkeypatch, {"mcp.public.test": ["93.184.216.34"]})
    monkeypatch.setattr(settings, "mcp_http_allow_private", False, raising=False)

    captured = {}

    def _fake_sse(url, headers=None, httpx_client_factory=None):
        captured["factory"] = httpx_client_factory
        return object()

    monkeypatch.setattr("mcp.client.sse.sse_client", _fake_sse)
    cfg = tp._TransportConfig(transport="sse", name="t", url="http://mcp.public.test:8899/sse",
                              headers={"X-S": "v"})
    MCPClientManager()._open_transport(cfg)

    client = captured["factory"](headers={"X-S": "v"}, timeout=None, auth=None)
    try:
        assert isinstance(client._transport, tp._PinnedIPHTTPTransport)
        assert client.headers["X-S"] == "v"
    finally:
        _close(client)
