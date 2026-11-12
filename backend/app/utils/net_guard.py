# -*- coding: utf-8 -*-
"""出站 HTTP 目标守卫（2026-11-05 安全复核批）。

背景：Mimosa 扫描指出 TTS 音频下载 URL 来自**远端响应**（`resp.output.audio.url`）：
若配置端点返回 `http://169.254.169.254/...` 这类地址，服务端会替它去取（SSRF）。

口径复用项目既有 SSRF 语义（plugins/MCP 的同一套 ipaddress 判定）：回环 / 私网 /
链路本地 / 保留 / 组播 / 未指定 / 198.18.0.0-15 一律拒绝；需要连内网时用显式开关放行
（``settings.http_fetch_allow_private`` 或调用参数）。

取舍：DNS 解析失败**不报错**（放行）——解析失败的请求本来也拿不到东西，不因瞬时抖动
打断 TTS/天气这类非关键链路；IP 字面量无需 DNS，判定必然生效。
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_NETS = (
    ipaddress.ip_network("198.18.0.0/15"),   # 基准测试保留段（假地址），与 plugins 口径一致
)


def allow_private_by_default() -> bool:
    """进程级显式放行开关（配置为准；读取失败按不放行）。"""
    try:
        from app.config import settings
        return bool(getattr(settings, "http_fetch_allow_private", False))
    except Exception:  # noqa: BLE001
        return False


def is_blocked_ip(raw: str) -> bool:
    """单个 IP 是否属于需拦截的目标段（非合法 IP 一律视为不可判定 ⇒ 拦截）。"""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return True
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast \
            or ip.is_reserved or ip.is_unspecified:
        return True
    return any(ip in net for net in _BLOCKED_NETS)


def ensure_outbound_url(url: str, *, allow_private: bool | None = None) -> None:
    """校验出站 URL；命中内网/云元数据等目标抛 ValueError（调用方按需 catch/降级）。"""
    if allow_private is None:
        allow_private = allow_private_by_default()
    if allow_private:
        return
    parsed = urlparse(str(url or ""))
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("outbound url scheme not allowed: %r" % (parsed.scheme,))
    host = parsed.hostname
    if not host:
        raise ValueError("outbound url has no host")
    try:
        infos = socket.getaddrinfo(
            host, parsed.port or (443 if parsed.scheme == "https" else 80),
            proto=socket.IPPROTO_TCP)
    except OSError:
        return  # DNS 失败：放行（请求本身会失败），不因瞬时抖动打断链路
    for info in infos:
        addr = info[4][0]
        if is_blocked_ip(addr):
            raise ValueError("ssrf blocked host=%s ip=%s" % (host, addr))
