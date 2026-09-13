# -*- coding: utf-8 -*-
"""iLink / 官方 ClawBot HTTP 客户端（合规私聊通道）。

【命名空间】渠道插件顶层模块名易撞，本插件沿用包内相对导入/文件名，新渠道须用包内相对导入或模块名前缀（registry 加载器保持现状）。

RUNTIME_VERIFIED（2026-09-12 部分闭环）：取码/状态端点已实测（waiting 态 + 字段结构），
状态机全量字段依据腾讯官方插件源码（@tencent-weixin/openclaw-weixin src/auth/login-qr.ts，
本机 ~/.openclaw/npm/projects/ 有副本）；confirmed 态待真机扫码闭环后最终确认。

本文件是纯协议层（PR1）：
- 不挂钩子、不读写 DB、不影响运行；
- 端点统一集中在 _EP 常量字典（§2.1/§8.2），一处改全局生效；
- 仍未真机核实的分支（confirmed 载荷细节）在方法 docstring 标注，闭环后更新；
- 对外公共方法统一返回 dict：成功含 ``ok: True``，失败吞掉超时/HTTP/协议错误并返回
  约定错误结构 ``{"ok": False, "kind": ..., "message": ...}``，绝不让 ILink 异常向上抛
  （P0-5：iLink 宕机/断网不得影响主链路）。
"""
from __future__ import annotations

import httpx

DEFAULT_HOST = "https://ilinkai.weixin.qq.com"

# 集中管理端点，便于真机校准（§2.1）。
# 【2026-09-12 真机核准】取码 GET /get_bot_qrcode?bot_type=3 返回 {qrcode, qrcode_img_content, ret="0"}
# 实测通过；qrcode_img_content 是**URL 字符串**（https://liteapp.weixin.qq.com/q/...，非 base64 图），
# 需由展示端自行渲染成二维码。状态端点为**长轮询**（腾讯官方客户端 35s 超时，见 auth/login-qr.ts）。
_EP = {
    "qrcode": "/ilink/bot/get_bot_qrcode",
    "qrcode_status": "/ilink/bot/get_qrcode_status",
    "getupdates": "/ilink/bot/getupdates",
    "sendmessage": "/ilink/bot/sendmessage",
    "getconfig": "/ilink/bot/getconfig",
    "sendtyping": "/ilink/bot/sendtyping",
}

# ret 成功值真机核实（2026-09-12 实测 ret="0"）：默认 "0"/空/"None" 视为成功。
_SUCCESS_RET = ("0", "", "None")

# 状态长轮询客户端超时：对齐腾讯官方 login-qr.ts 的 QR_LONG_POLL_TIMEOUT_MS=35s（留 5s 余量）。
# 上游在无状态变化时会挂住连接直到超时，客户端超时 = 仍在等待（App/调用方把 wait 当正常态继续轮询）。
_QR_STATUS_TIMEOUT_S = 40.0


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
    async def fetch_qrcode(bot_type: int = 3, local_token_list: list[str] | None = None) -> dict:
        """取绑定二维码。返回 ``{ok, qrcode, qrcode_img_content, ret}``。

        【2026-09-12 核准】qrcode_img_content 为 URL 字符串（需渲染成二维码图，非 base64 图片）。
        对齐腾讯官方 fetchQRCode（POST + local_token_list=本机已登录 bot token，最新在前最多 10 个）：
        上游据此识别「扫码的 bot 已连接过」并回 binded_redirect。GET 方式（旧实现）实测也可取码，
        但缺 local_token_list 时上游无法做已连接判定。
        """
        try:
            body = {"local_token_list": [str(t) for t in (local_token_list or [])][:10]}
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    DEFAULT_HOST + _EP["qrcode"],
                    params={"bot_type": bot_type},
                    json=body,
                )
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
    async def fetch_qrcode_status(
        qrcode: str,
        verify_code: str = "",
        base_url: str = "",
        timeout: float = _QR_STATUS_TIMEOUT_S,
    ) -> dict:
        """轮询扫码状态（长轮询，无变化时上游挂住直到超时）。

        【2026-09-12 核准（依据腾讯官方 auth/login-qr.ts）】成功响应含 ``status`` 字段：
        wait / scaned / confirmed / expired / scaned_but_redirect / need_verifycode /
        verify_code_blocked / binded_redirect；confirmed 时含
        bot_token / ilink_bot_id / baseurl / ilink_user_id；scaned_but_redirect 时含
        redirect_host（IDC 分流，后续轮询须切换到 https://{redirect_host}）。
        need_verifycode 时须在下轮轮询携带 verify_code（用户在微信上看到的数字配对码）。

        客户端超时/网络错误 → 返回 ``{ok: True, status: "wait"}``（对齐官方：视为仍在等待，
        由调用方继续轮询）；协议错误 → 约定错误结构。
        """
        try:
            params: dict = {"qrcode": qrcode}
            if verify_code:
                params["verify_code"] = verify_code
            url = (base_url or DEFAULT_HOST).strip().rstrip("/") + _EP["qrcode_status"]
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, params=params)
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict):
                    raise ILinkError("qrcode_status unexpected payload type")
            return _ok(**data)
        except Exception as e:  # noqa: BLE001
            # 异常映射（2026-09-12 红点修复拍板，对齐官方 pollQRStatus 并收窄）：
            # - 客户端超时 = 长轮询无变化 → wait 继续轮询（官方 AbortError→wait 口径）；
            # - 真实 HTTP 4xx/5xx = 真故障 → 约定错误结构 kind="http"（吞成 wait 会让用户
            #   静默卡住、排障无线索；App 侧连续错误会给可见提示）；
            # - 其余传输层网络抖动（连接失败/读断等）→ wait 重试（官方「网络错误视为等待」口径）。
            if isinstance(e, httpx.TimeoutException):
                return _ok(status="wait")
            if isinstance(e, httpx.HTTPStatusError):
                return _error("http", f"fetch_qrcode_status http {e.response.status_code}")
            if isinstance(e, httpx.HTTPError):
                return _ok(status="wait")
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
