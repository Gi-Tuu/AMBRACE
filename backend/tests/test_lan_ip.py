# -*- coding: utf-8 -*-
"""lan_ip 探测逻辑测试（2026-08-23：排除 VPN 虚拟网卡，优先真实私网地址）"""

import socket

import psutil

from app.application.system import _get_lan_ip, _is_private_ipv4  # F8：api 门面已删，改指定义模块


def test_is_private_common():
    assert _is_private_ipv4("192.168.1.21")
    assert _is_private_ipv4("10.0.0.5")
    assert _is_private_ipv4("172.16.3.4")


def test_is_private_excludes_virtual():
    assert not _is_private_ipv4("127.0.0.1")
    assert not _is_private_ipv4("169.254.242.204")
    assert not _is_private_ipv4("198.18.0.1")
    assert not _is_private_ipv4("8.8.8.8")


def _fake_sock(*_a, **_k):
    raise OSError("no default route")


def test_get_lan_ip_prefers_real_iface(monkeypatch):
    fake = {
        "WLAN": [type("A", (), {"family": socket.AF_INET, "address": "192.168.1.21"})()],
        "vEthernet (WSL)": [type("A", (), {"family": socket.AF_INET, "address": "172.29.240.1"})()],
        "Tailscale": [type("A", (), {"family": socket.AF_INET, "address": "100.89.98.7"})()],
        "iKuuuVPN": [type("A", (), {"family": socket.AF_INET, "address": "198.18.0.1"})()],
        "以太网": [type("A", (), {"family": socket.AF_INET, "address": "169.254.242.204"})()],
    }
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: fake)
    monkeypatch.setattr(socket, "socket", _fake_sock)
    assert _get_lan_ip() == "192.168.1.21"
