"""48a 插件桥服务层：store KV / getUserInfo / http 代理（SSRF 防护）/ ai 分发（调 48b service）。

桥 API 白名单与 HTTP 级错误（401/404/400/429）在 app/api/plugin_bridge.py；
本模块只实现能力本身，业务错误统一以 {"ok": False, "error": ...} 返回，由 API 层包成响应。
"""
import asyncio
import ipaddress
import json
import socket
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.agent.llm_client import TASK_PLUGIN_AI, chat_completion, get_user_llm_config
from app.config import settings
from app.db.database import async_session_factory
from app.i18n import tr_lang
from app.models.plugin_store import PluginStore
from app.models.user import User
from app.utils.logger import get_logger

_logger = get_logger("services.plugin_bridge")

# ---- 桥 API 白名单（含统一入口 call；openChat/toast/copy/navigate 为前端能力，不落后端）----
VALID_APIS = ("ai", "getAiList", "getAiInfo", "getUserInfo", "store.set", "store.get", "http", "call")

# ---- store 限额 ----
MAX_STORE_VALUE_BYTES = 100 * 1024  # store value ≤100KB（序列化后 UTF-8 字节）
MAX_STORE_KEY_CHARS = 128

# ---- 桥 ai 限额（进程内滑动窗口；每用户每插件 plugin_bridge_ai_rate_per_min/分、_per_day/天；
#      北京时间日期键；重启清零可接受，对齐 app/api/plugins.py 与 character_chat_api 模式）----
_BRIDGE_WINDOW_SEC = 60.0
# (user_id, plugin_name) -> 分钟窗口时间戳 deque（monotonic）
_bridge_hits_min: dict[tuple[int, str], deque] = defaultdict(deque)
# (user_id, plugin_name) -> [日期串(北京时间), 当日计数]
_bridge_hits_day: dict[tuple[int, str], list] = defaultdict(lambda: [None, 0])

_UA = "AMBRACE-PluginBridge/1.0"


def bridge_ai_rate_check(user_id: int, plugin_name: str) -> tuple[bool, int]:
    """进程内限额：返回 (是否放行, 429 重试秒数)；放行时记录本次调用（纯逻辑，可单测）"""
    rate_min = int(getattr(settings, "plugin_bridge_ai_rate_per_min", 10) or 10)
    rate_day = int(getattr(settings, "plugin_bridge_ai_rate_per_day", 200) or 200)
    now = _time.monotonic()
    key = (user_id, plugin_name)
    dq = _bridge_hits_min[key]
    while dq and now - dq[0] > _BRIDGE_WINDOW_SEC:
        dq.popleft()
    if len(dq) >= rate_min:
        wait = int(_BRIDGE_WINDOW_SEC - (now - dq[0])) + 1
        return False, max(1, wait)
    day_key = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    rec = _bridge_hits_day[key]
    if rec[0] != day_key:
        rec[0], rec[1] = day_key, 0
    if rec[1] >= rate_day:
        return False, 60
    dq.append(now)
    rec[1] += 1
    return True, 0


def reset_bridge_ai_rate() -> None:
    """清空限额状态（测试用）"""
    _bridge_hits_min.clear()
    _bridge_hits_day.clear()


# ---------------- store KV ----------------

async def store_set(plugin_name: str, user_id: int, key: str, value, lang: str = "zh") -> dict:
    """插件命名空间 KV 写（按 plugin_name+user_id 隔离；value 任意 JSON ≤100KB；upsert）"""
    key = str(key or "").strip()
    if not key or len(key) > MAX_STORE_KEY_CHARS:
        return {"ok": False, "error": tr_lang(lang, "store_key_invalid")}
    try:
        value_json = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"ok": False, "error": tr_lang(lang, "store_value_not_json")}
    if len(value_json.encode("utf-8")) > MAX_STORE_VALUE_BYTES:
        return {"ok": False, "error": tr_lang(lang, "store_value_too_large")}
    async with async_session_factory() as db:
        row = (await db.execute(
            select(PluginStore).where(
                PluginStore.plugin_name == plugin_name,
                PluginStore.user_id == user_id,
                PluginStore.key == key,
            )
        )).scalar_one_or_none()
        if row is None:
            db.add(PluginStore(plugin_name=plugin_name, user_id=user_id, key=key, value_json=value_json))
        else:
            row.value_json = value_json
        await db.commit()
    return {"ok": True, "data": True}


async def store_get(plugin_name: str, user_id: int, key=None) -> dict:
    """插件命名空间 KV 读；key 缺省返回整个存储对象 {key: value}"""
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(PluginStore).where(
                PluginStore.plugin_name == plugin_name,
                PluginStore.user_id == user_id,
            )
        )).scalars().all()
    if key is not None and str(key).strip():
        k = str(key).strip()
        for r in rows:
            if r.key == k:
                try:
                    return {"ok": True, "data": json.loads(r.value_json or "{}")}
                except Exception:
                    return {"ok": True, "data": None}
        return {"ok": True, "data": None}
    obj: dict = {}
    for r in rows:
        try:
            obj[r.key] = json.loads(r.value_json or "{}")
        except Exception:
            obj[r.key] = None
    return {"ok": True, "data": obj}


# ---------------- getUserInfo ----------------

async def get_user_info(user_id: int, lang: str = "zh") -> dict:
    """当前登录用户 {id, nickname, avatar_url}"""
    async with async_session_factory() as db:
        u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if u is None:
        return {"ok": False, "error": tr_lang(lang, "user_not_found")}
    return {"ok": True, "data": {"id": u.id, "nickname": u.nickname, "avatar_url": u.avatar_url}}


# ---------------- http 代理（SSRF 防护） ----------------

def _is_blocked_ip(ip_str: str) -> bool:
    """SSRF 地址判定：私有/环回/链路本地/组播/保留/未指定/云元数据 169.254.169.254 一律拦截"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 无法解析的地址一律拦截
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    # 云元数据（AWS/GCP/Azure 等，169.254.169.254）显式兜底（is_link_local 已覆盖，双保险）
    if ip.version == 4 and str(ip).startswith("169.254."):
        return True
    return False


def _check_url_allowed(url: str) -> str | None:
    """协议/SSRF 校验（纯函数可测）：返回错误 i18n key；None 表示放行"""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return "http_failed"
    if parsed.scheme not in ("http", "https"):
        return "http_scheme_not_allowed"
    if parsed.scheme == "http" and not getattr(settings, "plugin_http_allow_http", False):
        return "http_scheme_not_allowed"
    host = parsed.hostname
    if not host:
        return "http_failed"
    if getattr(settings, "plugin_http_allow_private", False):
        return None  # 显式放行内网（测试/自托管内网服务用）
    try:
        infos = socket.getaddrinfo(
            host, parsed.port or (443 if parsed.scheme == "https" else 80),
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror:
        return "http_failed"
    for info in infos:
        if _is_blocked_ip(info[4][0]):
            return "http_ssrf_blocked"
    return None


def _sanitize_headers(headers: dict | None) -> dict:
    """过滤请求头（纯函数可测）：不转发 Cookie/Authorization，不允许伪造 User-Agent/Host/Content-Length"""
    out: dict = {}
    for k, v in (headers or {}).items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        kl = k.lower()
        if kl in ("cookie", "authorization", "user-agent", "host", "content-length"):
            continue  # 不转发凭据 / 不允许伪造 Host / UA
        out[k] = v
    return out


def _http_fetch(url: str, method: str, data, headers: dict, timeout: float, max_bytes: int) -> dict:
    """同步 urllib 请求（在 to_thread 中调用）：禁重定向；不转发 Cookie/Authorization；
    响应 ≤max_bytes；返回 {"ok": bool, "data": {status,headers,body} | "error"}"""
    hdrs = {"User-Agent": _UA}
    hdrs.update(_sanitize_headers(headers))
    body = None
    if data is not None:
        if isinstance(data, bytes):
            body = data
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            try:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError):
                body = None
        if body is not None and "content-type" not in {k.lower() for k in hdrs}:
            hdrs["Content-Type"] = "application/json"

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            raise urllib.error.HTTPError(url, 302, "redirect blocked", {}, None)

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, data=body, headers=hdrs, method=str(method or "GET").upper())
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return {"ok": True, "data": {"status": 413, "headers": {}, "body": "response too large"}}
            rheaders = {k: v for k, v in resp.headers.items() if k.lower() not in ("set-cookie", "cookie")}
            text = raw.decode("utf-8", errors="replace")
            return {"ok": True, "data": {"status": resp.status, "headers": rheaders, "body": text}}
    except urllib.error.HTTPError as e:
        # 4xx/5xx：状态码 + 响应体对插件可见（不抛错）
        try:
            raw = e.read(max_bytes + 1)
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = ""
        return {"ok": True, "data": {"status": e.code, "headers": {}, "body": text}}
    except Exception as e:
        _logger.warning("plugin bridge http failed %s: %s", url, e)
        return {"ok": False, "error": tr_lang("zh", "http_failed", err=str(e)[:200])}


async def http_proxy(params: dict, lang: str = "zh") -> dict:
    """桥 http 代理入口：仅 https（http 需 debug 开关）；SSRF 防护；禁重定向；超时 10s；响应 ≤2MB"""
    url = str(params.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": tr_lang(lang, "http_failed", err="empty url")}
    err_key = _check_url_allowed(url)
    if err_key:
        return {"ok": False, "error": tr_lang(lang, err_key)}
    method = str(params.get("method") or "GET").upper()
    data = params.get("data")
    headers = params.get("headers")
    if not isinstance(headers, dict):
        headers = {}
    timeout = float(getattr(settings, "plugin_http_timeout", 10.0) or 10.0)
    max_bytes = int(getattr(settings, "plugin_http_max_bytes", 2 * 1024 * 1024) or 2 * 1024 * 1024)
    try:
        return await asyncio.to_thread(_http_fetch, url, method, data, headers, timeout, max_bytes)
    except Exception as e:
        _logger.warning("plugin bridge http to_thread failed %s: %s", url, e)
        return {"ok": False, "error": tr_lang(lang, "http_failed", err=str(e)[:200])}


# ---------------- ai 分发 ----------------

def build_custom_prompt_messages(plugin_name: str, user_input: str, history: object | None) -> list[dict]:
    """自定义 prompt 模式消息组装（纯函数可测）：插件 config.prompt.systemPrompt 作 system（有则注入）+
    history（≤20 条、role 白名单 user/assistant、每条 ≤2000 字符）+ 当前输入"""
    messages: list[dict] = []
    system = _plugin_system_prompt(plugin_name)
    if system:
        messages.append({"role": "system", "content": system})
    if isinstance(history, list):
        for h in history[:20]:
            if not isinstance(h, dict):
                continue
            role = str(h.get("role") or "")
            content = str(h.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content[:2000]})
    messages.append({"role": "user", "content": user_input})
    return messages


def _plugin_system_prompt(plugin_name: str) -> str:
    """取插件 config.prompt.systemPrompt（供自定义 prompt 模式作 system；无则空串）"""
    try:
        from app.plugins import registry
        pl = registry.get_plugin(plugin_name)
        if pl:
            prompt_cfg = (pl.get("config") or {}).get("prompt") or {}
            return str(prompt_cfg.get("systemPrompt") or "").strip()
    except Exception:
        pass
    return ""


async def ai_dispatch(plugin_name: str, params: dict, user_id: int, lang: str = "zh") -> dict:
    """桥 ai：传 aiId → 48b chat_with_character 角色模式；否则自定义 prompt 模式（BYOK 三级回退）"""
    ai_id = params.get("aiId")
    prompt = str(params.get("prompt") or params.get("input") or "").strip()
    history = params.get("history")
    max_tokens = params.get("maxTokens")
    temperature = params.get("temperature")

    if ai_id is not None:
        try:
            ai_id = int(ai_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": tr_lang(lang, "ai_character_not_found")}
        try:
            from app.services.character_chat_api import chat_with_character
            result = await chat_with_character(
                ai_id=ai_id, user_id=user_id, input_text=prompt,
                history=history, max_tokens=max_tokens or 800,
                temperature=temperature if temperature is not None else 0.8,
                lang=lang,
            )
            return {"ok": True, "data": result["reply"]}
        except HTTPException as e:
            return {"ok": False, "error": str(e.detail)}

    # ---- 自定义 prompt 模式（无 aiId）----
    if not prompt:
        return {"ok": False, "error": tr_lang(lang, "ai_chat_input_empty")}
    if len(prompt) > 4000:
        return {"ok": False, "error": tr_lang(lang, "ai_chat_input_too_long")}
    try:
        max_tokens = max(1, min(int(getattr(settings, "plugin_ai_max_tokens", 2000) or 2000), int(max_tokens or 800)))
        temperature = max(0.0, min(1.5, float(temperature if temperature is not None else 0.8)))
    except Exception:
        max_tokens, temperature = 800, 0.8
    messages = build_custom_prompt_messages(plugin_name, prompt, history)
    # BYOK 三级回退：用户 BYOK > 服务器级 DB > .env（chat_completion 内部处理）
    byok = await get_user_llm_config(user_id)
    if getattr(settings, "plugin_ai_require_byok", False) and not byok:
        return {"ok": False, "error": tr_lang(lang, "ai_character_no_byok")}
    try:
        reply = await chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            task=TASK_PLUGIN_AI,  # 记账归因（复用 llm_usage.task）
            user_id=user_id,
            **(byok or {}),
        )
    except Exception as e:
        _logger.warning("plugin bridge ai custom failed %s user=%d: %s", plugin_name, user_id, e)
        return {"ok": False, "error": tr_lang(lang, "ai_llm_failed", err=str(e)[:200])}
    # 输出剥离动作标记（复用 48b 清理链路）
    from app.agent.actions import strip_actions, strip_status_update
    cleaned = strip_status_update(strip_actions(reply or "")).strip()
    if not cleaned:
        cleaned = (reply or "").strip()
    return {"ok": True, "data": cleaned}


# ---------------- 统一分发 ----------------

async def dispatch(plugin_name: str, api: str, params: dict, user_id: int, lang: str = "zh") -> dict:
    """按 api 名分发（含统一入口 call 递归）；返回 {"ok": bool, "data"/"error"}"""
    if api == "call":
        inner = str(params.get("api") or "").strip()
        inner_params = params.get("params")
        if not isinstance(inner_params, dict):
            inner_params = {}
        if inner not in VALID_APIS or inner == "call":
            return {"ok": False, "error": tr_lang(lang, "bridge_api_unknown", api=inner or "(空)")}
        return await dispatch(plugin_name, inner, inner_params, user_id, lang)

    if api == "ai":
        return await ai_dispatch(plugin_name, params, user_id, lang)
    if api == "getAiList":
        try:
            from app.services.character_chat_api import list_characters
            return {"ok": True, "data": await list_characters(user_id)}
        except HTTPException as e:
            return {"ok": False, "error": str(e.detail)}
    if api == "getAiInfo":
        try:
            ai_id = int(params.get("aiId"))
        except (TypeError, ValueError):
            return {"ok": False, "error": tr_lang(lang, "ai_character_not_found")}
        try:
            from app.services.character_chat_api import get_character_detail
            return {"ok": True, "data": await get_character_detail(ai_id, user_id, lang)}
        except HTTPException as e:
            return {"ok": False, "error": str(e.detail)}
    if api == "getUserInfo":
        return await get_user_info(user_id, lang)
    if api == "store.set":
        return await store_set(plugin_name, user_id, str(params.get("key") or ""), params.get("value"), lang)
    if api == "store.get":
        return await store_get(plugin_name, user_id, params.get("key"))
    if api == "http":
        return await http_proxy(params, lang)
    return {"ok": False, "error": tr_lang(lang, "bridge_api_unknown", api=api)}
