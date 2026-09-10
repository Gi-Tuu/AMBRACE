"""系统状态与配置应用服务（F5-c，2026-08-31 自 api/system.py 迁入）。

业务体：局域网探测/BYOK 与服务器级·任务级 LLM 配置/生图·识图·语音配置/
连接测试/token 用量与额度/Feature Flag 运行时开关/备份触发与下载/更新公告解析。
api/system.py 只留 FastAPI 壳（收参→调本模块→返回）+ health/WebSocket 两个纯传输端点 +
历史名字门面重导出（F8 删旧时移除）。

不变量：api_key 永不回传明文、_require_admin 仅主账号、新建配置自动启用语义（方案 A）、
changelog 按天折叠最近 30 天——迁移仅改驻留位置，逻辑逐字节保持。
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import tr_lang
from app.application.permission_service import is_admin_user
from app.utils.logger import get_logger
from app.utils.version import get_project_version

_logger = get_logger("application.system")


def _is_private_ipv4(ip: str) -> bool:
    """判断是否为私网 IPv4（排除回环/链路本地/VPN 虚拟网卡地址）"""
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
        if a.version != 4 or a.is_loopback or a.is_link_local or not a.is_private:
            return False
        # Python 3.13+ 将 198.18.0.0/15（RFC 2544 benchmarking，常见于 VPN 虚拟网卡）判为私网，显式排除
        if a in ipaddress.ip_network("198.18.0.0/15"):
            return False
        return True
    except ValueError:
        return False


def _get_lan_ip() -> str:
    """探测局域网 IP：优先私网 IPv4（排除回环/链路本地/VPN 虚拟网卡），失败返回空串"""
    import socket
    # 1) UDP 默认路由法（不实际发包）：若路由 IP 是私网则直接采用
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if _is_private_ipv4(ip):
            return ip
    except Exception:
        pass
    # 2) 枚举所有网卡 IPv4，按网卡名过滤虚拟/隧道接口后取私网地址（psutil 已在依赖内）
    try:
        import psutil
        skip_keywords = (
            "vethernet", "wsl", "tailscale", "vmware", "virtualbox", "docker",
            "vpn", "tun", "tap", "ppp", "hyper-v", "loopback", "isatap",
            "teredo", "bluetooth",
        )
        candidates: list[str] = []
        for _iface, addrs in psutil.net_if_addrs().items():
            if any(k in _iface.lower() for k in skip_keywords):
                continue
            for a in addrs:
                if a.family == socket.AF_INET and _is_private_ipv4(a.address):
                    candidates.append(a.address)
        if candidates:
            return sorted(candidates)[0]
    except Exception:
        pass
    # 3) 兜底 getaddrinfo（去掉回环）
    try:
        ips = [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None)
               if i[0] == socket.AF_INET and not i[4][0].startswith("127.")]
        return ips[0] if ips else ""
    except Exception:
        return ""


async def _require_admin(user_id: int, lang: str = "zh") -> None:
    if not await is_admin_user(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "admin_config_only"))


def _task_cfg_payload(cfg) -> dict:
    return {
        "enabled": bool(cfg.enabled) if cfg else False,
        "base_url": cfg.base_url if cfg else None,
        "model": cfg.model if cfg else None,
        "provider": getattr(cfg, "provider", None) if cfg else None,
        "has_api_key": bool(cfg.api_key) if cfg else False,
        "configured": bool(cfg),
    }


async def _get_task_cfg(db, user_id: int, task: str):
    from app.models.agent import TaskLlmConfig
    result = await db.execute(
        select(TaskLlmConfig).where(TaskLlmConfig.user_id == user_id, TaskLlmConfig.task == task)
    )
    return result.scalar_one_or_none()


def _changelog_title(rest: str, date: str) -> str:
    """从 `## ` 后的文本提取标题。

    优先级：括号内非日期文本 → 括号外前缀（如 v3.3.9 / 待发布）→ 日期本身。
    """
    import re as _re
    if not rest:
        return date
    paren = _re.search(r"（(.+?)）", rest)
    if paren:
        inner = paren.group(1).strip()
        non_date = _re.sub(r"\d{4}-\d{2}-\d{2}", "", inner).strip("，,、 ")
        if non_date:
            return non_date
        prefix = rest[:paren.start()].strip()
        if prefix:
            return prefix
        return date
    return rest if rest else date


def _parse_changelog(text: str) -> list[dict]:
    """解析 changelog.md 文本，按天折叠（最新在前）。

    兼容标题格式：
    - 旧版：`## 2026-08-28（标题，待发布）`
    - 新版：`## v3.3.9（2026-08-28）` / `## 待发布（2026-08-28）`
    对每行 `## ` 开头用正则提取日期；`cur` 在循环前初始化为 None，首行即表格/无匹配时不 NameError。
    """
    import re as _re

    days_map: dict[str, dict] = {}
    order: list[str] = []
    cur: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("##"):
            dm = _re.search(r"(\d{4}-\d{2}-\d{2})", line)
            if dm is None:
                # 无日期标题（防御）：不挂到条目，避免后续表格行飘到错误日
                cur = None
                continue
            date = dm.group(1)
            title = _changelog_title(line[2:].strip(), date)
            if date not in days_map:
                days_map[date] = {"date": date, "title": title, "items": [], "_sections": 1}
                order.append(date)
            else:
                # 同一天多个标题：合并为一个折叠日，标题标注节数
                days_map[date]["_sections"] += 1
            cur = days_map[date]
            continue
        if cur is None or not line.startswith("|") or line.count("|") < 3:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or cells[0] in ("内容", "---"):
            continue
        if len(cells) >= 2 and cells[0]:
            content = _re.sub(r"\*\*(.+?)\*\*", r"\1", cells[0])
            reason = _re.sub(r"\*\*(.+?)\*\*", r"\1", cells[1]) if len(cells) >= 2 else ""
            cur["items"].append({"content": content, "reason": reason})
    # 组装：同一天标题加节数标注；只展示最近 30 天，避免过长
    out: list[dict] = []
    for date in order:
        entry = days_map[date]
        sections = entry.pop("_sections", 1)
        if sections > 1:
            entry["title"] = f"{entry['title']}（{sections} 节）"
        out.append(entry)
    return out[:30]


def _load_backup_module():
    """按文件路径加载 scripts/backup.py（repo 根不一定在 sys.path，故显式按路径导入）。

    返回的模块带 .BACKUP_ROOT / .do_backup()，与脚本命令行同一实现（单一数据源）。
    """
    from pathlib import Path as _P
    import importlib.util as _ilu
    path = _P(__file__).resolve().parents[3] / "scripts" / "backup.py"
    spec = _ilu.spec_from_file_location("ambrace_backup", str(path))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _backup_info(zip_path: str) -> dict:
    import os as _os
    size = _os.path.getsize(zip_path) if _os.path.isfile(zip_path) else 0
    created = _os.path.getmtime(zip_path) if _os.path.isfile(zip_path) else 0
    return {
        "path": _os.path.basename(zip_path),
        "size": size,
        "created_at": datetime.fromtimestamp(created).isoformat() if created else None,
    }


async def system_status(
):
    """服务器运行状态（含局域网 IP 与图片理解配置状态，便于部署者填手机端服务器地址）"""
    from app.config import settings
    return {
        "server": "AMBRACE Server",
        "version": get_project_version(),
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lan_ip": _get_lan_ip(),
        "vlm": {
            "enabled": bool(settings.vlm_enabled),
            "cloud_api_key_configured": bool(settings.vlm_api_key),
            "base_url": settings.vlm_base_url,
            "model": settings.vlm_model,
        },
    }


async def get_api_config(
    db: AsyncSession,
    user_id: int,
):
    """读取用户级 API 配置（api_key 不回传明文）"""
    from sqlalchemy import select
    from app.models.config import ApiConfig
    result = await db.execute(select(ApiConfig).where(ApiConfig.user_id == user_id))
    cfg = result.scalar_one_or_none()
    if not cfg:
        return {"enabled": False, "base_url": None, "model": None, "provider": None, "has_api_key": False, "configured": False}
    return {
        "enabled": bool(cfg.enabled),
        "base_url": cfg.base_url,
        "model": cfg.model,
        "provider": getattr(cfg, "provider", None),
        "has_api_key": bool(cfg.api_key),
        "configured": True,
    }


async def update_api_config(
    db: AsyncSession,
    data: dict,
    user_id: int,
):
    """写入用户级 API 配置（BYOK：聊天主链路启用后优先于服务器默认）"""
    from sqlalchemy import select
    from app.models.config import ApiConfig
    result = await db.execute(select(ApiConfig).where(ApiConfig.user_id == user_id))
    cfg = result.scalar_one_or_none()
    is_new = cfg is None
    if cfg is None:
        cfg = ApiConfig(user_id=user_id)
        db.add(cfg)
        await db.flush()
    if "base_url" in data:
        cfg.base_url = (data.get("base_url") or "").strip() or None
    if "api_key" in data:
        cfg.api_key = (data.get("api_key") or "").strip() or None
    if "model" in data:
        cfg.model = (data.get("model") or "").strip() or None
    if "provider" in data:
        cfg.provider = (data.get("provider") or "").strip() or None
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    # 方案 A：新建配置且传了 api_key 但未显式传 enabled 时，自动启用（避免 Key 存了不生效）
    if is_new and "enabled" not in data and cfg.api_key:
        cfg.enabled = True
    await db.commit()
    _logger.info("api-config updated user=%d enabled=%s base_url=%s provider=%s", user_id, bool(cfg.enabled), cfg.base_url, cfg.provider)
    return {"status": "ok", "enabled": bool(cfg.enabled), "configured": True}


async def get_server_api_config(
    db: AsyncSession,
    user_id: int,
    lang: str,
):
    """读取服务器级全局 API 配置（api_key 不回传明文）"""
    await _require_admin(user_id, lang)
    from app.models.config import ApiConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    result = await db.execute(select(ApiConfig).where(ApiConfig.user_id == SERVER_CONFIG_UID))
    cfg = result.scalar_one_or_none()
    if not cfg:
        return {"enabled": False, "base_url": None, "model": None, "provider": None, "has_api_key": False, "configured": False}
    return {
        "enabled": bool(cfg.enabled),
        "base_url": cfg.base_url,
        "model": cfg.model,
        "provider": getattr(cfg, "provider", None),
        "has_api_key": bool(cfg.api_key),
        "configured": True,
    }


async def update_server_api_config(
    db: AsyncSession,
    data: dict,
    user_id: int,
    lang: str,
):
    """写入服务器级全局 API 配置（影响所有未配 BYOK 的调用；仅主账号）"""
    await _require_admin(user_id, lang)
    from app.models.config import ApiConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    result = await db.execute(select(ApiConfig).where(ApiConfig.user_id == SERVER_CONFIG_UID))
    cfg = result.scalar_one_or_none()
    is_new = cfg is None
    if cfg is None:
        cfg = ApiConfig(user_id=SERVER_CONFIG_UID)
        db.add(cfg)
        await db.flush()
    if "base_url" in data:
        cfg.base_url = (data.get("base_url") or "").strip() or None
    if "api_key" in data:
        cfg.api_key = (data.get("api_key") or "").strip() or None
    if "model" in data:
        cfg.model = (data.get("model") or "").strip() or None
    if "provider" in data:
        cfg.provider = (data.get("provider") or "").strip() or None
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    # 方案 A：新建配置且传了 api_key 但未显式传 enabled 时，自动启用（避免 Key 存了不生效）
    if is_new and "enabled" not in data and cfg.api_key:
        cfg.enabled = True
    await db.commit()
    _logger.info("server api-config updated user=%d enabled=%s base_url=%s provider=%s", user_id, bool(cfg.enabled), cfg.base_url, cfg.provider)
    return {"status": "ok", "enabled": bool(cfg.enabled), "configured": True}


async def get_task_llm_catalog(
    user_id: int,
):
    """任务目录（供前端渲染任务选择）"""
    from app.agent.llm_client import TASK_LLM_CATALOG
    return {"tasks": TASK_LLM_CATALOG}


async def get_task_api_config(
    db: AsyncSession,
    task: str,
    user_id: int,
):
    """读取用户级任务 LLM 配置（api_key 不回传明文）"""
    cfg = await _get_task_cfg(db, user_id, task)
    return _task_cfg_payload(cfg)


async def update_task_api_config(
    db: AsyncSession,
    task: str,
    data: dict,
    user_id: int,
):
    """写入用户级任务 LLM 配置（upsert）"""
    from app.models.agent import TaskLlmConfig
    cfg = await _get_task_cfg(db, user_id, task)
    if cfg is None:
        cfg = TaskLlmConfig(user_id=user_id, task=task)
        db.add(cfg)
        await db.flush()
    if "base_url" in data:
        cfg.base_url = (data.get("base_url") or "").strip() or None
    if "api_key" in data:
        cfg.api_key = (data.get("api_key") or "").strip() or None
    if "model" in data:
        cfg.model = (data.get("model") or "").strip() or None
    if "provider" in data:
        cfg.provider = (data.get("provider") or "").strip() or None
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    await db.commit()
    _logger.info("task api-config updated user=%d task=%s enabled=%s model=%s", user_id, task, bool(cfg.enabled), cfg.model)
    return {"status": "ok", "task": task, "enabled": bool(cfg.enabled), "configured": True}


async def get_server_task_api_config(
    db: AsyncSession,
    task: str,
    user_id: int,
    lang: str,
):
    """读取服务器级任务 LLM 配置（仅主账号）"""
    await _require_admin(user_id, lang)
    from app.agent.llm_client import SERVER_CONFIG_UID
    cfg = await _get_task_cfg(db, SERVER_CONFIG_UID, task)
    return _task_cfg_payload(cfg)


async def update_server_task_api_config(
    db: AsyncSession,
    task: str,
    data: dict,
    user_id: int,
    lang: str,
):
    """写入服务器级任务 LLM 配置（仅主账号；影响所有用户的该任务调用）"""
    await _require_admin(user_id, lang)
    from app.models.agent import TaskLlmConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    cfg = await _get_task_cfg(db, SERVER_CONFIG_UID, task)
    if cfg is None:
        cfg = TaskLlmConfig(user_id=SERVER_CONFIG_UID, task=task)
        db.add(cfg)
        await db.flush()
    if "base_url" in data:
        cfg.base_url = (data.get("base_url") or "").strip() or None
    if "api_key" in data:
        cfg.api_key = (data.get("api_key") or "").strip() or None
    if "model" in data:
        cfg.model = (data.get("model") or "").strip() or None
    if "provider" in data:
        cfg.provider = (data.get("provider") or "").strip() or None
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    await db.commit()
    _logger.info("server task api-config updated task=%s enabled=%s model=%s", task, bool(cfg.enabled), cfg.model)
    return {"status": "ok", "task": task, "enabled": bool(cfg.enabled), "configured": True}


# 连接测试支持的模态 → 错误文案里的中文标签（非法/缺失 modality 一律回落 llm）
_MODALITY_LABELS = {
    "llm": "聊天(LLM)", "task": "任务", "vlm": "识图(VLM)", "image": "生图", "speech": "语音(TTS)",
}


async def test_api_connection(
    body: dict,
    user_id: int,
):
    """连接测试：按模态（modality）选择最小探测请求，校验 {base_url, api_key, model}。

    modality 取值：llm（默认）/ task / vlm / image / speech；缺省或非法值回落 llm
    （老 App 不带该字段 → 行为与改动前逐字节一致）。
    - llm / task / vlm：chat.completions 最小请求（识图为多模态 chat，纯文本 hi 可用）；
    - image：先 GET /v1/models 零成本探测，网关不支持时按 provider 退一次最小真实生图；
    - speech：先 OpenAI 兼容 /v1/audio/speech，不通再按 tts_service 端点规则打百炼私有端点。
    api_key 为空则回退服务器级全局配置（沿用旧行为）。多 Key 逐个尝试，任一成功即 ok。
    """
    import time
    from app.agent.llm_client import (
        get_llm_client, get_server_llm_config, _split_api_keys,
    )
    modality = (body.get("modality") or "llm").strip().lower()
    if modality not in _MODALITY_LABELS:
        modality = "llm"
    base_url = (body.get("base_url") or "").strip()
    # P1 安全加固（2026-08-16）：仅允许 http/https 协议，防 file:// 等 SSRF
    if base_url and not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="base_url must be http(s)")
    api_key = (body.get("api_key") or "").strip()
    model = (body.get("model") or "").strip()
    provider = (body.get("provider") or "").strip()
    if not api_key and not base_url:
        srv = await get_server_llm_config()
        if srv:
            base_url = srv.get("base_url") or ""
            api_key = srv.get("api_key") or ""
            model = srv.get("model") or model
    keys = _split_api_keys(api_key)
    if not keys:
        return {"ok": False, "error": "未提供 API Key（可留空使用服务器级全局配置）"}
    if not base_url:
        return {"ok": False, "error": "未提供 Base URL"}
    last_err = "连接失败"
    for key in keys:
        try:
            client = get_llm_client(api_key=key, base_url=base_url)
            t0 = time.monotonic()
            if modality == "image":
                probe, used_model = await _probe_image(client, key, base_url, model, provider)
            elif modality == "speech":
                probe, used_model = await _probe_speech(client, key, base_url, model, provider)
            else:  # llm / task / vlm：统一走 chat 最小请求
                probe, used_model = await _probe_chat(client, model or "gpt-4o-mini")
            latency_ms = int((time.monotonic() - t0) * 1000)
            return {"ok": True, "model": used_model, "latency_ms": latency_ms,
                    "api_key_tail": key[-6:] if len(key) > 6 else key, "provider": provider or None,
                    "modality": modality, "probe": probe}
        except Exception as e:
            last_err = _classify_probe_error(e, modality)
    return {"ok": False, "error": last_err, "modality": modality}


# ── 各模态最小探测（失败抛异常，由 test_api_connection 统一分类）──────────────────

async def _probe_chat(client, model: str) -> tuple[str, str]:
    """LLM / 任务 / 识图：chat.completions 最小请求（识图模型本身即多模态 chat）。"""
    import asyncio
    await asyncio.wait_for(
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        ),
        timeout=30,
    )
    return "chat_completions", model


async def _probe_models_list(client, model: str) -> tuple[str, str]:
    """零成本探测：GET /v1/models 校验地址 + 鉴权（+ 模型可见性）。

    鉴权失败（401/403）直接抛异常（上层判 Key 错误，不再兜底）；其它失败由调用方兜底。
    """
    import asyncio
    resp = await asyncio.wait_for(client.models.list(), timeout=20)
    ids: list[str] = []
    try:
        ids = [i for i in (getattr(m, "id", "") or "" for m in (resp.data or [])) if i]
    except Exception:
        ids = []
    if model and ids and model not in ids:
        # 网关回了列表但不含该模型：多数中转不回全量，不冤判，仅备注
        return "models_list(模型列表未包含该模型，多数中转不回全量，已按鉴权通过处理)", model
    return "models_list", model


async def _probe_image(client, api_key: str, base_url: str, model: str, provider: str) -> tuple[str, str]:
    """生图探测：优先零成本 /v1/models；网关不支持（404 等）时按 provider 退一次最小真实生图。"""
    import asyncio
    try:
        return await _probe_models_list(client, model)
    except Exception as e:
        if _status_code_of(e) in (401, 403):
            raise  # Key 问题，直接判失败
    if provider.lower() == "dashscope":
        return await _probe_image_dashscope(api_key, base_url, model)
    await asyncio.wait_for(
        client.images.generate(
            model=model,
            prompt="a minimal red dot",
            n=1,
            size="1024x1024",
        ),
        timeout=60,
    )
    return "images_generates(已实际生成一张测试图)", model


async def _probe_image_dashscope(api_key: str, base_url: str, model: str) -> tuple[str, str]:
    """百炼 qwen-image：POST {base_url}/chat/completions，content 列表格式（与 DashScopeChatImageProvider 一致）。"""
    import httpx
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "."}]}],
    }
    async with httpx.AsyncClient(proxy=None, timeout=60) as http:
        r = await http.post(
            url,
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json=payload,
        )
        r.raise_for_status()
    return "dashscope_chat_image(已实际生成一张测试图)", model


async def _probe_speech(client, api_key: str, base_url: str, model: str, provider: str) -> tuple[str, str]:
    """语音(TTS)最小探测：① OpenAI 兼容 /v1/audio/speech；② 百炼私有 multimodal-generation 端点。

    端点规则复用 tts_service._tts_endpoints（与真实合成链路同源），但只打与所配 base_url 同主机的
    端点（_tts_endpoints 另附的公开兜底主机在检测场景不探，避免把用户 Key 发到无关第三方）。
    两者都不通时给友好提示（语音链路有 edge-tts 兜底，可保存后用「试听」最终确认），不抛生硬 503。
    """
    import asyncio
    import httpx
    from urllib.parse import urlparse

    from app.application.tts_service import _tts_endpoints

    cfg_netloc = urlparse(base_url).netloc
    errs: list[str] = []
    # ① OpenAI 兼容 audio.speech（部分网关提供）
    try:
        await asyncio.wait_for(
            client.audio.speech.create(model=model, voice="alloy", input="你好", response_format="mp3"),
            timeout=30,
        )
        return "audio_speech", model
    except Exception as e:
        if _status_code_of(e) in (401, 403):
            raise
        errs.append(str(e)[:120])

    # ② 百炼私有 TTS 端点
    for endpoint, _models in _tts_endpoints({"base_url": base_url, "model": model}):
        if urlparse(endpoint).netloc != cfg_netloc:
            continue
        try:
            async with httpx.AsyncClient(proxy=None, timeout=30) as http:
                r = await http.post(
                    endpoint,
                    headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "input": {"text": "你好", "voice": "Cherry"},
                        "parameters": {"format": "mp3", "sample_rate": 24000},
                    },
                )
            if r.status_code in (401, 403):
                r.raise_for_status()
            if r.status_code == 200:
                data = r.json()
                audio_url = str((((data or {}).get("output") or {}).get("audio") or {}).get("url") or "")
                if audio_url.startswith("http"):
                    return "dashscope_tts", model
            r.raise_for_status()
        except Exception as e:
            if _status_code_of(e) in (401, 403):
                raise
            errs.append(str(e)[:120])
    raise RuntimeError(
        "语音(TTS)检测未通过（%s）。语音链路较特殊（百炼私有端点 + edge-tts 兜底），"
        "可保存后用角色编辑页「试听」做最终确认；ASR 不在本检测范围。" % (errs[-1] if errs else "端点未返回音频")
    )


def _status_code_of(exc: Exception) -> int | None:
    """取异常携带的 HTTP 状态码：openai SDK 挂 status_code，httpx 挂 response.status_code。"""
    sc = getattr(exc, "status_code", None)
    if isinstance(sc, int):
        return sc
    resp = getattr(exc, "response", None)
    sc = getattr(resp, "status_code", None)
    return sc if isinstance(sc, int) else None


def _classify_probe_error(exc: Exception, modality: str) -> str:
    """把底层异常翻译成可读中文，重点消除「生图/语音模型被 chat 探测」这类假失败。"""
    sc = _status_code_of(exc)
    msg = str(exc) or repr(exc)
    low = msg.lower()
    label = _MODALITY_LABELS.get(modality, modality)
    if msg.startswith("语音(TTS)检测未通过"):
        return msg[:300]  # _probe_speech 已给出友好结论，不再二次分类
    if sc in (401, 403):
        return f"API Key 无效或无权限（HTTP {sc}）：请检查 Key 是否正确、是否已开通{label}能力。"
    if sc == 404:
        if ("model" in low and "exist" in low) or "unknown model" in low or "model not" in low:
            return f"模型不存在或该网关无此模型（404）：请确认模型名拼写、该服务是否提供此模型。原始信息：{msg[:180]}"
        return (f"接口路径不存在（404）：请确认 Base URL 是否应包含 /v1，且与「{label}」标签页匹配；"
                f"生图/语音模型不能填到聊天地址上。原始信息：{msg[:160]}")
    if sc == 503 or "only supported on" in low or "images/generations" in low or "images/edits" in low:
        return (f"模型与模态不匹配（HTTP {sc or 503}）：该模型不支持当前检测所用接口。"
                f"生图模型应在「生图」标签页检测（已自动改走生图接口）；若仍报错，请确认 provider 与模型名。"
                f"原始信息：{msg[:180]}")
    if sc == 400:
        return f"请求被网关判定为参数错误（400）：多为模型名/规格不匹配。原始信息：{msg[:200]}"
    if sc == 429:
        return "触发限流/额度不足（429）：请稍后重试或检查账户额度。"
    if "timeout" in low or "timed out" in low or "connect" in low or "unreachable" in low:
        return f"连接超时或无法到达 Base URL：请检查网络/代理/地址是否正确。原始信息：{msg[:160]}"
    return msg[:300]


async def get_image_gen_server_config(
    db: AsyncSession,
    user_id: int,
    lang: str,
):
    """读取服务器级生图配置（api_key 不回传明文）"""
    await _require_admin(user_id, lang)
    from app.models.life import ImageGenConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    result = await db.execute(select(ImageGenConfig).where(ImageGenConfig.user_id == SERVER_CONFIG_UID))
    cfg = result.scalar_one_or_none()
    if not cfg:
        return {"enabled": False, "provider": None, "base_url": None, "model": None,
                "has_api_key": False, "daily_limit": 10, "configured": False}
    return {
        "enabled": bool(cfg.enabled),
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "has_api_key": bool(cfg.api_key),
        "daily_limit": cfg.daily_limit or 10,
        "configured": True,
    }


async def update_image_gen_server_config(
    db: AsyncSession,
    data: dict,
    user_id: int,
    lang: str,
):
    """写入服务器级生图配置（影响聊天内 AI 发图与 /images 接口；仅主账号）"""
    await _require_admin(user_id, lang)
    from app.models.life import ImageGenConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    result = await db.execute(select(ImageGenConfig).where(ImageGenConfig.user_id == SERVER_CONFIG_UID))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = ImageGenConfig(user_id=SERVER_CONFIG_UID)
        db.add(cfg)
        await db.flush()
    for field in ("provider", "base_url", "api_key", "model"):
        if field in data:
            setattr(cfg, field, (data.get(field) or "").strip() or None)
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    if "daily_limit" in data:
        try:
            cfg.daily_limit = max(1, int(data.get("daily_limit")))
        except (TypeError, ValueError):
            cfg.daily_limit = 10
    await db.commit()
    _logger.info("server image-gen-config updated user=%d enabled=%s provider=%s", user_id, bool(cfg.enabled), cfg.provider)
    return {"status": "ok", "enabled": bool(cfg.enabled), "configured": True}


async def get_vlm_server_config(
    db: AsyncSession,
    user_id: int,
    lang: str,
):
    """读取服务器级识图配置（api_key 不回传明文）"""
    await _require_admin(user_id, lang)
    from app.models.config import VlmConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    result = await db.execute(select(VlmConfig).where(VlmConfig.user_id == SERVER_CONFIG_UID))
    cfg = result.scalar_one_or_none()
    if not cfg:
        return {"enabled": False, "base_url": None, "model": None, "has_api_key": False, "configured": False}
    return {
        "enabled": bool(cfg.enabled),
        "base_url": cfg.base_url,
        "model": cfg.model,
        "has_api_key": bool(cfg.api_key),
        "configured": True,
    }


async def update_vlm_server_config(
    db: AsyncSession,
    data: dict,
    user_id: int,
    lang: str,
):
    """写入服务器级识图配置（影响聊天/手机感知等图片理解；仅主账号）"""
    await _require_admin(user_id, lang)
    from app.models.config import VlmConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    result = await db.execute(select(VlmConfig).where(VlmConfig.user_id == SERVER_CONFIG_UID))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = VlmConfig(user_id=SERVER_CONFIG_UID)
        db.add(cfg)
        await db.flush()
    for field in ("base_url", "api_key", "model"):
        if field in data:
            setattr(cfg, field, (data.get(field) or "").strip() or None)
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    await db.commit()
    _logger.info("server vlm-config updated user=%d enabled=%s base_url=%s", user_id, bool(cfg.enabled), cfg.base_url)
    return {"status": "ok", "enabled": bool(cfg.enabled), "configured": True}


async def get_speech_server_config(
    db: AsyncSession,
    user_id: int,
    lang: str,
):
    """读取服务器级语音大模型配置（api_key 不回传明文）"""
    await _require_admin(user_id, lang)
    from app.models.config import SpeechConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    result = await db.execute(select(SpeechConfig).where(SpeechConfig.user_id == SERVER_CONFIG_UID))
    cfg = result.scalar_one_or_none()
    if not cfg:
        return {"enabled": False, "provider": None, "base_url": None, "model": None,
                "has_api_key": False, "configured": False}
    return {
        "enabled": bool(cfg.enabled),
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "has_api_key": bool(cfg.api_key),
        "configured": True,
    }


async def update_speech_server_config(
    db: AsyncSession,
    data: dict,
    user_id: int,
    lang: str,
):
    """写入服务器级语音大模型配置（仅主账号）"""
    await _require_admin(user_id, lang)
    from app.models.config import SpeechConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    result = await db.execute(select(SpeechConfig).where(SpeechConfig.user_id == SERVER_CONFIG_UID))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = SpeechConfig(user_id=SERVER_CONFIG_UID)
        db.add(cfg)
        await db.flush()
    for field in ("provider", "base_url", "api_key", "model"):
        if field in data:
            setattr(cfg, field, (data.get(field) or "").strip() or None)
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    await db.commit()
    _logger.info("server speech-config updated user=%d enabled=%s provider=%s", user_id, bool(cfg.enabled), cfg.provider)
    return {"status": "ok", "enabled": bool(cfg.enabled), "configured": True}


async def speech_preview(
    data: dict,
    user_id: int,
    lang: str,
):
    """音色试听：用固定基础文案合成当前音色/语速/语调，返回音频 URL（角色编辑页试听用）"""
    from app.application.tts_service import synthesize
    text = "你好呀，我是你的AI伙伴，很高兴认识你。"
    url = await synthesize(
        text,
        subdir="preview",
        gender=(data.get("gender") or "").strip() or None,
        voice=(data.get("voice") or "").strip() or None,
        voice_rate=float(data.get("voice_rate") or 1.0),
        voice_pitch=float(data.get("voice_pitch") or 0.0),
    )
    if not url:
        _logger.warning("speech preview failed user=%d", user_id)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "tts_failed"))
    return {"url": url}


async def get_updates(
):
    """更新公告：解析 docs/changelog.md，按天折叠（最新在前），供 app 内「更新公告」页展示"""
    from pathlib import Path

    changelog_path = Path(__file__).resolve().parents[3] / "docs" / "changelog.md"
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except Exception as e:
        _logger.warning("Changelog read failed: %s", e)
        return {"days": []}
    return {"days": _parse_changelog(text)}


async def get_llm_usage(
    user_id: int,
):
    """token 用量统计：今日/近7天/本月/累计 + 按模型汇总 + 剩余额度（#68 P6 组聚合）

    主账号：统计范围 = 自己 + 直属子账号（user_id IN family_member_ids）+ user_id IS NULL 服务器级行；
    子账号：仅统计自己，不返回 by_user。
    """
    from sqlalchemy import or_
    from app.db.database import async_session_factory
    from app.models.agent import LlmUsage, LlmUsageLimit
    from app.models.user import User
    from app.application.family_service import is_sub_account, get_family_member_ids

    # B-TZ 修复（2026-09-01 审查）：库里 created_at 是 UTC naive（func.now()）。
    # 窗口按用户本地日历语义切，再统一转成 UTC naive 参与比较——否则本地
    # 00:00-08:00 的"今日/本月"会错窗（本地零点=前一天 16:00 UTC）。
    from datetime import timezone as _tz

    def _to_utc_naive(local_naive: datetime) -> datetime:
        return local_naive.astimezone(_tz.utc).replace(tzinfo=None)

    now_local = datetime.now()
    today0 = _to_utc_naive(datetime(now_local.year, now_local.month, now_local.day))
    week0 = today0 - timedelta(days=6)
    month0 = _to_utc_naive(datetime(now_local.year, now_local.month, 1))

    async with async_session_factory() as db:
        is_sub = await is_sub_account(db, user_id)
        if is_sub:
            scope_ids = [user_id]
            include_server = False
        else:
            scope_ids = await get_family_member_ids(db, user_id)
            include_server = True
        cond = LlmUsage.user_id.in_(scope_ids)
        if include_server:
            cond = or_(cond, LlmUsage.user_id.is_(None))
        rows = (await db.execute(select(LlmUsage).where(cond))).scalars().all()
        limit_row = (await db.execute(
            select(LlmUsageLimit).where(LlmUsageLimit.id == 1)
        )).scalar_one_or_none()
        nickname_map: dict[int, str] = {}
        if not is_sub and scope_ids:
            users = (await db.execute(select(User).where(User.id.in_(scope_ids)))).scalars().all()
            nickname_map = {u.id: (u.nickname or u.username or str(u.id)) for u in users}

    total = today = week = month = 0
    by_model: dict[str, int] = {}
    by_user_map: dict[int, int] = {}
    for r in rows:
        t = r.total_tokens or 0
        total += t
        created = r.created_at
        if created:
            if created >= today0:
                today += t
            if created >= week0:
                week += t
            if created >= month0:
                month += t
        if r.model:
            by_model[r.model] = by_model.get(r.model, 0) + t
        if r.user_id is not None:
            by_user_map[r.user_id] = by_user_map.get(r.user_id, 0) + t

    by_user = []
    if not is_sub:
        by_user = [
            {"user_id": uid, "nickname": nickname_map.get(uid, str(uid)), "total": by_user_map.get(uid, 0)}
            for uid in scope_ids
            if by_user_map.get(uid, 0) > 0
        ]

    limit = limit_row.total_limit if limit_row else 0
    remaining = (limit - total) if (limit and limit > 0) else None
    return {
        "total_limit": limit,
        "used_total": total,
        "remaining": remaining,
        "today": today,
        "week": week,
        "month": month,
        "by_model": [{"model": k, "total": v}
                     for k, v in sorted(by_model.items(), key=lambda kv: -kv[1])],
        "by_user": by_user,
        "can_edit_limit": await is_admin_user(user_id),
    }


async def update_llm_usage_limit(
    body: dict,
    user_id: int,
    lang: str,
):
    """设置免费额度总量（tokens，仅主账号；0=清除总额设置）"""
    await _require_admin(user_id, lang)
    from app.db.database import async_session_factory
    from app.models.agent import LlmUsageLimit
    try:
        limit = max(0, int(body.get("total_limit") or 0))
    except Exception:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "total_limit_invalid"))
    async with async_session_factory() as db:
        row = (await db.execute(
            select(LlmUsageLimit).where(LlmUsageLimit.id == 1)
        )).scalar_one_or_none()
        if row is None:
            db.add(LlmUsageLimit(id=1, total_limit=limit, updated_by=user_id))
        else:
            row.total_limit = limit
            row.updated_by = user_id
        await db.commit()
    return {"total_limit": limit}


async def get_feature_flags(
    user_id: int,
    lang: str,
):
    '''读取全部运行时 Feature Flag（主账号）；source: db=DB 覆盖 / default=硬编码默认'''
    await _require_admin(user_id, lang)
    from app.application import flag_service
    return {'status': 'ok', 'flags': await flag_service.get_all_flags()}


async def update_feature_flag(
    key: str,
    data: dict,
    user_id: int,
    lang: str,
):
    '''切换 Feature Flag（主账号）：写 DB + 热更新内存立即生效；未知 key 返回 404'''
    await _require_admin(user_id, lang)
    if 'enabled' not in data:
        raise HTTPException(status_code=400, detail='enabled required')
    from app.application import flag_service
    ok = await flag_service.set_runtime_flag(key, bool(data.get('enabled')))
    if not ok:
        raise HTTPException(status_code=404, detail='unknown feature flag: ' + key)
    return {'status': 'ok', 'key': key, 'enabled': bool(data.get('enabled'))}


async def trigger_backup(
    user_id: int,
    lang: str,
):
    """触发一次备份（数据库 + 配置 + 源码快照），仅主账号。

    当天已有备份（如多次调用）则直接返回现有文件信息；返回 {path, size, created_at}。
    """
    await _require_admin(user_id, lang)
    import os as _os
    mod = _load_backup_module()
    try:
        # do_backup：运行中库用 SQLite backup API 安全复制，并做日志轮换 / 过期备份清理
        mod.do_backup()
    except Exception as e:
        _logger.error("backup triggered failed: %s", e)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "backup_failed"))
    today = datetime.now().strftime("%Y%m%d")
    zip_path = _os.path.join(mod.BACKUP_ROOT, f"{today}.zip")
    if not _os.path.isfile(zip_path):
        raise HTTPException(status_code=500, detail=tr_lang(lang, "backup_failed"))
    return {"status": "ok", **_backup_info(zip_path)}


async def download_backup(
    user_id: int,
    lang: str,
):
    """下载当天 / 最近一份备份 zip（仅主账号）；文件名用 ascii 安全名。"""
    await _require_admin(user_id, lang)
    import os as _os
    from fastapi.responses import FileResponse
    mod = _load_backup_module()
    candidate = None
    today = datetime.now().strftime("%Y%m%d")
    today_zip = _os.path.join(mod.BACKUP_ROOT, f"{today}.zip")
    if _os.path.isfile(today_zip):
        candidate = today_zip
    else:
        try:
            zips = [f for f in _os.listdir(mod.BACKUP_ROOT) if f.endswith(".zip")]
            if zips:
                zips.sort(reverse=True)
                candidate = _os.path.join(mod.BACKUP_ROOT, zips[0])
        except Exception as e:
            _logger.warning("backup download list failed: %s", e)
            candidate = None
    if not candidate or not _os.path.isfile(candidate):
        raise HTTPException(status_code=404, detail=tr_lang(lang, "backup_not_found"))
    ascii_name = "ambrace-backup-" + _os.path.basename(candidate).replace(".zip", "") + ".zip"
    return FileResponse(candidate, media_type="application/zip", filename=ascii_name)

