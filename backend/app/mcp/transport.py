"""MCP 传输与安全（F1 拆分，2026-08-31）：SSRF 校验/IP 绑定传输/超时配置/传输配置数据类。

自 manager.py 原样搬移；app.mcp.manager 保留同名重导出（兼容面不变）。
"""
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx  # P3-8：连接期绑定已校验 IP 用

from app.config import settings
from app.utils.logger import get_logger

_logger = get_logger("mcp.transport")


# ---- 常量 / 状态（Phase 1-2；连接到 config 可调） ----
# P3-C（2026-08-29）：连接到 settings 可配（.env 的 MCP_CONNECT_TIMEOUT / MCP_CALL_TIMEOUT /
# MCP_RECONNECT_MAX 经 config.py 的 mcp_connect_timeout / mcp_call_timeout / mcp_reconnect_max 生效），
# 保留默认值兜底（config.py 缺失字段时回退到这里的默认值）。运行期一律读函数（反映运行时配置/测试覆盖），
# 模块级常量仅作为默认值兜底与外部兼容引用。


def _connect_timeout() -> float:
    """连接/初始化/发现超时（秒）：settings.mcp_connect_timeout，默认 10.0。"""
    try:
        return float(getattr(settings, "mcp_connect_timeout", 10.0) or 10.0)
    except Exception:
        return 10.0


def _call_timeout() -> float:
    """单次工具调用超时（秒）：settings.mcp_call_timeout，默认 30.0。"""
    try:
        return float(getattr(settings, "mcp_call_timeout", 30.0) or 30.0)
    except Exception:
        return 30.0


def _reconnect_max() -> int:
    """连接失败最大重试次数（指数退避 1s/2s/4s）：settings.mcp_reconnect_max，默认 3。"""
    try:
        return max(1, int(getattr(settings, "mcp_reconnect_max", 3) or 3))
    except Exception:
        return 3


MCP_CONNECT_TIMEOUT = _connect_timeout()  # 默认值兜底（运行期用 _connect_timeout()）
MCP_CALL_TIMEOUT = _call_timeout()  # 默认值兜底（运行期用 _call_timeout()）
MCP_RECONNECT_MAX = _reconnect_max()  # 默认值兜底（运行期用 _reconnect_max()）

STATUS_DISCONNECTED = "disconnected"
STATUS_CONNECTING = "connecting"
STATUS_CONNECTED = "connected"
STATUS_ERROR = "error"

# 支持的传输类型
SUPPORTED_TRANSPORTS = ("stdio", "sse", "streamable_http")


@dataclass
class _TransportConfig:
    """单个 MCP Server 的传输配置（由 _build_config 从 DB 行解析；跨 worker 复用，不含运行态）。"""

    transport: str
    name: str = ""
    # stdio
    command: str = ""
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None
    # sse / streamable_http
    url: str | None = None
    headers: dict[str, str] | None = None


class _ManagedHttpTransport:
    """包装 streamable_http_client（mcp SDK 不接管调用方传入的 httpx client），退出时关闭 client。"""

    def __init__(self, cm, http_client: Any) -> None:
        self._cm = cm
        self._client = http_client

    async def __aenter__(self):
        return await self._cm.__aenter__()

    async def __aexit__(self, *exc):
        try:
            return await self._cm.__aexit__(*exc)
        finally:
            try:
                await self._client.aclose()
            except Exception as e:
                _logger.warning("mcp httpx client close error: %s", e)


def validate_mcp_url(url: str) -> None:
    """SSRF 防护（MCP 专用）：复用 plugin_bridge 的 IP 判定；默认禁用内网/本地地址，可配置放行。

    - 允许 http/https 方案（MCP 本地/远程 Server 可能用 http）；
    - 默认拦截私有/环回/链路本地/组播/保留/未指定/云元数据地址；
    - settings.mcp_http_allow_private=True 时全量放行（自托管内网服务）。
    - 校验失败抛 ValueError（connect/test_connect/API 会捕获并报告）。
    """
    # P3-8（2026-08-25）：复用 _resolve_mcp_ip 同一套「解析 + SSRF 校验」（含 mcp_http_allow_private 放行）。
    _resolve_mcp_ip(url)


def _resolve_mcp_ip(url: str) -> str:
    """解析 URL host 为 IP 并做 SSRF 校验，返回一个允许连接的目标 IP（连接期用于绑定，防 DNS rebinding）。

    - settings.mcp_http_allow_private=True 时直接返回 ""（不绑定、原样全放行，保持既有内网放行逻辑）；
    - 解析失败 / host 缺失 / 任一解析 IP 命中私有/环回/链路本地/组播/保留/云元数据地址 → 抛 ValueError；
    - 返回解析出的可连接 IP：连接层把 TCP 目标绑定到该 IP，即使随后 DNS 被重新绑定到内网地址，
      流量仍打到已校验的公网 IP（缩小 validate_mcp_url 与真正连接之间的 TOCTOU/rebinding 窗口）。
    """
    if getattr(settings, "mcp_http_allow_private", False):
        return ""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported url scheme: {parsed.scheme}")
    host = parsed.hostname
    if not host:
        raise ValueError("url missing host")
    from app.services.plugin_bridge_service import _is_blocked_ip

    try:
        infos = socket.getaddrinfo(
            host, parsed.port or (443 if parsed.scheme == "https" else 80),
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror:
        raise ValueError(f"url host unresolvable: {host}")
    for info in infos:
        if _is_blocked_ip(info[4][0]):
            raise ValueError("ssrf blocked (private/link-local address)")
    return infos[0][4][0] if infos else ""


class _PinnedIPNetworkBackend:
    """把 TCP 连接的目标地址改写为 SSRF 校验时解析出的 IP（仅 IP 层，不改 TLS 证书校验）。

    通过组合包装 httpcore 的 AnyIOBackend（避免在模块顶层依赖 httpcore 私有路径）：连接池仍以
    URL 主机名做 SNI/Host 头/证书校验（https 下证书校验针对原域名），这里只把底层 socket 连到
    的目标地址替换为已校验 IP。DNS rebinding 后即使域名重新解析到内网，连接仍走校验过的公网 IP。
    """

    def __init__(self, pinned_ip: str) -> None:
        from httpcore._backends.anyio import AnyIOBackend
        self._inner = AnyIOBackend()
        self._pinned_ip = pinned_ip

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if self._pinned_ip:
            host = self._pinned_ip
        return await self._inner.connect_tcp(host, port, timeout, local_address, socket_options)

    async def connect_unix_socket(self, *a, **kw):
        return await self._inner.connect_unix_socket(*a, **kw)

    async def sleep(self, delay):
        return await self._inner.sleep(delay)


class _PinnedIPHTTPTransport(httpx.AsyncHTTPTransport):
    """httpx 传输：注入 _PinnedIPNetworkBackend，把 MCP http 连接绑定到已校验 IP。

    仅用于 streamable_http/sse 的 MCP 连接（单 URL 场景）。构造失败由 _build_pinned_http_client 回退普通 client。
    """

    def __init__(self, pinned_ip: str) -> None:
        import httpcore as _hc
        ssl_context = httpx.create_ssl_context(verify=True, trust_env=True)
        self._pool = _hc.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=5.0,
            http1=True,
            http2=False,
            network_backend=_PinnedIPNetworkBackend(pinned_ip),
        )


def _to_httpx_timeout(timeout):
    """把调用方传入的 timeout（可能是 mcp 的 httpx2.Timeout）转为外部 httpx 可接受形式。"""
    if timeout is None:
        return None
    if hasattr(timeout, "connect") and hasattr(timeout, "read"):
        try:
            return httpx.Timeout(connect=timeout.connect, read=timeout.read, write=timeout.write, pool=timeout.pool)
        except Exception:
            return None
    return timeout


def _build_pinned_http_client(url, headers=None, timeout=None, auth=None):
    """构建连接期绑定已校验 IP 的 httpx.AsyncClient（P3-8 防 DNS rebinding）。

    - mcp_http_allow_private=True：返回普通 client（不绑定，保持既有内网放行逻辑）；
    - 解析/校验失败或绑定构造失败：回退普通 client（不阻断 MCP 连接，行为与现状一致）。
    调用方（streamable_http 的 http_client / sse 的 httpx_client_factory）复用该 client。
    """
    base_kw: dict = {}
    if headers is not None:
        base_kw["headers"] = headers
    _t = _to_httpx_timeout(timeout)
    if _t is not None:
        base_kw["timeout"] = _t
    if auth is not None:
        base_kw["auth"] = auth
    pin_ip = ""
    try:
        pin_ip = _resolve_mcp_ip(url)
    except Exception:
        pin_ip = ""
    if not pin_ip:
        return httpx.AsyncClient(**base_kw)
    try:
        return httpx.AsyncClient(transport=_PinnedIPHTTPTransport(pin_ip), **base_kw)
    except Exception:
        return httpx.AsyncClient(**base_kw)
