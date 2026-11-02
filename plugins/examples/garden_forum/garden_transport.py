# -*- coding: utf-8 -*-
"""M3 出站边界：base 规范化、host 绑定、HTTPS 约束、禁止跨站重定向。

只做纯 URL/重定向判断；SDK 的真实发请求入口用它，测试可直接打这一层。
"""
from __future__ import annotations

import ipaddress
import urllib.error
import urllib.parse
import urllib.request

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_loopback(host: str) -> bool:
    h = (host or "").strip("[]").lower()
    if h in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def normalize_base(url: str, *, allow_loopback: bool = True) -> str:
    """校验并归一 base：https；仅 loopback 允许 http；禁止 userinfo/query/fragment。"""
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("base_url 为空")
    p = urllib.parse.urlsplit(raw)
    if p.scheme not in ("http", "https"):
        raise ValueError("base_url 必须是 http(s)")
    if p.username or p.password:
        raise ValueError("base_url 不得含 userinfo")
    if p.query or p.fragment:
        raise ValueError("base_url 不得含 query/fragment")
    if not p.hostname:
        raise ValueError("base_url 缺 host")
    host = p.hostname.lower()
    if p.scheme == "http" and not (allow_loopback and _is_loopback(host)):
        raise ValueError("公开环境必须 HTTPS（仅 loopback 允许 http）")
    port = p.port
    if port is not None and not (0 < port < 65536):
        raise ValueError("端口非法")
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = "[%s]" % host
    if port:
        netloc += ":%d" % port
    path = (p.path or "").rstrip("/")
    return "%s://%s%s" % (p.scheme, netloc, path)


def origin(url: str) -> tuple[str, str, int]:
    p = urllib.parse.urlsplit(str(url or ""))
    scheme = (p.scheme or "").lower()
    host = (p.hostname or "").lower()
    port = p.port or (443 if scheme == "https" else 80)
    return scheme, host, port


def same_origin(a: str, b: str) -> bool:
    return origin(a) == origin(b)


def assert_same_origin(url: str, expected_base: str) -> None:
    if not same_origin(url, expected_base):
        raise ValueError("跨站目标被拒绝: %s" % urllib.parse.urlsplit(url).hostname)


class PinnedRedirect(urllib.request.HTTPRedirectHandler):
    """跟随同源重定向；跨站/降级（https→http）一律拒绝。"""

    def __init__(self, expected_base: str):
        super().__init__()
        self.expected_base = expected_base
        self.expected_origin = origin(expected_base)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if origin(target) != self.expected_origin:
            raise urllib.error.HTTPError(
                target, code, "cross-origin redirect refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, target)


def open_pinned(req: urllib.request.Request, expected_base: str, timeout: float):
    """带同源重定向门禁的 opener；返回 response（调用方负责 close）。"""
    opener = urllib.request.build_opener(PinnedRedirect(expected_base))
    return opener.open(req, timeout=timeout)


def safe_url_for_log(url: str) -> str:
    """日志只留 scheme+host+path，绝不带 query/userinfo。"""
    p = urllib.parse.urlsplit(str(url or ""))
    return "%s://%s%s" % (p.scheme, p.netloc.split("@")[-1], p.path)
