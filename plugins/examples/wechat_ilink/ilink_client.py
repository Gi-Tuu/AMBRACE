# -*- coding: utf-8 -*-
"""iLink / 官方 ClawBot HTTP 客户端（合规私聊通道）。

【命名空间】渠道插件顶层模块名易撞，本插件沿用包内相对导入/文件名，新渠道须用包内相对导入或模块名前缀（registry 加载器保持现状）。

NEEDS_RUNTIME_VERIFICATION：端点与字段来自官方插件行为 + 社区抓包交叉核实，
可能随微信版本变化；集中在本文件，真机扫码联调后只改这里。

本文件是纯协议层（PR1）：
- 不挂钩子、不读写 DB、不影响运行；
- 端点统一集中在 _EP 常量字典（§2.1/§8.2），一处改全局生效；
- 所有真机未核实的端点/字段标注 NEEDS_RUNTIME_VERIFICATION，未闭环前不得宣称可用；
- 对外公共方法统一返回 dict：成功含 ``ok: True``，失败吞掉超时/HTTP/协议错误并返回
  约定错误结构 ``{"ok": False, "kind": ..., "message": ...}``，绝不让 ILink 异常向上抛
  （P0-5：iLink 宕机/断网不得影响主链路）。
"""
from __future__ import annotations

import httpx

DEFAULT_HOST = "https://ilinkai.weixin.qq.com"

# 集中管理端点，便于真机校准（§2.1）。路径/参数名以真机抓包为准（NEEDS_RUNTIME_VERIFICATION）。
_EP = {
    "qrcode": "/ilink/bot/get_bot_qrcode",
    "qrcode_status": "/ilink/bot/get_qrcode_status",
    "getupdates": "/ilink/bot/getupdates",
    "sendmessage": "/ilink/bot/sendmessage",
    "getconfig": "/ilink/bot/getconfig",
    "sendtyping": "/ilink/bot/sendtyping",
}

# ret 成功值真机核实（NEEDS_RUNTIME_VERIFICATION）：默认 "0"/空/"None" 视为成功。
_SUCCESS_RET = ("0", "", "None")


class ILinkError(RuntimeError):
    """iLink 协议错误：ret != 成功值 / 非 JSON / 响应非 dict。"""

    def __init__(self, message: str, ret: str | int | None = None):
        super().__init__(message)
        self.ret = ret


def _ok(**payload: object) -> dict:
    """统一成功返回：``{"ok": True, ...}``。防御 iLink 恰好返回名为 ok 的字段。"""
    payload = dict(payload)
    payload.pop("ok", None)
    return {"ok": True, **payload}


def _error(kind: str, message: str, ret: str | int | None = None) -> dict:
    """统一错误返回：``{"ok": False, kind, message[, ret]}``（约定错误结构）。"""
    out: dict = {"ok": False, "kind": kind, "message": message}
    if ret is not None:
        out["ret"] = ret
    return out


def _is_success(data: dict) -> bool:
    """iLink ret 是否成功值。"""
    return str(data.get("ret")) in _SUCCESS_RET


def _kind_of(exc: Exception) -> str:
    """把异常归为约定错误结构里的 kind（timeout/http/protocol/unknown）。"""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPError):
        return "http"
    if isinstance(exc, (ILinkError, ValueError, TypeError, KeyError)):
        return "protocol"
    return "unknown"


class ILinkClient:
    """单绑定对应一个 client；host 确认后切换为返回的 baseurl。

    v1 只做私聊纯文本；图片/语音/文件等主动下发与多开群控均不在本协议层（见方案非目标）。
    """

    def __init__(self, bot_token: str, baseurl: str = DEFAULT_HOST, timeout: float = 30.0):
        self.bot_token = bot_token
        # baseurl 来自扫码 confirmed 返回，应缓存并只允许 *.weixin.qq.com / *.wechat.com 白名单域
        # （P3-2 SSRF 防 baseurl 投毒，白名单校验属 PR2 绑定落库时做，NEEDS_RUNTIME_VERIFICATION）。
        self.baseurl = (baseurl or DEFAULT_HOST).strip().rstrip("/")
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        """关闭底层连接池（由创建方负责调用）。"""
        await self._client.aclose()

    # ---------- 低层请求：失败抛出，由公共方法吞掉并转约定错误结构 ----------

    async def _get(self, ep: str, params: dict | None = None) -> dict:
        p = {"bot_token": self.bot_token, **(params or {})}
        r = await self._client.get(self.baseurl + _EP[ep], params=p)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ILinkError(f"{ep} unexpected payload type={type(data).__name__}")
        if not _is_success(data):
            raise ILinkError(f"{ep} ret={data.get('ret')} msg={data.get('errmsg')}", ret=data.get("ret"))
        return data

    async def _post(self, ep: str, body: dict | None = None) -> dict:
        payload = {"bot_token": self.bot_token, **(body or {})}
        r = await self._client.post(self.baseurl + _EP[ep], json=payload)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ILinkError(f"{ep} unexpected payload type={type(data).__name__}")
        if not _is_success(data):
            raise ILinkError(f"{ep} ret={data.get('ret')} msg={data.get('errmsg')}", ret=data.get("ret"))
        return data

    # ---------- 绑定流程（不依赖已存 token 的静态阶段） ----------

    @staticmethod
    async def fetch_qrcode(bot_type: int = 3) -> dict:
        """取绑定二维码。返回 ``{ok, qrcode, qrcode_img_content, ret}``（字段以真机为准）。"""
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(DEFAULT_HOST + _EP["qrcode"], params={"bot_type": bot_type})
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict):
                    raise ILinkError("qrcode unexpected payload type")
                if not _is_success(data):
                    raise ILinkError(f"qrcode ret={data.get('ret')} msg={data.get('errmsg')}", ret=data.get("ret"))
            return _ok(**data)
        except Exception as e:  # noqa: BLE001 - 协议层吞掉一切错误，返回约定错误结构
            return _error(_kind_of(e), f"fetch_qrcode failed: {e}", ret=getattr(e, "ret", None))

    @staticmethod
    async def fetch_qrcode_status(qrcode: str) -> dict:
        """轮询扫码状态。confirmed 时含 bot_token/ilink_bot_id/baseurl/ilink_user_id（字段以真机为准）。

        注意：qrcode/status 响应不一定带 ret，故此处不强制 ret 成功值，仅吞掉异常。
        """
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(DEFAULT_HOST + _EP["qrcode_status"], params={"qrcode": qrcode})
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict):
                    raise ILinkError("qrcode_status unexpected payload type")
            return _ok(**data)
        except Exception as e:  # noqa: BLE001
            return _error(_kind_of(e), f"fetch_qrcode_status failed: {e}", ret=getattr(e, "ret", None))

    # ---------- 收消息（长轮询） ----------

    async def get_updates(self, buf: str | None = None) -> dict:
        """长轮询拉取。返回 ``{ok, messages: [...], buf: 新游标}``。

        消息结构（发送人/文本/context_token/msg_id 真实路径）以真机为准，交入站解析层
        `_std_inbound` 标准化（NEEDS_RUNTIME_VERIFICATION）。
        """
        try:
            params = {"get_updates_buf": buf} if buf else None
            data = await self._get("getupdates", params)
            messages = data.get("messages") or data.get("data") or []
            new_buf = data.get("get_updates_buf") or buf
            return _ok(messages=messages, buf=new_buf)
        except Exception as e:  # noqa: BLE001
            return _error(_kind_of(e), f"getupdates failed: {e}", ret=getattr(e, "ret", None))

    # ---------- 发消息 ----------

    async def send_text(self, text: str, context_token: str | None = None) -> dict:
        """发送文本。带 context_token = 回复该条（形成引用）；不带 = 主动推送。

        v1 整段合并为恰好 1 条 sendmessage（P1-1：严禁逐 token/气泡拆分吃光配额）。
        """
        try:
            body: dict = {"content": text, "msg_type": "text"}
            if context_token:
                body["context_token"] = context_token
            data = await self._post("sendmessage", body)
            return _ok(**data)
        except Exception as e:  # noqa: BLE001
            return _error(_kind_of(e), f"sendmessage failed: {e}", ret=getattr(e, "ret", None))

    async def send_typing(self) -> dict:
        """可选：正在输入。先 getconfig 拿 ticket，再 sendtyping（默认关，省请求配额）。

        v1 默认不调用；本方法仅留协议接口（§2.1）。ticket 有效期字段以真机为准。
        """
        try:
            cfg = await self._post("getconfig", {})
            ticket = cfg.get("ticket")
            if not ticket:
                return _error("protocol", "getconfig 未返回 ticket")
            data = await self._post("sendtyping", {"ticket": ticket})
            return _ok(**data)
        except Exception as e:  # noqa: BLE001
            return _error(_kind_of(e), f"sendtyping failed: {e}", ret=getattr(e, "ret", None))
