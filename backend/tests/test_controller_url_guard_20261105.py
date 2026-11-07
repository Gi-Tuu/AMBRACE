# -*- coding: utf-8 -*-
"""server_controller 8 项复核（2026-11-05）：`_guard_http_url` 的「放行私网/回环、拒链路本地/元数据」口径。

背景：Mimosa 标记 server_controller 8 项（3 ssrf + 5 path）。复核结论：
- 5 处 path 全为误报/已加固（路径来自模块常量或文件选择框，且已包 `_tool_path`）；
- 3 处 ssrf 属设计（控制台目标就是本机/局域网后端），但原守卫只查协议+host，
  **没挡云元数据**（169.254.0.0/16）——本批补强并钉住。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "server_controller") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "server_controller"))

import console_core as sc  # noqa: E402  W6-A 共享真源（纯标准库，不建 Tk 实例）


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/api/v1/system/health",   # 本机后端（设计目标）
    "http://192.168.1.20:8000/api/v1/system/liveness",  # 局域网后端（远程监控模式）
    "http://10.0.0.5:8000/x",
])
def test_allows_loopback_and_lan_targets(url):
    sc._guard_http_url(url)  # 不抛即通过


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # 云元数据（链路本地）
    "http://[fe80::1]/x",                          # IPv6 链路本地
    "http://0.0.0.0/x",
    "http://198.18.0.9/x",                         # 假地址段
])
def test_rejects_link_local_and_metadata(url):
    with pytest.raises(ValueError):
        sc._guard_http_url(url)


@pytest.mark.parametrize("url", ["ftp://127.0.0.1/x", "file:///etc/passwd", "http:///nohost"])
def test_rejects_bad_scheme_or_host(url):
    with pytest.raises(ValueError):
        sc._guard_http_url(url)


def test_dns_failure_passes(monkeypatch):
    def boom(*a, **k):
        raise OSError("dns down")

    monkeypatch.setattr(sc.socket, "getaddrinfo", boom)
    sc._guard_http_url("http://some-lan-host.invalid:8000/x")


def test_guard_applied_at_all_outbound_sites():
    src = sc.__file__
    with open(src, encoding="utf-8") as f:
        text = f.read()
    # 定义 1 处 + 实际调用 3 处（health / liveness / _http_json）
    assert text.count("_guard_http_url(") >= 4, "出站三处（health/liveness/_http_json）都要过守卫"
    assert "def _guard_http_url(" in text
    assert "is_link_local" in text, "守卫必须拦链路本地/云元数据"
