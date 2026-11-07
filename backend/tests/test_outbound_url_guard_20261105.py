# -*- coding: utf-8 -*-
"""2026-11-05 安全复核批合同：出站 URL 守卫（SSRF）＋上传子目录防线。

覆盖：内网/回环/链路本地/假地址段拦截、公网 IP 放行、协议与 host 校验、显式放行开关、
DNS 失败放行（不阻断链路）、上传 subdir 白名单判定、以及四处接线棘轮。
"""
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/x",
    "http://127.1.1.1/",
    "http://[::1]:8765/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.1.2.3/",
    "http://192.168.1.10/",
    "http://172.20.0.1/",
    "http://198.18.0.1/",
    "http://0.0.0.0/",
])
def test_blocks_private_and_metadata_targets(url):
    from app.utils.net_guard import ensure_outbound_url

    with pytest.raises(ValueError):
        ensure_outbound_url(url)


@pytest.mark.parametrize("url", [
    "https://93.184.216.34/api",
    "http://1.1.1.1/",
    "https://8.8.4.4/x?y=1",
])
def test_allows_public_ip_literals(url):
    from app.utils.net_guard import ensure_outbound_url

    ensure_outbound_url(url)  # 不抛即通过


def test_rejects_bad_scheme_or_missing_host():
    from app.utils.net_guard import ensure_outbound_url

    for url in ("file:///etc/passwd", "ftp://example.com/x", "http:///nohost", ""):
        with pytest.raises(ValueError):
            ensure_outbound_url(url)


def test_explicit_allow_private(monkeypatch):
    from app.utils import net_guard

    net_guard.ensure_outbound_url("http://127.0.0.1:8000/", allow_private=True)  # 参数放行
    from app.config import settings
    monkeypatch.setattr(settings, "http_fetch_allow_private", True, raising=False)
    net_guard.ensure_outbound_url("http://127.0.0.1:8000/")  # 配置放行


def test_dns_failure_does_not_break_link(monkeypatch):
    import socket

    from app.utils.net_guard import ensure_outbound_url

    def boom(*a, **k):
        raise OSError("dns down")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    ensure_outbound_url("https://some-host.invalid/audio")  # 解析失败 ⇒ 放行（请求本身会失败）


@pytest.mark.parametrize("subdir", ["1", "moments/3", "avatars/5", "phone/7/album", "files/9"])
def test_upload_subdir_accepts_relative_segments(subdir):
    from app.application.upload_service import safe_subdir

    assert safe_subdir(subdir) == subdir


@pytest.mark.parametrize("subdir", ["", "../x", "a/../../b", "/abs/path", "C:/x", "c:../x", "file://x"])
def test_upload_subdir_rejects_escape(subdir):
    from app.application.upload_service import safe_subdir

    with pytest.raises(ValueError):
        safe_subdir(subdir)


def test_wiring_ratchets():
    tts = (APP / "application" / "tts_service.py").read_text(encoding="utf-8")
    assert tts.count("ensure_outbound_url") >= 3, "TTS 端点与音频 URL 都要校验（含 import 一行）"
    cfg = (APP / "config.py").read_text(encoding="utf-8")
    assert "http_fetch_allow_private" in cfg
    up = (APP / "application" / "upload_service.py").read_text(encoding="utf-8")
    assert "safe_subdir(subdir)" in up and "invalid_upload_target" in up
