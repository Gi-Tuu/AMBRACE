# -*- coding: utf-8 -*-
"""扫码绑定下放手机（2026-09-12）后端测试。

- gateway_accounts：网关账号落盘（幂等/只加不改/token 读取顺序）；
- ilink_client：状态长轮询「超时=wait」语义（对齐腾讯官方 pollQRStatus）与成功透传；
- 路由层：scaned_but_redirect 的 redirect_host 记忆与白名单（SSRF 防护）。

抖音 bind/qr 会话为有状态浏览器 worker（单 worker + 有头 Edge），不在单测覆盖
（见交接红线；会话 dict 逻辑随真机验证）。
"""
import asyncio
import json
import sys

import httpx
import pytest

import pathlib as _pl

_PLUGIN_DIR = _pl.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"
_PLUGIN_DIR_STR = str(_PLUGIN_DIR)

# 原始 AsyncClient（httpx 为共享模块对象，monkeypatch 替换后无法再取到原始类）
_PRISTINE_ASYNC_CLIENT = httpx.AsyncClient


def _ensure_path():
    if _PLUGIN_DIR_STR not in sys.path:
        sys.path.insert(0, _PLUGIN_DIR_STR)


# ---------------- gateway_accounts 纯函数 ----------------

def test_gw_register_writes_account_and_index(tmp_path):
    _ensure_path()
    import gateway_accounts

    state = tmp_path / "openclaw-weixin"
    ok = gateway_accounts.register_account(
        "abc123-im-bot", "tok", baseurl="https://ilinkai.weixin.qq.com",
        user_id="u1@im.wechat", state_dir=state)
    assert ok is True
    acc = json.loads((state / "accounts" / "abc123-im-bot.json").read_text(encoding="utf-8"))
    assert acc["token"] == "tok"
    assert acc["baseUrl"] == "https://ilinkai.weixin.qq.com"
    assert acc["userId"] == "u1@im.wechat"
    assert json.loads((state / "accounts.json").read_text(encoding="utf-8")) == ["abc123-im-bot"]
    assert gateway_accounts.account_registered("abc123-im-bot", state_dir=state) is True


def test_gw_register_idempotent_no_overwrite(tmp_path):
    _ensure_path()
    import gateway_accounts

    state = tmp_path / "openclaw-weixin"
    assert gateway_accounts.register_account("b1-im-bot", "old-tok", state_dir=state) is True
    # 已注册：再注册不覆盖旧凭据（只加不改）
    assert gateway_accounts.register_account("b1-im-bot", "new-tok", state_dir=state) is True
    acc = json.loads((state / "accounts" / "b1-im-bot.json").read_text(encoding="utf-8"))
    assert acc["token"] == "old-tok"


def test_gw_read_local_bot_tokens_order_and_limit(tmp_path):
    _ensure_path()
    import gateway_accounts

    state = tmp_path / "openclaw-weixin"
    for i, aid in enumerate(["a-im-bot", "b-im-bot", "c-im-bot"]):
        gateway_accounts.register_account(aid, f"tok-{aid}", state_dir=state)
    (state / "accounts" / "b-im-bot.json").write_text("not json", encoding="utf-8")  # 损坏文件跳过
    tokens = gateway_accounts.read_local_bot_tokens(state_dir=state, limit=10)
    assert tokens == ["tok-c-im-bot", "tok-a-im-bot"]  # 最新在前；损坏的 b 跳过
    assert gateway_accounts.read_local_bot_tokens(state_dir=state, limit=1) == ["tok-c-im-bot"]


def test_gw_register_invalid_args(tmp_path):
    _ensure_path()
    import gateway_accounts

    assert gateway_accounts.register_account("", "tok", state_dir=tmp_path) is False
    assert gateway_accounts.register_account("x-im-bot", "", state_dir=tmp_path) is False


# ---------------- ilink_client 协议语义 ----------------

def _patch_http(monkeypatch, handler):
    """把 ilink_client 里的 httpx.AsyncClient 换成注入 MockTransport 的版本。"""
    _ensure_path()
    import ilink_client

    real_client = _PRISTINE_ASYNC_CLIENT

    def fake_client(*a, **kw):
        kw.pop("transport", None)
        return real_client(transport=httpx.MockTransport(handler), *a, **kw)

    monkeypatch.setattr(ilink_client.httpx, "AsyncClient", fake_client)
    return ilink_client


def test_status_timeout_maps_to_wait(monkeypatch):
    """客户端超时 = 仍在等待（对齐腾讯官方：AbortError/网络错误 → wait 继续轮询）。"""
    def handler(request):
        raise httpx.ConnectTimeout("boom", request=request)

    ilink = _patch_http(monkeypatch, handler)
    out = asyncio.run(ilink.ILinkClient.fetch_qrcode_status("q1"))
    assert out == {"ok": True, "status": "wait"}


def test_status_success_passthrough(monkeypatch):
    """成功响应原样透传（含 status / confirmed 凭据字段）。"""
    def handler(request):
        assert "qrcode=q1" in str(request.url)
        return httpx.Response(200, json={"status": "scaned", "ret": "0"})

    ilink = _patch_http(monkeypatch, handler)
    out = asyncio.run(ilink.ILinkClient.fetch_qrcode_status("q1"))
    assert out["ok"] is True and out["status"] == "scaned"

    def handler_confirmed(request):
        assert "verify_code=42" in str(request.url)
        return httpx.Response(200, json={"status": "confirmed", "bot_token": "t",
                                         "ilink_bot_id": "b@im.bot", "baseurl": "https://ilinkai.weixin.qq.com",
                                         "ilink_user_id": "u"})

    ilink2 = _patch_http(monkeypatch, handler_confirmed)
    out2 = asyncio.run(ilink2.ILinkClient.fetch_qrcode_status("q1", verify_code="42"))
    assert out2["ok"] is True and out2["status"] == "confirmed"
    assert out2["ilink_bot_id"] == "b@im.bot" and out2["bot_token"] == "t"


def test_fetch_qrcode_posts_local_token_list(monkeypatch):
    """取码走 POST + local_token_list（对齐官方 fetchQRCode）。"""
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"qrcode": "q", "qrcode_img_content": "https://x/q", "ret": "0"})

    ilink = _patch_http(monkeypatch, handler)
    out = asyncio.run(ilink.ILinkClient.fetch_qrcode(local_token_list=["t1", "t2"]))
    assert out["ok"] is True and out["qrcode"] == "q"
    assert seen["method"] == "POST"
    assert seen["body"] == {"local_token_list": ["t1", "t2"]}


# ---------------- 路由层：redirect_host 记忆 + 白名单 ----------------

@pytest.fixture()
def qr_routes(monkeypatch):
    """加载 wechat_ilink 插件（仅路由面，不建库）并 stub 掉 ILinkClient 与网关 token 读取。"""
    _ensure_path()
    from app.plugins import registry

    if not registry.load_plugin_dir(_PLUGIN_DIR):
        raise RuntimeError("wechat_ilink plugin failed to load")
    monkeypatch.setattr("gateway_accounts.read_local_bot_tokens", lambda *a, **kw: [])

    calls: list[dict] = []

    class FakeClient:
        @staticmethod
        async def fetch_qrcode(*a, **kw):
            return {"ok": True, "qrcode": "qX", "qrcode_img_content": "https://x/q", "ret": "0"}

        @staticmethod
        async def fetch_qrcode_status(qrcode, verify_code="", base_url="", timeout=None):
            calls.append({"qrcode": qrcode, "verify_code": verify_code,
                          "base_url": base_url, "timeout": timeout})
            return _next_status.pop(0)

    import ilink_client
    monkeypatch.setattr(ilink_client, "ILinkClient", FakeClient)

    # 装配路由（不带鉴权覆盖：/qrcode 端点在 router 全局鉴权之下，测试里覆盖为 uid=1）
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.auth.deps import get_current_user_id

    app = FastAPI()
    router_obj = registry._loaded["wechat_ilink"].get("router")
    assert router_obj is not None
    app.include_router(router_obj)
    app.dependency_overrides[get_current_user_id] = lambda: 1
    client = TestClient(app)
    yield client, calls
    from app.plugins import registry as _reg

    _reg._loaded.pop("wechat_ilink", None)
    _reg._db_config.pop("wechat_ilink", None)
    _reg._enabled.pop("wechat_ilink", None)


_next_status: list[dict] = []


def test_redirect_host_remembered_and_whitelisted(qr_routes):
    """scaned_but_redirect：后续轮询切到 redirect_host；非法域被白名单拦下回落默认。"""
    global _next_status
    client, calls = qr_routes
    r1 = client.get("/api/v1/plugins/wechat_ilink/qrcode")
    assert r1.json()["ok"] is True

    _next_status = [{"ok": True, "status": "scaned_but_redirect",
                     "redirect_host": "abc.idc.weixin.qq.com"}]
    client.get("/api/v1/plugins/wechat_ilink/qrcode/qX")
    _next_status = [{"ok": True, "status": "wait"}]
    client.get("/api/v1/plugins/wechat_ilink/qrcode/qX")
    assert calls[-1]["base_url"] == "https://abc.idc.weixin.qq.com"

    # 非法域：不在白名单 → 回落默认 host（base_url 为空）
    _next_status = [{"ok": True, "status": "scaned_but_redirect", "redirect_host": "evil.example.com"}]
    client.get("/api/v1/plugins/wechat_ilink/qrcode/qX")
    _next_status = [{"ok": True, "status": "wait"}]
    client.get("/api/v1/plugins/wechat_ilink/qrcode/qX")
    assert calls[-1]["base_url"] == ""


def test_verify_code_passthrough(qr_routes):
    """need_verifycode → App 带回的 verify_code 透传给协议层。"""
    global _next_status
    client, calls = qr_routes
    _next_status = [{"ok": True, "status": "wait"}]
    client.get("/api/v1/plugins/wechat_ilink/qrcode/qX", params={"verify_code": "42"})
    assert calls[-1]["verify_code"] == "42"
    # 路由不覆盖超时（由协议层默认 40s 承担，对齐官方 35s 长轮询）
    import ilink_client

    assert ilink_client._QR_STATUS_TIMEOUT_S >= 35
