# -*- coding: utf-8 -*-
"""本地渠道登录页（P2｜扫码绑定下放手机，2026-09-12）。

给桌面控制台「渠道登录」按钮用的极简网页（服务器本地浏览器打开）：微信二维码在电脑上展示
与轮询，confirmed 后代写网关账号文件（等价 openclaw channels login 落盘）→ bot 出现在
App「查看可添加的机器人」→ App 选角色绑定；网关需重启一次开始拉消息。

【红线】二维码等于登录凭证：所有接口仅允许 127.0.0.1 访问（::1 一并放行），不写日志、不落盘。
【实现】微信协议层（ilink_client）与网关落盘（gateway_accounts）按文件路径懒加载/复用
sys.modules——均为无状态纯协议模块，与运行中的插件实例不冲突；不复制协议逻辑。
抖音不进本页：其扫码会话依赖插件进程内有状态 worker（二次加载会产生第二个浏览器 worker
抢同一 profile），App 内扫码已覆盖，电脑端走插件旧 POST /bind。
"""
from __future__ import annotations

import base64
import importlib.util
import sys
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/channel-login", tags=["Channel Login"])

_PLUGIN_PROTOCOL_DIR = Path(__file__).resolve().parents[2] / "data" / "plugins" / "wechat_ilink"

_PAGE_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>AMBRACE 渠道登录</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:420px;margin:40px auto;padding:0 16px;color:#333}
 img.qr{width:260px;height:260px;border:1px solid #ddd;border-radius:10px;padding:8px;background:#fff}
 .st{margin:14px 0;font-size:14px}
 .ok{color:#0a7d32}.warn{color:#b26a00}.err{color:#b00020}
 button{padding:8px 18px;border:1px solid #ccc;border-radius:8px;background:#f6f6f6;cursor:pointer}
</style></head><body>
<h3>微信 ClawBot 扫码登录</h3>
<div class="st" id="st">正在获取二维码…</div>
<div id="qrbox"></div>
<div style="margin-top:18px"><button onclick="location.reload()">重新取码</button></div>
<p style="margin-top:26px;font-size:12px;color:#888">
 抖音渠道：请直接在手机 App 内扫码，或在拥爱 App「扩展 → 抖音」使用「在电脑上扫码」。</p>
<script>
let qrcode = "";
async function fetchQr() {
  const r = await fetch("./api/wechat/qrcode");
  const d = await r.json();
  if (!d.ok) { document.getElementById("st").textContent = "取码失败：" + (d.message || d.detail || ""); return; }
  qrcode = d.qrcode;
  document.getElementById("qrbox").innerHTML = '<img class="qr" src="' + d.qr_png_data_url + '">';
  document.getElementById("st").textContent = "请用手机微信「扫一扫」识别二维码";
  poll();
}
async function poll() {
  while (true) {
    await new Promise(r => setTimeout(r, 1000));
    if (!qrcode) return;
    let d;
    try { d = await (await fetch("./api/wechat/status?qrcode=" + encodeURIComponent(qrcode))).json(); }
    catch (e) { continue; }
    const st = document.getElementById("st");
    const s = d.status || "";
    if (s === "wait") { st.textContent = "等待扫码…"; }
    else if (s === "scaned" || s === "scaned_but_redirect") { st.textContent = "已扫码，请在手机上确认"; }
    else if (s === "need_verifycode") { st.textContent = "请在手机微信上查看数字配对码（当前版本请在 App 内完成配对码校验）"; }
    else if (s === "expired") { st.className = "st warn"; st.textContent = "二维码已过期，请点「重新取码」"; return; }
    else if (s === "binded_redirect") { st.className = "st ok"; st.textContent = "该机器人已登录过网关，无需重复扫码（在 App「查看可添加的机器人」中绑定即可）"; return; }
    else if (s === "confirmed") {
      st.className = "st ok";
      st.textContent = d.gateway_registered
        ? "登录成功！已写入网关。请在 App「扩展 → 微信」点「查看可添加的机器人」选角色绑定；网关需重启一次开始收发消息。"
        : "登录成功，但写网关账号失败：请在电脑运行 openclaw channels login 后在 App 内绑定。";
      return;
    }
  }
}
fetchQr();
</script></body></html>"""


def _local_only(request: Request) -> None:
    """红线：二维码=登录凭证，本页全部接口仅限本机回环访问。"""
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="渠道登录页仅限本机访问（127.0.0.1）")


def _load_plugin_module(name: str):
    """按文件路径加载微信插件的无状态协议模块；插件运行时已加载同名模块则直接复用。"""
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    path = _PLUGIN_PROTOCOL_DIR / f"{name}.py"
    if not path.is_file():
        raise HTTPException(status_code=503, detail="微信插件未安装")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # 先注册再执行（模块内相对 import 兄弟模块需要）
    spec.loader.exec_module(mod)
    return mod


def _normalize_bot_id(raw: str) -> str:
    s = str(raw or "").strip()
    return s[: -len("@im.bot")] + "-im-bot" if s.endswith("@im.bot") else s


def _qr_png_data_url(content: str) -> str:
    """二维码内容 → PNG data URL（纯内存，不落盘）。"""
    import qrcode  # noqa: PLC0415  运行依赖见 backend/requirements.txt

    img = qrcode.make(content)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def page(request: Request):
    _local_only(request)
    return _PAGE_HTML


@router.get("/api/wechat/qrcode")
async def wechat_qrcode(request: Request):
    """取码（对齐 App 通道：上报 local_token_list）；二维码内容渲染为 PNG data URL 返回。"""
    _local_only(request)
    ilink = _load_plugin_module("ilink_client")
    gw = _load_plugin_module("gateway_accounts")
    result = await ilink.ILinkClient.fetch_qrcode(local_token_list=gw.read_local_bot_tokens())
    if not result.get("ok"):
        return {"ok": False, "message": result.get("message", "取码失败")}
    content = str(result.get("qrcode_img_content") or "")
    if not content:
        return {"ok": False, "message": "上游未返回二维码内容"}
    return {"ok": True, "qrcode": result.get("qrcode", ""),
            "qr_png_data_url": _qr_png_data_url(content)}


@router.get("/api/wechat/status")
async def wechat_status(request: Request, qrcode: str = "", verify_code: str = ""):
    """轮询扫码状态（长轮询透传）；confirmed 时代写网关账号文件（best-effort）。"""
    _local_only(request)
    qrcode = qrcode.strip()
    if not qrcode:
        return {"ok": False, "status": "failed", "message": "缺少 qrcode"}
    ilink = _load_plugin_module("ilink_client")
    result = await ilink.ILinkClient.fetch_qrcode_status(qrcode, verify_code=verify_code.strip())
    out = dict(result)
    if result.get("ok") and str(result.get("status") or "") == "confirmed":
        gw = _load_plugin_module("gateway_accounts")
        bot_id = _normalize_bot_id(str(result.get("ilink_bot_id") or ""))
        token = str(result.get("bot_token") or "")
        registered = False
        if bot_id and token:
            registered = gw.register_account(
                bot_id, token, baseurl=str(result.get("baseurl") or ""),
                user_id=str(result.get("ilink_user_id") or ""))
        out["gateway_registered"] = bool(registered)
    return out
