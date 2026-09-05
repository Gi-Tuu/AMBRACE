# -*- coding: utf-8 -*-
"""wechat_ilink PR1 协议层单测：iLink HTTP 客户端 + 入站解析纯函数。

- 协议层用 mock httpx（不打真实网络）：统一以 _FakeAsyncClient 替换 httpx.AsyncClient。
- 入站解析层为纯函数，直接单测 _std_inbound 等。
- 真实微信扫码联调用例标 @pytest.mark.live，默认不跑（PR3 阻塞项，NEEDS_RUNTIME_VERIFICATION）。
"""
import asyncio
import importlib.util
import os
import pathlib

import httpx
import pytest

# 真实微信扫码联调默认跳过；设置 ILINK_RUN_LIVE=1 才运行（PR3 阻塞项）。
_RUN_LIVE = os.environ.get("ILINK_RUN_LIVE") == "1"

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"
_DEFAULT_HOST = "https://ilinkai.weixin.qq.com"


def _load(name: str):
    """加载 plugins/examples/wechat_ilink/<name>.py；用唯一模块名避免与项目内同名模块冲突。"""
    spec = importlib.util.spec_from_file_location("dsh_wechat_" + name, _PLUGIN_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


ilink = _load("ilink_client")
inbound = _load("inbound")


# ------------------------------------------------------------------ mock httpx
class _Resp:
    """模拟 httpx.Response：status_code 能触发 raise_for_status，json() 返回 payload。"""

    def __init__(self, payload=None, *, status_code: int = 200):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://ilinkai.weixin.qq.com/x")
            raise httpx.HTTPStatusError(
                "http error", request=req, response=httpx.Response(self.status_code, request=req)
            )

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """替换 httpx.AsyncClient：get/post 记录调用并返回配置好的响应或抛配置好的异常。"""

    def __init__(self, get=None, post=None):
        self._get = get
        self._post = post
        self.calls: list[tuple] = []

    async def get(self, url, params=None):
        self.calls.append(("GET", url, params))
        return self._make(self._get)

    async def post(self, url, json=None):
        self.calls.append(("POST", url, json))
        return self._make(self._post)

    async def aclose(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @staticmethod
    def _make(resp):
        if isinstance(resp, Exception):
            raise resp
        if isinstance(resp, _Resp):
            return resp
        return _Resp(resp if resp is not None else {})


def _patch(monkeypatch, get=None, post=None):
    """替换 httpx.AsyncClient 为受控 fake，并返回创建出的 fake 实例列表。"""
    instances = []

    def _ctor(*a, **k):
        c = _FakeAsyncClient(get=get, post=post)
        instances.append(c)
        return c

    monkeypatch.setattr(httpx, "AsyncClient", _ctor)
    return instances


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ 端点常量表结构
def test_endpoint_constants_structure():
    ep = ilink._EP
    # 六个方法齐全（§2.1/§8.2）
    assert set(ep.keys()) == {"qrcode", "qrcode_status", "getupdates", "sendmessage", "getconfig", "sendtyping"}
    # 路径统一以 /ilink/bot/ 开头，一处改全局生效
    for path in ep.values():
        assert path.startswith("/ilink/bot/")
    assert ilink.DEFAULT_HOST == "https://ilinkai.weixin.qq.com"


# ------------------------------------------------------------------ 取码
def test_fetch_qrcode_success(monkeypatch):
    instances = _patch(monkeypatch, get=_Resp({"ret": 0, "qrcode": "q1", "qrcode_img_content": "https://x"}))
    res = _run(ilink.ILinkClient.fetch_qrcode())
    assert res["ok"] is True
    assert res["qrcode"] == "q1"
    assert res["ret"] == 0
    # 使用默认 host + 端点，bot_type=3
    assert instances[0].calls == [("GET", _DEFAULT_HOST + "/ilink/bot/get_bot_qrcode", {"bot_type": 3})]


def test_fetch_qrcode_http_error(monkeypatch):
    _patch(monkeypatch, get=_Resp({"ret": 0}, status_code=500))
    res = _run(ilink.ILinkClient.fetch_qrcode())
    assert res["ok"] is False
    assert res["kind"] == "http"
    assert "message" in res


def test_fetch_qrcode_timeout(monkeypatch):
    req = httpx.Request("GET", _DEFAULT_HOST + "/ilink/bot/get_bot_qrcode", params={"bot_type": 3})
    _patch(monkeypatch, get=httpx.TimeoutException("timed out", request=req))
    res = _run(ilink.ILinkClient.fetch_qrcode())
    assert res["ok"] is False
    assert res["kind"] == "timeout"


def test_fetch_qrcode_protocol_ret_error(monkeypatch):
    # ret 非成功值 → 协议错误，并带回 ret
    _patch(monkeypatch, get=_Resp({"ret": 500, "errmsg": "expired"}))
    res = _run(ilink.ILinkClient.fetch_qrcode())
    assert res["ok"] is False
    assert res["kind"] == "protocol"
    assert res["ret"] == 500


# ------------------------------------------------------------------ 查状态
def test_fetch_qrcode_status_success(monkeypatch):
    instances = _patch(monkeypatch, get=_Resp({
        "status": "confirmed", "bot_token": "tok", "ilink_bot_id": "b1",
        "baseurl": "https://base", "ilink_user_id": "u1",
    }))
    res = _run(ilink.ILinkClient.fetch_qrcode_status("q1"))
    assert res["ok"] is True
    assert res["status"] == "confirmed"
    assert res["ilink_user_id"] == "u1"
    assert instances[0].calls == [("GET", _DEFAULT_HOST + "/ilink/bot/get_qrcode_status", {"qrcode": "q1"})]


def test_fetch_qrcode_status_http_error(monkeypatch):
    _patch(monkeypatch, get=_Resp({}, status_code=503))
    res = _run(ilink.ILinkClient.fetch_qrcode_status("q1"))
    assert res["ok"] is False
    assert res["kind"] == "http"


# ------------------------------------------------------------------ 长轮询收消息
def test_get_updates_success(monkeypatch):
    instances = _patch(monkeypatch, get=_Resp({
        "ret": 0, "messages": [{"msg_id": "m1", "content": "hi"}], "get_updates_buf": "next-buf",
    }))
    client = ilink.ILinkClient("tok", timeout=5)
    res = _run(client.get_updates("prev-buf"))
    assert res["ok"] is True
    assert res["buf"] == "next-buf"
    assert len(res["messages"]) == 1 and res["messages"][0]["msg_id"] == "m1"
    # 带 bot_token + get_updates_buf
    assert instances[0].calls == [
        ("GET", _DEFAULT_HOST + "/ilink/bot/getupdates", {"bot_token": "tok", "get_updates_buf": "prev-buf"})
    ]


def test_get_updates_without_buf(monkeypatch):
    instances = _patch(monkeypatch, get=_Resp({"ret": 0, "messages": []}))
    client = ilink.ILinkClient("tok")
    res = _run(client.get_updates())
    assert res["ok"] is True
    assert res["messages"] == []
    assert res["buf"] is None
    # buf 为空时不带 get_updates_buf，只带 bot_token
    assert instances[0].calls == [("GET", _DEFAULT_HOST + "/ilink/bot/getupdates", {"bot_token": "tok"})]


def test_get_updates_timeout(monkeypatch):
    req = httpx.Request("GET", _DEFAULT_HOST + "/ilink/bot/getupdates", params={"bot_token": "tok"})
    _patch(monkeypatch, get=httpx.TimeoutException("timed out", request=req))
    client = ilink.ILinkClient("tok")
    res = _run(client.get_updates())
    assert res["ok"] is False
    assert res["kind"] == "timeout"


# ------------------------------------------------------------------ 发文本
def test_send_text_with_context_token(monkeypatch):
    instances = _patch(monkeypatch, post=_Resp({"ret": 0, "msg_id": "out1"}))
    client = ilink.ILinkClient("tok")
    res = _run(client.send_text("你好呀", context_token="c-1"))
    assert res["ok"] is True
    assert res["msg_id"] == "out1"
    body = instances[0].calls[0][2]
    assert body["bot_token"] == "tok"
    assert body["content"] == "你好呀"
    assert body["msg_type"] == "text"
    assert body["context_token"] == "c-1"
    assert instances[0].calls[0][1] == _DEFAULT_HOST + "/ilink/bot/sendmessage"


def test_send_text_without_context_token(monkeypatch):
    instances = _patch(monkeypatch, post=_Resp({"ret": 0}))
    client = ilink.ILinkClient("tok")
    res = _run(client.send_text("主动推送"))
    assert res["ok"] is True
    body = instances[0].calls[0][2]
    assert body["content"] == "主动推送"
    assert body["msg_type"] == "text"
    assert "context_token" not in body  # 不带=主动推送


def test_send_text_http_error(monkeypatch):
    _patch(monkeypatch, post=_Resp({}, status_code=500))
    client = ilink.ILinkClient("tok")
    res = _run(client.send_text("hi"))
    assert res["ok"] is False
    assert res["kind"] == "http"


def test_send_text_timeout(monkeypatch):
    req = httpx.Request("POST", _DEFAULT_HOST + "/ilink/bot/sendmessage")
    _patch(monkeypatch, post=httpx.TimeoutException("timed out", request=req))
    client = ilink.ILinkClient("tok")
    res = _run(client.send_text("hi"))
    assert res["ok"] is False
    assert res["kind"] == "timeout"


# ------------------------------------------------------------------ typing
def test_send_typing_success(monkeypatch):
    instances = _patch(monkeypatch, post=_Resp({"ret": 0, "ticket": "tk1"}))
    client = ilink.ILinkClient("tok")
    res = _run(client.send_typing())
    assert res["ok"] is True
    # getconfig 拿 ticket，再 sendtyping 带 ticket
    assert instances[0].calls[0][1] == _DEFAULT_HOST + "/ilink/bot/getconfig"
    assert instances[0].calls[1][1] == _DEFAULT_HOST + "/ilink/bot/sendtyping"
    assert instances[0].calls[1][2] == {"bot_token": "tok", "ticket": "tk1"}


def test_send_typing_missing_ticket(monkeypatch):
    _patch(monkeypatch, post=_Resp({"ret": 0}))  # getconfig 未返回 ticket
    client = ilink.ILinkClient("tok")
    res = _run(client.send_typing())
    assert res["ok"] is False
    assert res["kind"] == "protocol"


# ------------------------------------------------------------------ 解析层纯函数
def test_std_inbound_full_fields():
    raw = {"msg_id": "m1", "content": "  你好  ", "context_token": "c1", "msg_type": "text"}
    out = inbound._std_inbound(raw)
    assert out["msg_id"] == "m1"
    assert out["text"] == "你好"
    assert out["context_token"] == "c1"
    assert out["msg_type"] == "text"
    assert out["is_text"] is True


def test_std_inbound_missing_fields_no_raise():
    out = inbound._std_inbound({})
    assert out["msg_id"] == ""
    assert out["text"] == ""
    assert out["context_token"] == ""
    assert out["is_text"] is True  # msg_type 缺失默认按 text


def test_std_inbound_empty_text():
    out = inbound._std_inbound({"msg_id": "m1", "content": "", "msg_type": "text"})
    assert out["text"] == ""
    assert out["is_text"] is True


def test_std_inbound_non_text():
    # 图片/语音：is_text False，文本不被当作对话文本
    out = inbound._std_inbound({"msg_id": "m1", "msg_type": "image", "content": "https://cdn/x.png"})
    assert out["is_text"] is False
    assert out["msg_type"] == "image"


def test_std_inbound_not_a_dict():
    for raw in (None, "str", 123, ["a"], b"bytes"):
        out = inbound._std_inbound(raw)
        assert out["msg_id"] == ""
        assert out["is_text"] is False


def test_std_inbound_msg_id_candidates():
    assert inbound._std_inbound({"message_id": "x1"})["msg_id"] == "x1"
    assert inbound._std_inbound({"id": "x2"})["msg_id"] == "x2"
    assert inbound._std_inbound({"msg_id": "x3", "message_id": "x1"})["msg_id"] == "x3"


def test_inbound_placeholder():
    assert inbound._inbound_placeholder({"msg_type": "image"}) == "[image]"
    assert inbound._inbound_placeholder({"msg_type": "voice"}) == "[voice]"
    assert inbound._inbound_placeholder({}) == "[media]"


def test_inbound_placeholder_text_passthrough():
    assert inbound._inbound_placeholder({"msg_type": "text", "text": "你好"}) == "你好"
    assert inbound._inbound_placeholder(None) == ""


# ------------------------------------------------------------------ live 真机联调（默认跳过）
@pytest.mark.live
@pytest.mark.skipif(not _RUN_LIVE, reason="live 用例默认跳过，需真实微信扫码联调（PR3 阻塞项）")
def test_live_full_roundtrip_real_wechat():
    """真实微信扫码联调（PR3 阻塞项，NEEDS_RUNTIME_VERIFICATION）：绑定→收发→解绑。

    默认不运行；需真实微信号与官方 ClawBot，跑通并校准端点/字段后才允许宣称可用。
    """
    raise AssertionError("live 用例默认跳过，需真实微信扫码联调（PR3 阻塞项）")
