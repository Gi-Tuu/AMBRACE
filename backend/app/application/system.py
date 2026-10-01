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
from app.utils.timeutil import app_local_now
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


async def _require_server_admin(user_id: int, lang: str = "zh") -> None:
    """服务器控制台管理员门禁（账号独立 P2，契约 §3）：写类服务器级端点用。

    ``is_admin``（家庭主账号，家庭内管理）与 ``server_admin``（服务器控制台管理员，跨家庭管
    服务器级配置）分离后，**写类**服务器级端点收紧为 server_admin（非 server_admin → 403）；
    **读类**（get_*）保持既有 _require_admin（is_admin），避免非 server_admin 账号的 App 页面
    因读接口 403 报错。判定走 permission_service.is_server_admin（DB 权威 + 30s 缓存 + env 兜底）。
    """
    from app.application.permission_service import is_server_admin
    if not await is_server_admin(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "admin_config_only"))


# 服务器级配置审计快照字段（api_key 由 admin_audit_service._dump 统一脱敏为 "***"，不进审计明文）
_API_CFG_FIELDS = ("base_url", "api_key", "model", "provider", "enabled")
_VLM_CFG_FIELDS = ("base_url", "api_key", "model", "enabled")
_IMAGE_CFG_FIELDS = ("provider", "base_url", "api_key", "model", "enabled", "daily_limit")
_SPEECH_CFG_FIELDS = ("provider", "base_url", "api_key", "model", "enabled")


def _cfg_snapshot(cfg, fields) -> dict:
    """配置行 → 审计快照（None=无行；api_key 只留是否已配置，脱敏由 admin_audit_service 兜底）。"""
    if cfg is None:
        return {"configured": False}
    out = {"configured": True}
    for f in fields:
        v = getattr(cfg, f, None)
        if f == "api_key":
            v = "***" if v else None
        out[f] = v
    return out


async def _audit(db, actor_user_id, action: str, target: str | None = None,
                 before=None, after=None) -> None:
    """控制台写动作审计（契约 §0/§1.5）；fail-open，见 app/application/admin_audit_service。"""
    from app.application.admin_audit_service import record
    await record(db, actor_user_id, action, target, before, after)


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


async def system_status_public() -> dict:
    """匿名可见的最小状态：在线布尔 + 版本，不含局域网 IP / 内网 base_url。

    P3-B：公开 /status 只回这四项；完整版 system_status()（含 lan_ip / vlm）挪到需登录的
    GET /status/detail。
    """
    return {
        "server": "AMBRACE Server",
        "version": get_project_version(),
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def system_status(
):
    """服务器运行状态（完整版，含局域网 IP 与图片理解配置状态）——仅经鉴权端点回传。"""
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
    from app.application.llm_config_service import get_server_modality_row
    cfg = await get_server_modality_row(db, "llm")  # 四模态统一出口（账号独立 P1）
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
    """写入服务器级全局 API 配置（影响所有未配 BYOK 的调用；仅服务器控制台管理员）"""
    await _require_server_admin(user_id, lang)
    from app.application.llm_config_service import (
        get_or_create_server_modality_row,
        get_server_modality_row,
    )
    cfg = await get_server_modality_row(db, "llm")  # 四模态统一出口（账号独立 P1）
    is_new = cfg is None
    _before = _cfg_snapshot(cfg, _API_CFG_FIELDS)
    if cfg is None:
        cfg = await get_or_create_server_modality_row(db, "llm")
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
    await _audit(db, user_id, "server.api_config.update", "modality:llm",
                 _before, _cfg_snapshot(cfg, _API_CFG_FIELDS))
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
    """写入服务器级任务 LLM 配置（仅服务器控制台管理员；影响所有用户的该任务调用）"""
    await _require_server_admin(user_id, lang)
    from app.models.agent import TaskLlmConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    cfg = await _get_task_cfg(db, SERVER_CONFIG_UID, task)
    _before = _cfg_snapshot(cfg, _API_CFG_FIELDS)
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
    await _audit(db, user_id, "server.task_api_config.update", "task:" + task,
                 _before, _cfg_snapshot(cfg, _API_CFG_FIELDS))
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
    from app.application.llm_config_service import get_server_modality_row
    cfg = await get_server_modality_row(db, "image")  # 四模态统一出口（账号独立 P1）
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
    """写入服务器级生图配置（影响聊天内 AI 发图与 /images 接口；仅服务器控制台管理员）"""
    await _require_server_admin(user_id, lang)
    from app.application.llm_config_service import (
        get_or_create_server_modality_row,
        get_server_modality_row,
    )
    # 审计前置快照：先读既有行（无行不建行，避免只读动作顺手造哨兵行）
    _before = _cfg_snapshot(await get_server_modality_row(db, "image"), _IMAGE_CFG_FIELDS)
    cfg = await get_or_create_server_modality_row(db, "image")  # 四模态统一出口（账号独立 P1）
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
    await _audit(db, user_id, "server.image_gen_config.update", "modality:image",
                 _before, _cfg_snapshot(cfg, _IMAGE_CFG_FIELDS))
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
    from app.application.llm_config_service import get_server_modality_row
    cfg = await get_server_modality_row(db, "vlm")  # 四模态统一出口（账号独立 P1）
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
    """写入服务器级识图配置（影响聊天/手机感知等图片理解；仅服务器控制台管理员）"""
    await _require_server_admin(user_id, lang)
    from app.application.llm_config_service import (
        get_or_create_server_modality_row,
        get_server_modality_row,
    )
    _before = _cfg_snapshot(await get_server_modality_row(db, "vlm"), _VLM_CFG_FIELDS)
    cfg = await get_or_create_server_modality_row(db, "vlm")  # 四模态统一出口（账号独立 P1）
    for field in ("base_url", "api_key", "model"):
        if field in data:
            setattr(cfg, field, (data.get(field) or "").strip() or None)
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    await _audit(db, user_id, "server.vlm_config.update", "modality:vlm",
                 _before, _cfg_snapshot(cfg, _VLM_CFG_FIELDS))
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
    from app.application.llm_config_service import get_server_modality_row
    cfg = await get_server_modality_row(db, "speech")  # 四模态统一出口（账号独立 P1）
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
    """写入服务器级语音大模型配置（仅服务器控制台管理员）"""
    await _require_server_admin(user_id, lang)
    from app.application.llm_config_service import (
        get_or_create_server_modality_row,
        get_server_modality_row,
    )
    _before = _cfg_snapshot(await get_server_modality_row(db, "speech"), _SPEECH_CFG_FIELDS)
    cfg = await get_or_create_server_modality_row(db, "speech")  # 四模态统一出口（账号独立 P1）
    for field in ("provider", "base_url", "api_key", "model"):
        if field in data:
            setattr(cfg, field, (data.get(field) or "").strip() or None)
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    await _audit(db, user_id, "server.speech_config.update", "modality:speech",
                 _before, _cfg_snapshot(cfg, _SPEECH_CFG_FIELDS))
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
    from app.models.agent import LlmUsage  # A8：额度读数改走 llm_quota.resolve_limit，本函数不再直接读额度表
    from app.models.user import User
    from app.application.family_service import is_sub_account, get_family_member_ids

    # B-TZ 修复（2026-09-01 审查）：库里 created_at 是 UTC naive（func.now()）。
    # 窗口按用户本地日历语义切，再统一转成 UTC naive 参与比较——否则本地
    # 00:00-08:00 的"今日/本月"会错窗（本地零点=前一天 16:00 UTC）。
    from datetime import timezone as _tz

    def _to_utc_naive(local_dt: datetime) -> datetime:
        """带 tzinfo 的本地时间 → UTC naive（供库内 UTC naive 列比较）。"""
        return local_dt.astimezone(_tz.utc).replace(tzinfo=None)

    # P3-12（2026-09-17）：原为裸 datetime.now()——取的是**服务器 OS 本地时区**，与本仓
    # 「用户可感知窗口统一走 app_local_now()（按 settings.APP_TZ_OFFSET_HOURS）」的规约不一致
    # （见 app/utils/timeutil.py 顶部：正因分散定义出过"北京日期当 UTC 零点"的 8 小时窗口偏差）。
    # 此处语义就是"用户本地日历的今日/近 7 日/本月"，故改用 app_local_now()；
    # 默认 +8 且服务器 OS 同区时与旧值逐字节等价，跨区部署时才真正纠正。
    # （备份 zip 文件名的日期键同属「用户可感知」口径 —— 批 2b 起生产端
    #  scripts/backup.py 与三处消费端（本模块 trigger_backup / download_backup、
    #  application/account_purge.py 的前置备份）统一走 backup_day_key()＝应用本地时区。）
    now_local = app_local_now()
    today0 = _to_utc_naive(datetime(now_local.year, now_local.month, now_local.day, tzinfo=now_local.tzinfo))
    week0 = today0 - timedelta(days=6)
    month0 = _to_utc_naive(datetime(now_local.year, now_local.month, 1, tzinfo=now_local.tzinfo))

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

    # A4 批 5 / T6 成本与缓存护栏 M0 项 3（2026-09-27）：按任务归因的用量桶 by_task。
    # 依据：llm_usage.task 列早已存在（写入侧归因，审计 P1-07），但读端只有 by_model/by_user，
    # 看不出「哪个用途吃掉多少 token」，成本/缓存护栏无法定位大头。这里是**纯读端聚合**
    # （rows 已在内存），不新增列、不写迁移。整块 fail-open：聚合异常按空处理只记 WARNING，
    # 不让用量接口 500（记账与观测不得让调用失败）。task 为空的行归到 "(untagged)"，
    # 与「未知用途」区分开，避免把无归因用量误算进某个真实任务。
    by_task: list[dict] = []
    try:
        _task_acc: dict[str, dict[str, int]] = {}
        for r in rows:
            _tk = (r.task or "")[:30] or "(untagged)"
            _b = _task_acc.setdefault(_tk, {"calls": 0, "total": 0, "prompt": 0, "completion": 0})
            _b["calls"] += 1
            _b["total"] += r.total_tokens or 0
            _b["prompt"] += r.prompt_tokens or 0
            _b["completion"] += r.completion_tokens or 0
        # 批8 块 D：桶形状**一字未动**（既有测试钉死精确相等，且只增不减是本接口口径）；
        # 面板要用的窗口聚合另走 usage_panel（下方返回键），不在这条全表载入上扩窗口（D-2）。
        by_task = [
            {"task": k, **v}
            for k, v in sorted(_task_acc.items(), key=lambda kv: (-kv[1]["total"], kv[0]))
        ]
    except Exception as e:
        _logger.warning("llm usage by_task aggregate failed: %s", e)
        by_task = []

    # A4 批 5 / T6 M2 项 2（2026-09-27）：按渠道归因的用量桶 by_channel（app / wechat_ilink / server）。
    # 与 by_task 同一段纯内存聚合写法（rows 已在内存，零新查询）。channel 为 NULL 的行**不回填**
    # （勘察 §4：回填会把「渠道归因上线前的历史」与「将来某处漏传」永久混进同一格，失去排错能力），
    # 这里单独归 (unknown) 桶——沿用 by_task 的 (untagged) 口径：无归因不与真实取值混读。
    # 整块 fail-open：聚合异常按空处理只记 WARNING，不让用量接口 500。
    by_channel: list[dict] = []
    try:
        _chan_acc: dict[str, dict[str, int]] = {}
        for r in rows:
            _ch = (r.channel or "")[:30] or _USAGE_UNKNOWN
            _b = _chan_acc.setdefault(_ch, {"calls": 0, "total": 0, "prompt": 0, "completion": 0})
            _b["calls"] += 1
            _b["total"] += r.total_tokens or 0
            _b["prompt"] += r.prompt_tokens or 0
            _b["completion"] += r.completion_tokens or 0
        by_channel = [
            {"channel": k, **v}
            for k, v in sorted(_chan_acc.items(), key=lambda kv: (-kv[1]["total"], kv[0]))
        ]
    except Exception as e:
        _logger.warning("llm usage by_channel aggregate failed: %s", e)
        by_channel = []

    # A8（2026-09-20）：额度改为按账号生效（覆盖 > 全局 > 未设置），与服务器控制台同口径 ——
    # 统一走 app/application/llm_quota.resolve_limit（额度表唯一读写出口）；控制台给某账号设过
    # 覆盖时，App 这里显示的就是该账号的真实额度（并回传 limit_source 便于前端区分来源）。
    from app.application import llm_quota
    _quota = await llm_quota.resolve_limit(user_id)
    limit = int(_quota.get("total_limit") or 0)
    remaining = (limit - total) if (limit and limit > 0) else None
    # 批8 块 D M0：窗口用量面板（近 N 天，按用途/按渠道 + 服务端占比 + estimated/money 说明）。
    # 走 usage_panel 的「窗口一次 SELECT」这条读法，**不是**在上面 rows 全表载入上扩窗口（D-2）；
    # 失败只让本段退回空结构，既有字段照常返回（观测不得让读数端 500）。
    try:
        panel = await usage_panel(user_id, _PANEL_DEFAULT_DAYS)
    except Exception as e:
        _logger.warning("llm usage panel failed user_id=%s: %s", user_id, e)
        panel = _blank_usage_panel(_PANEL_DEFAULT_DAYS)
        panel["error"] = "usage_panel_unavailable"
    return {
        "total_limit": limit,
        "limit_source": _quota.get("source"),
        "used_total": total,
        "remaining": remaining,
        "today": today,
        "week": week,
        "month": month,
        "by_model": [{"model": k, "total": v}
                     for k, v in sorted(by_model.items(), key=lambda kv: -kv[1])],
        "by_user": by_user,
        # T6-M0 项 3：只增不减——by_task 是新增项，上面既有字段口径一字未动（前端/既有测试不受影响）
        "by_task": by_task,
        # T6-M2 项 2：同样只增不减（by_channel 与 by_task 同构，NULL 归 (unknown)）
        "by_channel": by_channel,
        # 批8 块 D M0：窗口面板（口径/失败处理见上方注释；只增键，既有字段一字未动）
        "usage_panel": panel,
        "can_edit_limit": await is_admin_user(user_id),
    }


# ── A4 批 5 / T6 M1 项 1：分用途 / 分自然日 / 分模型的用量报表（服务器控制台只读）────
# 依据：M0 项 3 的 by_task 只挂在 App 侧 get_llm_usage（家庭范围、只到 total 一项），控制台要的是
# 「固定窗口内、四件套 token 全量 + 估算行留痕」的完整报表，才能回答「成本大头在哪个用途、
# 流式估算占了多大比例」。纯读端聚合：不加列、不建表、不写迁移。
_USAGE_UNTAGGED = "(untagged)"   # 与 M0 项 3 同哨兵：task 为空单独成桶，不混进真实用途
_USAGE_UNKNOWN = "(unknown)"     # provider / model / 日期缺失的行归这里（与「无归因」同一思路）


def _usage_metrics_blank() -> dict:
    """四件套 + calls 的空桶（报表与面板共用同一形状，避免两处各写一份键名）。"""
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "reasoning_tokens": 0}


def _usage_window_bounds(days: int) -> dict:
    """days → 窗口四界（本地日界 + UTC naive），口径与 usage_report 建立时一字未变。

    库内 created_at 是 UTC naive，窗口按 ``app_local_now()`` 的应用本地日历切
    （days=N 含今天，向前推 N-1 个本地日界），自然日也按同一 offset 归桶。
    """
    from app.utils.timeutil import app_tz_offset_hours, now_naive_utc

    offset = app_tz_offset_hours()
    now_local = app_local_now()
    start_local = datetime(
        now_local.year, now_local.month, now_local.day, tzinfo=now_local.tzinfo
    ) - timedelta(days=days - 1)
    return {
        "days": days,
        "offset": offset,
        "now_local": now_local,
        "start_local": start_local,
        "start_utc": start_local.astimezone(timezone.utc).replace(tzinfo=None),
        "end_utc": now_naive_utc(),
    }


def _usage_window_descriptor(w: dict) -> dict:
    """窗口四界 → 对外可读形状（键名/精度与原 usage_report.window 完全一致）。"""
    return {
        "days": w["days"],
        "tz_offset_hours": w["offset"],
        "start_local": w["start_local"].isoformat(timespec="seconds"),
        "end_local": w["now_local"].isoformat(timespec="seconds"),
        "start_utc": w["start_utc"].isoformat(timespec="seconds"),
        "end_utc": w["end_utc"].isoformat(timespec="seconds"),
    }


async def _read_usage_window(db, start_utc: datetime, end_utc: datetime,
                             scope_cond=None) -> list:
    """窗口内用量行的一次 SELECT（只取聚合所需列）；``scope_cond=None`` ＝全局（控制台口径）。

    这是块 D 唯一的技术债防线：面板/报表一律走「窗口 + 指定列」这条读法，
    **不得**沿用 get_llm_usage 的 ``select(LlmUsage)`` 全表载入（设计 §1.4 D-2）。
    """
    from app.models.agent import LlmUsage

    stmt = select(
        LlmUsage.task, LlmUsage.channel, LlmUsage.provider, LlmUsage.model, LlmUsage.created_at,
        LlmUsage.prompt_tokens, LlmUsage.completion_tokens,
        LlmUsage.total_tokens, LlmUsage.reasoning_tokens,
    ).where(LlmUsage.created_at >= start_utc, LlmUsage.created_at <= end_utc)
    if scope_cond is not None:
        stmt = stmt.where(scope_cond)
    return (await db.execute(stmt)).all()


def _aggregate_usage_rows(rows: list, offset: int) -> tuple[dict, dict, dict, dict, dict]:
    """窗口行 → (task_acc, chan_acc, day_acc, model_acc, total)；纯函数、不碰库。

    分桶哨兵：task 空 → (untagged)、channel/provider/model/日期缺失 → (unknown)，
    「无归因」不与真实取值混读（channel 历史行不回填，见迁移 b4c5d6e7f8a9）。
    """
    from app.utils.timeutil import shift_utc_naive

    def _split(r) -> tuple:
        metrics = {
            "prompt_tokens": r.prompt_tokens or 0,
            "completion_tokens": r.completion_tokens or 0,
            "total_tokens": r.total_tokens or 0,
            "reasoning_tokens": r.reasoning_tokens or 0,
        }
        day = (shift_utc_naive(r.created_at, offset).date().isoformat()
               if r.created_at else _USAGE_UNKNOWN)
        key_model = ((r.provider or "")[:30] or _USAGE_UNKNOWN,
                     (r.model or "")[:50] or _USAGE_UNKNOWN)
        return metrics, day, key_model

    task_acc: dict[str, dict] = {}
    chan_acc: dict[str, dict] = {}
    day_acc: dict[str, dict] = {}
    model_acc: dict[tuple[str, str], dict] = {}
    total_b = _usage_metrics_blank()
    for r in rows:
        metrics, day, key_model = _split(r)
        for acc, key in ((task_acc, (r.task or "")[:30] or _USAGE_UNTAGGED),
                         (chan_acc, (getattr(r, "channel", None) or "")[:30] or _USAGE_UNKNOWN),
                         (day_acc, day), (model_acc, key_model)):
            b = acc.setdefault(key, _usage_metrics_blank())
            b["calls"] += 1
            for f, v in metrics.items():
                b[f] += v
        total_b["calls"] += 1
        for f, v in metrics.items():
            total_b[f] += v
    return task_acc, chan_acc, day_acc, model_acc, total_b


def _usage_emit(acc: dict, naming) -> list[dict]:
    """桶 → 列表：total_tokens 降序，同额按名称升序（输出稳定，便于回归比对）。"""
    return [
        {**naming(k), **b}
        for k, b in sorted(acc.items(), key=lambda kv: (-kv[1]["total_tokens"], str(kv[0])))
    ]


async def usage_report(days: int = 7) -> dict:
    """窗口内 LLM 用量报表：total + by_task + by_channel + by_day + by_model + estimated_calls（只读）。

    口径与 ``get_llm_usage`` 一致：库内 created_at 是 UTC naive，窗口按 ``app_local_now()``
    的应用本地日历切（days=N 含今天，向前推 N-1 个本地日界），自然日也按本地日界归桶。
    SQL 侧不做方言相关的日期函数——窗口内一次 SELECT + 内存分桶（与既有读端同法）。
    estimated_calls 取 agent_task_logs.route="usage_estimated"（M0 项 2(b) obs_event 写入点），
    用于把「估算行」与「实测行」分开看（llm_usage 没有估算标记列，只能靠这条留痕对账）。

    全程只 SELECT；fail-open：读库/聚合异常返回空结构 + WARNING，不让控制台 500。
    days 的上下限校验在 API 层（app/api/admin.py）做，本函数按已校验值处理。
    """
    from sqlalchemy import func

    from app.db.database import async_session_factory
    from app.models.agent import AgentTaskLog

    w = _usage_window_bounds(days)

    result = {
        "window": _usage_window_descriptor(w),
        "total": _usage_metrics_blank(),
        "by_task": [],
        "by_channel": [],   # T6-M2：空结构也要带这个键（fail-open 返回体口径一致）
        "by_day": [],
        "by_model": [],
        "estimated_calls": 0,
    }

    try:
        async with async_session_factory() as db:
            rows = await _read_usage_window(db, w["start_utc"], w["end_utc"])
            result["estimated_calls"] = int((await db.execute(
                select(func.count()).select_from(AgentTaskLog).where(
                    AgentTaskLog.route == "usage_estimated",
                    AgentTaskLog.created_at >= w["start_utc"],
                    AgentTaskLog.created_at <= w["end_utc"],
                )
            )).scalar_one() or 0)
    except Exception as e:
        _logger.warning("usage report read failed days=%s: %s", days, e)
        return result

    task_acc, chan_acc, day_acc, model_acc, total_b = _aggregate_usage_rows(rows, w["offset"])
    result["total"] = total_b
    result["by_task"] = _usage_emit(task_acc, lambda k: {"task": k})
    # T6-M2 项 2：chan_acc 已在上面分桶，这里必须吐出（与 by_task 同排序口径：用量降序）
    result["by_channel"] = _usage_emit(chan_acc, lambda k: {"channel": k})
    # by_day 不跟随「用量降序」：时间序列按日期升序才是可读的报表形态（其余三桶仍按用量降序）
    result["by_day"] = [{"date": k, **day_acc[k]} for k in sorted(day_acc)]
    result["by_model"] = _usage_emit(model_acc, lambda k: {"provider": k[0], "model": k[1]})
    return result


# ── A4 批 8 / 块 D「费用面板」M0（2026-09-30）：按账号的窗口用量读数（纯读、零新 schema 依赖）──
# 与 usage_report 的关系：**同一套聚合内核**（_read_usage_window + _aggregate_usage_rows +
# _usage_emit），差别只在这一处——面板按账号范围过滤（设计 §2.4「安全口径」）。
# 面板绝不沿 get_llm_usage 的全表载入扩窗口（D-2），也绝不重写 SQL 口径（D4）。
_PANEL_DEFAULT_DAYS = 7
_PANEL_MIN_DAYS = 1
_PANEL_MAX_DAYS = 90   # 与 admin.py:1152 控制台报表同一上限，两处窗口不各说各话


def _panel_days_or_raise(raw: object) -> int:
    """days 校验：脏输入/越界 → ValueError（API 层映射 400），**不静默夹取**。

    照控制台报表的口径（app/api/admin.py 对 days 1..90 越界返回 400 而不是夹到边界）：
    静默夹取会让「我要看 365 天」变成「看到 90 天」却毫无提示。
    """
    try:
        days = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("days 必须是 1-%d 的整数" % _PANEL_MAX_DAYS)
    if days < _PANEL_MIN_DAYS or days > _PANEL_MAX_DAYS:
        raise ValueError("days 必须在 %d-%d 之间" % (_PANEL_MIN_DAYS, _PANEL_MAX_DAYS))
    return days


def _panel_estimated_segment() -> dict:
    """估算可区分性说明：行级「实测 / 估算」**如实 unavailable**，不编数字。

    已知断点 D-1：流式上游不回 usage 时 llm_client 记的是估算行（``estimated=True``），
    但 ``llm_usage`` 没有估算标记列（models/agent/__init__.py LlmUsage），标记只落在
    ``agent_task_logs`` route="usage_estimated" 的观测明细里（obs_event 的第一参是
    character_id、不带 user_id）⇒ 既无法按账号过滤，也没有可 join 的行级外键。
    补列属 M1 之后的独立决策（设计 §8 待拍板 4），本轮面板只做**诚实声明**。
    """
    return {
        "status": "unavailable",
        "reason": "no_estimated_column",
        "per_row": "unavailable",
        "join_key": None,
        "note": ("估算行与实测行在 llm_usage 里同形（无估算列）；留痕在 agent_task_logs"
                 " route=usage_estimated 但不带账号与行级外键 ⇒ 本面板不按账号给估算数"),
    }


def _panel_money_segment() -> dict:
    """金额段：维持 unavailable（无价目表），且**绝不把单轮预算投影说成历史花费**。

    单一事实源是 _cost_estimate / _price_range_for / _TOKEN_PRICE_RANGES（刻意留空，见其注释）。
    本轮「费用估算」的 basis 是 ``full_effective_budget_input_only``＝一轮、输入侧、预算用满的
    **投影**，与本面板的「窗口历史用量」是两套语义 ⇒ 这里不复用那个字符串（设计 §2.4：
    混在一个字段名里就是第二套真相），金额字段一律 None。
    """
    return {
        "status": "unavailable",
        "reason": ("no_price_table" if not _TOKEN_PRICE_RANGES else "priced_history_basis_undefined"),
        "currency": _PRICE_CURRENCY,
        "amount_low": None,
        "amount_high": None,
        "basis": None,
        "is_historical_spend": False,
        "note": "无价目表 ⇒ 金额段不出数；既有 cost_estimate 段是单轮预算投影，不是历史花费",
    }


def _blank_usage_panel(days: int) -> dict:
    """面板空结构（窗口照出、桶为空）：读库失败与「本就没数据」共用同一形状，App 端一套渲染。"""
    w = _usage_window_bounds(days)
    return {
        "window": _usage_window_descriptor(w),
        "scope": {"account_only": False, "includes_server_rows": True},
        "total": _usage_metrics_blank(),
        "by_task": [],
        "by_channel": [],
        "estimated": _panel_estimated_segment(),
        "money": _panel_money_segment(),
        "error": "",
    }


async def usage_panel(user_id: int, days: int = _PANEL_DEFAULT_DAYS) -> dict:
    """本账号窗口内用量面板读数：total + by_task + by_channel（含占比）+ estimated + money（纯读）。

    - **数据源**＝llm_usage 唯一事实源，聚合复用 usage_report 的「窗口一次 SELECT + 分桶」内核；
      禁止沿 get_llm_usage 的全表载入扩窗口（设计 §1.4 D-2，本块唯一技术债防线）。
    - **账号范围**＝与 get_llm_usage 一致：主账号＝自己 + 直属子账号 + user_id IS NULL 的
      服务器级行；子账号＝仅自己。家庭共享（group_owner_id）口径本轮**不出**（§8 待拍板 5）。
    - **占比 share**＝该桶 total_tokens ÷ 窗口 total_tokens（服务端算，前端零本地计算）；
      窗口总额为 0 时 share 给 0.0，不做除零兜底数字。
    - **estimated / money** 见两个段函数：不编数字、不冒充实测/历史花费。
    - fail-open：读库/聚合异常返回空结构 + WARNING，不让读数端 500（观测不得让调用失败）。
    """
    from sqlalchemy import or_

    from app.application.family_service import get_family_member_ids, is_sub_account
    from app.db.database import async_session_factory
    from app.models.agent import LlmUsage

    result = _blank_usage_panel(days)
    w = _usage_window_bounds(days)
    try:
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
            rows = await _read_usage_window(db, w["start_utc"], w["end_utc"], cond)
    except Exception as e:
        _logger.warning("usage panel read failed user_id=%s days=%s: %s", user_id, days, e)
        result["error"] = "usage_panel_read_failed"
        return result

    result["scope"] = {"account_only": bool(is_sub), "includes_server_rows": bool(include_server)}
    task_acc, chan_acc, _day_acc, _model_acc, total_b = _aggregate_usage_rows(rows, w["offset"])
    result["total"] = total_b
    denom = total_b["total_tokens"]

    def _with_share(buckets: list[dict]) -> list[dict]:
        for b in buckets:
            b["share"] = round(b["total_tokens"] / denom, 3) if denom > 0 else 0.0
        return buckets

    result["by_task"] = _with_share([
        {**{"task": k, "key": k}, **b} for k, b in
        sorted(task_acc.items(), key=lambda kv: (-kv[1]["total_tokens"], str(kv[0])))
    ])
    result["by_channel"] = _with_share([
        {**{"channel": k, "key": k}, **b} for k, b in
        sorted(chan_acc.items(), key=lambda kv: (-kv[1]["total_tokens"], str(kv[0])))
    ])
    return result


async def update_llm_usage_limit(
    body: dict,
    user_id: int,
    lang: str,
):
    """设置**本账号**的免费额度覆盖（tokens，服务器管理员；0=额度为 0，非「清除」）。

    A8（2026-09-20）：额度从单行全局扩成「全局默认 + 账号覆盖」——
    - App 侧写的是**自己账号**的覆盖行（不再改服务器全局默认）：全局默认由服务器控制台管理
      （PUT /api/v1/admin/server/llm-limit），控制台还可逐账号设/清除覆盖；
    - 与控制台同口径、同一出口：写走 llm_quota.set_user_limit，读走 resolve_limit（覆盖 > 全局 > 未设置）。
    """
    await _require_server_admin(user_id, lang)
    from app.application import llm_quota
    from app.db.database import async_session_factory
    try:
        limit = max(0, int(body.get("total_limit") or 0))
    except Exception:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "total_limit_invalid"))
    _before = await llm_quota.resolve_limit(user_id)
    try:
        await llm_quota.set_user_limit(user_id, limit, user_id)
    except Exception:
        raise HTTPException(status_code=500, detail=tr_lang(lang, "config_invalid"))
    async with async_session_factory() as db:
        await _audit(db, user_id, "server.llm_usage_limit.update", "llm_usage_limit",
                     _before, {"scope": "user", "total_limit": limit})
        await db.commit()
    after = await llm_quota.resolve_limit(user_id)
    return {"total_limit": int(after.get("total_limit") or 0), "limit_source": after.get("source")}


async def get_feature_flags(
    user_id: int,
    lang: str,
):
    '''读取全部运行时 Feature Flag（主账号 = is_admin）；source: db=DB 覆盖 / default=硬编码默认

    契约 §3：读类保持现状（_require_admin），避免非 server_admin 账号的 App 开关页读接口 403。

    A5 用户级开关覆盖（2026-09-19）：每个条目新增
    - ``scope``：``'user'``（该键按账号生效，本批 5 个隐私细槽族键）/ ``'server'``（服务器级）；
    - ``user_enabled``：**该账号**的覆盖值（无覆盖行 = ``null``）；
    既有 key/enabled/value/type/source 字段语义保持不变。在服务层补充而非改 flag_service.get_all_flags
    签名，保持既有 monkeypatch（无参 fake）与调用方兼容。

    A4 目录元数据（2026-09-20）：每个条目**追加** ``meta``
    ``{title, desc, group, group_order, order, visible}``（按请求 lang 选 zh/en，缺省 zh；
    visible=True = App 常用开关直显）。来源 = ``app/application/flag_catalog.py`` 的纯内存字典，
    **不打库**；既有字段（key/enabled/value/type/source/scope/user_enabled）语义与顺序均不变。

    C1a（2026-09-25）：再**追加** ``locked`` 与 ``self_service`` 两个布尔（缺省语义同
    ``get_flag_policy``：未锁定 / 可自助改），来源 = ``flag_settings`` 策略，**一次批量取**
    （:func:`flag_service.get_flag_policies`，禁止逐键打库 N 次）。App 开关页据此把该条置灰只读。
    '''
    await _require_admin(user_id, lang)
    from app.application import flag_catalog
    from app.application import flag_service
    flags = await flag_service.get_all_flags()
    user_flags = await flag_service.get_user_flags(user_id)
    policies = await flag_service.get_flag_policies([f.get('key') for f in flags])
    for f in flags:
        if f.get('key') in flag_service.USER_SCOPED_FLAG_KEYS:
            f['scope'] = 'user'
            f['user_enabled'] = user_flags.get(f['key'])  # 无覆盖 = None（不是 False）
        else:
            f['scope'] = 'server'
            f['user_enabled'] = None
        f['meta'] = flag_catalog.meta_for(f.get('key'), lang)
        policy = policies.get(f.get('key')) or {}
        f['locked'] = bool(policy.get('server_locked', False))
        f['self_service'] = bool(policy.get('self_service', True))
    return {'status': 'ok', 'flags': flags}


async def update_feature_flag(
    key: str,
    data: dict,
    user_id: int,
    lang: str,
):
    '''切换 Feature Flag（本账号可用；服务器控制台可逐键锁定）：写 DB + 热更新内存立即生效；未知 key 返回 404

    账号独立 P2（契约 §1.3 **Codex 09-19 修订**）：本端点即「用户侧写开关」路径——
    - 写权限**保持既有 `_require_admin`**：本产品里每个独立账号都用自己的 App 开关页，若一并
      收紧为 require_server_admin，会让所有非服务器管理员的账号一写开关就 403（功能回归）；
    - 服务器控制权改由**逐键策略**承担：``flag_settings.server_locked=1`` → 403（锁定＝仅控制台
      PUT /api/v1/admin/server/flags/{key} 可改）；``self_service=0`` → 同样不可自助改（403）。
      这正是用户要的「能让某些开关关闭、不放开开关权限」。
    - 真正「服务器级」的写入口（四模态服务器配置 / 任务 API 配置 / 额度 / 备份触发与下载）仍收紧为
      ``require_server_admin``（见契约 §3 与 `_require_server_admin`）。

    A5 用户级开关覆盖（2026-09-19）：写路径按 key 分流——
    - key ∈ ``USER_SCOPED_FLAG_KEYS`` → 写**该账号的覆盖行**（不写全局、不动进程级 AGENT_FLAGS），
      返回体沿用现状结构并标 ``scope='user'``；
    - key ∉ USER_SCOPED → **保持现状写全局**（权限判定不变），返回 ``scope='server'``。
    两条路径都**先过既有策略判定**（server_locked / self_service → 403），非 bool 键的类型防护
    由 set_runtime_flag / set_user_flag 内部承担，均不得绕过。
    '''
    await _require_admin(user_id, lang)
    if 'enabled' not in data:
        raise HTTPException(status_code=400, detail='enabled required')
    from app.application import flag_service
    from app.agent.loop import AGENT_FLAGS
    # 先取策略与旧值（各自只读；策略读失败 fail-open 到「自助开、未锁定」）
    policy = await flag_service.get_flag_policy(key)
    if policy['server_locked']:
        raise HTTPException(status_code=403, detail=tr_lang(lang, 'flag_server_locked'))
    if not policy['self_service']:
        raise HTTPException(status_code=403, detail=tr_lang(lang, 'flag_self_service_disabled'))
    enabled = bool(data.get('enabled'))
    if key in flag_service.USER_SCOPED_FLAG_KEYS:
        # 用户语义键：写该账号覆盖行；全局值与其它账号均不受影响。
        _before = {'enabled': bool(await flag_service.resolve_flag(key, user_id))}
        ok = await flag_service.set_user_flag(key, user_id, enabled)
        if not ok:
            raise HTTPException(status_code=404, detail='unknown feature flag: ' + key)
        await _audit(None, user_id, 'server.feature_flag.update', 'flag:' + key,
                     _before, {'enabled': enabled, 'scope': 'user'})
        return {'status': 'ok', 'key': key, 'enabled': enabled, 'scope': 'user'}
    _before = {'enabled': bool(AGENT_FLAGS.get(key)) if key in AGENT_FLAGS else None}
    ok = await flag_service.set_runtime_flag(key, enabled)
    if not ok:
        raise HTTPException(status_code=404, detail='unknown feature flag: ' + key)
    await _audit(None, user_id, 'server.feature_flag.update', 'flag:' + key,
                 _before, {'enabled': enabled})
    return {'status': 'ok', 'key': key, 'enabled': enabled, 'scope': 'server'}


# ── 上下文预算读数（P2b，2026-09-24：App「导出诊断信息」的 P2a 读数端）──

# 与 context_builder._apply_system_total_quota 写埋点时的 route 同名（改埋点名此处同步）
_CLIP_ROUTE = "quota_clipped_sections"
_CLIP_WINDOW_HOURS = 24
# T5 M0 项2（2026-09-27）装配尾部留痕：每轮真装配写一条「system 总字符 + 本次生效预算」，
# 不依赖 provider usage ⇒ 它就是 S2 读数端「最近一轮实际占用」的样本源。
_USAGE_ROUTE = "system_total_chars"
# Y2（2026-09-29，S2 尾巴）：每段注入体量留痕（agent/context/__init__.py 的 obs_event），
# 每轮装配一条，detail.sections 只带 chars 最大的前 16 段（埋点侧截断，见该文件注释）。
_SECTION_ROUTE = "section_budget"

BREAKDOWN_DEFAULT_SAMPLES = 20
BREAKDOWN_MAX_SAMPLES = 50
# 每轮埋点里最多带 16 段（超出被截断），响应条数也按这个上限收口
_BREAKDOWN_TURN_SECTIONS = 16

# 单价区间表：键 = model 名 → provider 名 → "default"，值 = 每百万 input token 的（低, 高）价。
# **刻意留空**：项目内目前没有任何价目来源（llm_usage 只记 token 不记价、user_llm_configs 无价目列、
# 配置文件也没有）⇒ 硬编码一个数字＝编造价目，读数端宁可不报。接价目时只改这张表，
# 估算算式与 unavailable 状态机不动（见 _cost_estimate 的 reason 三态）。
_TOKEN_PRICE_RANGES: dict[str, tuple[float, float]] = {}
_PRICE_CURRENCY = "CNY"
_PER_MILLION_TOKENS = 1_000_000


def _unknown_usage(reason: str = "no_sample") -> dict:
    """无样本时的占用口径：只报「未知」+ 为什么未知，绝不拿预算值倒推一个占用数。"""
    return {"status": "unknown", "reason": reason, "system_chars": None, "est_tokens": None}


def _empty_breakdown(samples: int) -> dict:
    """无样本的体量段：samples=0 + items 空，App 侧据此显示「暂无样本」而不是 0。"""
    return {
        "status": "no_sample",
        "samples": 0,
        "samples_limit": samples,
        "sections_scope": "top%d_per_turn" % _BREAKDOWN_TURN_SECTIONS,
        "keys_total": 0,
        "items": [],
    }


def _clamp_breakdown_samples(raw: object) -> int:
    """请求样本数 → 合法值：脏输入/越界一律夹到 [1, MAX]（与档位夹紧同一口径，不报错）。"""
    try:
        n = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return BREAKDOWN_DEFAULT_SAMPLES
    return max(1, min(n, BREAKDOWN_MAX_SAMPLES))


def _aggregate_section_breakdown(details: list[dict], samples_limit: int) -> dict:
    """把 N 条 section_budget 埋点聚成「按 key 的均值/最大/空次数/样本数」。

    纯函数（不碰库），口径与埋点侧对齐：一条埋点 = 一轮装配；某 key 在该轮没出现就不计入
    它的 samples（「出现过几次」与「其中几次是空」分开报，混成一个分母会让人误读空率）。
    """
    acc: dict[str, dict] = {}
    for detail in details:
        for sec in (detail.get("sections") or []):
            if not isinstance(sec, dict):
                continue
            key = str(sec.get("key") or "")[:60]
            if not key:
                continue
            try:
                chars = int(sec.get("chars"))
            except (TypeError, ValueError):
                continue
            chars = max(0, chars)
            slot = acc.setdefault(
                key, {"samples": 0, "chars_sum": 0, "chars_max": 0, "empty_count": 0})
            slot["samples"] += 1
            slot["chars_sum"] += chars
            slot["chars_max"] = max(slot["chars_max"], chars)
            if chars <= 0 or sec.get("empty") is True:
                slot["empty_count"] += 1
    if not acc:
        return _empty_breakdown(samples_limit)
    items = [
        {
            "key": key,
            "samples": slot["samples"],
            "avg_chars": int(round(slot["chars_sum"] / slot["samples"])),
            "max_chars": slot["chars_max"],
            "empty_count": slot["empty_count"],
        }
        for key, slot in acc.items()
    ]
    items.sort(key=lambda x: (-x["avg_chars"], x["key"]))
    top = items[:_BREAKDOWN_TURN_SECTIONS]
    peak = top[0]["avg_chars"] if top else 0
    # 条形长度也交给服务端：前端自己按最大值算比例会和「均值/最大并存」这套数打架
    for item in top:
        item["share"] = round(item["avg_chars"] / peak, 3) if peak > 0 else 0.0
    return {
        "status": "ok",
        "samples": len(details),
        "samples_limit": samples_limit,
        "sections_scope": "top%d_per_turn" % _BREAKDOWN_TURN_SECTIONS,
        "keys_total": len(items),
        "items": top,
    }


def _price_range_for(model: str | None, provider: str | None):
    """查单价区间：model → provider → default，命中返回 (区间, 命中的键)；都没命中返回 None。"""
    if not _TOKEN_PRICE_RANGES:
        return None
    for name in (model, provider, "default"):
        if not name:
            continue
        rng = _TOKEN_PRICE_RANGES.get(str(name))
        if isinstance(rng, (tuple, list)) and len(rng) == 2:
            return (float(rng[0]), float(rng[1])), str(name)
    return None


def _cost_estimate(budget_tokens: int, model: str | None, provider: str | None) -> dict:
    """按当前生效预算估「一轮输入侧」的费用区间（区间＝价目低/高两界，不给单点承诺）。

    口径写进响应（basis/assumptions）：按本档预算**用满**、**只算输入**、不含输出与工具调用；
    缺价目时 status=unavailable + reason，任何金额字段都是 None——不编数字。
    """
    payload = {
        "status": "unavailable",
        "reason": "no_price_table",
        "currency": _PRICE_CURRENCY,
        "basis": "full_effective_budget_input_only",
        "budget_tokens": budget_tokens,
        "model": model,
        "price_source": None,
        "per_million_low": None,
        "per_million_high": None,
        "per_turn_low": None,
        "per_turn_high": None,
    }
    hit = _price_range_for(model, provider)
    if hit is None:
        payload["reason"] = (
            "no_price_table" if not _TOKEN_PRICE_RANGES else "model_unpriced")
        return payload
    (low, high), source = hit
    low, high = min(low, high), max(low, high)
    factor = budget_tokens / _PER_MILLION_TOKENS
    payload.update(
        status="ok",
        reason="",
        price_source=source,
        per_million_low=round(low, 6),
        per_million_high=round(high, 6),
        per_turn_low=round(low * factor, 6),
        per_turn_high=round(high * factor, 6),
    )
    return payload


async def read_account_context_budget_tier(user_id: int, db: AsyncSession | None = None) -> str | None:
    """读本账号的上下文预算档位**原值**（users.context_budget_tier 的唯一存储读端）。

    - 未设置（列 NULL）/ 空串 / 账号不存在 → None（= 标准档 = 现状逐字节旧行为）；
    - 本函数**不吞异常**：调用方各有自己的 fail-open 口径——对话装配链
      （context_builder._resolve_account_budget_tier）异常退回标准档；读数端按「不可用」上报
      （get_context_budget 的 tier_source），因为这里区分「没配」与「读不到」是有信息量的。
    - ``db=None`` 时自开会话（装配链路上没有现成会话）。
    """
    from app.models.user import User

    async def _one(session) -> str | None:
        value = (await session.execute(
            select(User.context_budget_tier).where(User.id == user_id)
        )).scalar_one_or_none()
        return value.strip() if isinstance(value, str) and value.strip() else None

    if db is not None:
        return await _one(db)
    from app.db.database import async_session_factory

    async with async_session_factory() as session:
        return await _one(session)


async def set_context_budget_tier(
    user_id: int,
    db: AsyncSession,
    data: dict | None,
    lang: str = "zh",
) -> dict:
    """设置本账号上下文预算档位（S2 M0，2026-09-27；账号级偏好，只影响自己的装配预算）。

    写入侧刻意不报错打断（档位是可调项不是校验题）：任何输入先过
    ``context_builder.normalize_context_budget_tier``——合法名归一、越界数值夹到最近的合法档、
    认不出的一律退回 standard。账号不存在才 404（系统边界，用户可感知）。

    生效时机：下一轮装配（每轮入口重新读库，无缓存 ⇒ 改完即生效，不需重启、不需重连）。
    """
    from app.agent import context_builder as _cb
    from app.models.user import User

    tier = _cb.normalize_context_budget_tier((data or {}).get("tier"))
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "user_not_found"))
    _before = {"context_budget_tier": target.context_budget_tier}
    target.context_budget_tier = tier
    await _audit(db, user_id, "server.context_budget_tier.update", "user:%d" % user_id,
                 _before, {"context_budget_tier": tier})
    await db.commit()
    return {
        "status": "ok",
        "tier": tier,
        "tier_budget_tokens": _cb.context_budget_tier_tokens(tier),
        "previous_tier": _before["context_budget_tier"] or _cb.CONTEXT_BUDGET_TIER_DEFAULT,
    }


async def get_context_budget(
    user_id: int,
    db: AsyncSession,
    breakdown_samples: object = BREAKDOWN_DEFAULT_SAMPLES,
) -> dict:
    """上下文预算读数：档位 + P2a 预留口径 + 本账号最近一轮实际占用/最近一次被裁记录（纯读）。

    预算段直接复用 app/agent/context_builder 的常量与纯函数（含私有的
    ``_effective_system_budget_tokens``）——**读端把同一套算式再抄一遍，常量一调两边就失真**，
    而这里要报的正是「装配时真正生效的那个数」，故按同包内部纯函数的既有约定直接调用而非复制实现。
    S2 起该数还带账号档位维：读端显式传 ``tier=``（不依赖装配链的 ContextVar，二者在
    请求上下文里本就不同）。

    占用段读 agent_task_logs 里 trigger='memory_obs' + route='system_total_chars' 的每轮留痕
    （T5 M0 项2），**只看当前用户自己的行**；无样本时 status='unknown'，不拿预算值冒充占用。
    裁剪段同理读 route='quota_clipped_sections'（P2a 要求 C：真发生裁剪才写）。
    Y2（2026-09-29）再加两块同样**纯读**的段：section_breakdown＝本账号最近 N 轮
    route='section_budget' 埋点按段聚合（N 越界夹到 [1, 50]，脏输入不报错）；
    cost_estimate＝按本档生效预算 × 单价区间估「一轮输入侧」费用，**价目表为空时如实
    unavailable**（见 _TOKEN_PRICE_RANGES 注释），不编数字。
    整段查库 fail-open：任何异常（含 steps_json 是坏 JSON）都退化为「预算段照出 + 无记录 + error
    文案」，绝不抛 500——一次诊断导出不该因为读不到观测流水而失败。
    """
    from app.agent import context_builder as _cb

    flag_enabled = _cb.context_budget_reserve_enabled()
    samples_limit = _clamp_breakdown_samples(breakdown_samples)
    stored_tier: str | None = None
    tier_error = ""
    try:
        stored_tier = await read_account_context_budget_tier(user_id, db)
    except Exception as e:
        tier_error = ("tier_query_failed: " + repr(e))[:200]
    # 档位三态：user=账号显式配置 / default=未配置（NULL，等价标准档）/ unavailable=读库失败
    tier = _cb.normalize_context_budget_tier(stored_tier)
    tier_source = ("user" if stored_tier else "default") if not tier_error else "unavailable"
    payload: dict = {
        "status": "ok",
        "total_quota_tokens": _cb.TOTAL_SYSTEM_QUOTA_TOKENS,
        "reserve_reply_tokens": _cb.REPLY_RESERVE_TOKENS,
        "reserve_tools_tokens": _cb.TOOL_DEFS_RESERVE_TOKENS,
        "floor_tokens": _cb.MIN_SYSTEM_BUDGET_TOKENS,
        # S2 档位段：当前档位 + 该档有效预算 + 可调档位表（档位 UI 在 App 下一批，这里只给词表；
        # 刻意不回中文 label —— 展示文案归客户端 i18n，服务端只给 key 与数值）
        "tier": tier,
        "tier_source": tier_source,
        "tier_stored": stored_tier,
        "tier_error": tier_error,
        "tier_budget_tokens": _cb.context_budget_tier_tokens(tier),
        "tier_ceiling_tokens": _cb.CONTEXT_BUDGET_TIER_CEILING_TOKENS,
        "tier_options": [
            {
                "key": key,
                "budget_tokens": _cb.context_budget_tier_tokens(key),
                "is_current": key == tier,
            }
            for key in (
                _cb.CONTEXT_BUDGET_TIER_STANDARD,
                _cb.CONTEXT_BUDGET_TIER_EXTENDED,
                _cb.CONTEXT_BUDGET_TIER_MAX,
            )
        ],
        "effective_budget_tokens": _cb._effective_system_budget_tokens(
            reserve_enabled=flag_enabled, tier=tier),
        "flag_enabled": flag_enabled,
        "last_usage": _unknown_usage(),
        "last_clip": None,
        "clip_count_24h": 0,
        # Y2 两段的兜底值：查库失败/无样本时照这个返回（估算段先按「无价目 + 生效预算」，
        # 命中本账号最近一次调用的 model 后在 try 里重算）
        "section_breakdown": _empty_breakdown(samples_limit),
        "cost_estimate": None,
        # 批8 块 D M0：本账号窗口用量面板（按用途/按渠道 + 占比，金额段照旧 unavailable）。
        # 兜底＝空结构（读数端不得因为面板取不到数而少一段），口径见 usage_panel docstring。
        "usage_panel": _blank_usage_panel(_PANEL_DEFAULT_DAYS),
        "error": "",
    }
    # 兜底＝按「生效预算 + 未知 model」算（无价目表时就是 unavailable）；try 里读到 model 后重算
    payload["cost_estimate"] = _cost_estimate(payload["effective_budget_tokens"], None, None)
    try:
        import json

        from sqlalchemy import func
        from app.models.agent import AgentTaskLog
        from app.utils.timeutil import now_naive_utc

        conds = (
            AgentTaskLog.trigger == "memory_obs",
            AgentTaskLog.user_id == user_id,
        )
        since = now_naive_utc() - timedelta(hours=_CLIP_WINDOW_HOURS)
        counted = (await db.execute(
            select(func.count()).select_from(AgentTaskLog).where(
                *conds, AgentTaskLog.route == _CLIP_ROUTE, AgentTaskLog.created_at >= since)
        )).scalar()
        payload["clip_count_24h"] = int(counted or 0)

        def _detail(row) -> dict | None:
            # detail 只回标量字段：埋点里的 blocks 数组带的是用户上下文块头部原文（≤24 字 ×8 条），
            # 预算读数用不上，也不必再把它外流一次；解析不出 dict 按「无记录」处理。
            if not row or not row.steps_json:
                return None
            detail = json.loads(row.steps_json)
            if not isinstance(detail, dict):
                return None
            return {k: v for k, v in detail.items() if not isinstance(v, (list, dict))}

        clip_row = (await db.execute(
            select(AgentTaskLog).where(*conds, AgentTaskLog.route == _CLIP_ROUTE)
            .order_by(AgentTaskLog.id.desc()).limit(1)
        )).scalars().first()
        clip_detail = _detail(clip_row)
        if clip_detail is not None:
            payload["last_clip"] = {
                "id": clip_row.id,
                "character_id": clip_row.character_id,
                "created_at": clip_row.created_at.isoformat(sep=" ") if clip_row.created_at else None,
                "detail": clip_detail,
            }

        usage_row = (await db.execute(
            select(AgentTaskLog).where(*conds, AgentTaskLog.route == _USAGE_ROUTE)
            .order_by(AgentTaskLog.id.desc()).limit(1)
        )).scalars().first()
        usage_detail = _detail(usage_row)
        if usage_detail is None:
            # 无样本：如实报「未知」，并给出为什么未知（该账号还没聊过一轮 / 观测开关关着）
            payload["last_usage"] = _unknown_usage()
        else:
            chars = usage_detail.get("system_chars")
            chars = int(chars) if isinstance(chars, (int, float)) else None
            payload["last_usage"] = {
                "status": "ok",
                "id": usage_row.id,
                "character_id": usage_row.character_id,
                "created_at": usage_row.created_at.isoformat(sep=" ") if usage_row.created_at else None,
                "system_chars": chars,
                # 估算口径与装配侧一致：2 字符 ≈ 1 token（context_builder._EST_CHARS_PER_TOKEN）
                "est_tokens": (chars // _cb._EST_CHARS_PER_TOKEN) if chars is not None else None,
                "budget_tokens_at_turn": usage_detail.get("budget_tokens"),
                "reserve_on": usage_detail.get("reserve_on"),
            }

        # ── Y2 ①每层体量：最近 N 轮 section_budget 埋点按段聚合（只看自己的行）──
        load_rows = (await db.execute(
            select(AgentTaskLog).where(*conds, AgentTaskLog.route == _SECTION_ROUTE)
            .order_by(AgentTaskLog.id.desc()).limit(samples_limit)
        )).scalars().all()
        details: list[dict] = []
        for row in load_rows:
            if not row.steps_json:
                continue
            try:
                parsed = json.loads(row.steps_json)
            except Exception:
                continue  # 单条坏留痕跳过（不让一行坏 JSON 拖垮整段聚合）
            if isinstance(parsed, dict) and isinstance(parsed.get("sections"), list):
                details.append(parsed)
        payload["section_breakdown"] = _aggregate_section_breakdown(details, samples_limit)

        # ── Y2 ②费用估算：本账号最近一次调用的 model/provider 进价目表查区间 ──
        from app.models.agent import LlmUsage

        used = (await db.execute(
            select(LlmUsage).where(LlmUsage.user_id == user_id)
            .order_by(LlmUsage.id.desc()).limit(1)
        )).scalars().first()
        payload["cost_estimate"] = _cost_estimate(
            payload["effective_budget_tokens"],
            used.model if used else None,
            used.provider if used else None,
        )

        # ── 批8 块 D M0 ③本账号窗口面板（按用途/按渠道 + 占比）──
        # 单独 try：面板读数失败只让这一段退回空结构，不污染上面已经取到的预算/占用/裁剪/估算段
        # （整段读数的 fail-open 取向同 Y2：一次诊断读数不该因为某段取不到而整体失败）。
        try:
            payload["usage_panel"] = await usage_panel(user_id, _PANEL_DEFAULT_DAYS)
        except Exception as e:
            _logger.warning("context budget panel failed user_id=%s: %s", user_id, e)
            payload["usage_panel"] = _blank_usage_panel(_PANEL_DEFAULT_DAYS)
            payload["usage_panel"]["error"] = "usage_panel_unavailable"
    except Exception as e:
        payload["error"] = ((payload["error"] + "; ") if payload["error"] else "") + (
            "clip_query_failed: " + repr(e))[:200]
    return payload


async def trigger_backup(
    user_id: int,
    lang: str,
):
    """触发一次备份（数据库 + 配置 + 源码快照），仅服务器控制台管理员。

    当天已有备份（如多次调用）则直接返回现有文件信息；返回 {path, size, created_at}。
    """
    await _require_server_admin(user_id, lang)
    import os as _os
    mod = _load_backup_module()
    try:
        # do_backup：运行中库用 SQLite backup API 安全复制，并做日志轮换 / 过期备份清理
        mod.do_backup()
    except Exception as e:
        _logger.error("backup triggered failed: %s", e)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "backup_failed"))
    today = mod.backup_day_key()  # 批 2b：与生产端同源（应用本地时区），勿再自行 datetime.now()
    zip_path = _os.path.join(mod.BACKUP_ROOT, f"{today}.zip")
    if not _os.path.isfile(zip_path):
        raise HTTPException(status_code=500, detail=tr_lang(lang, "backup_failed"))
    info = {"status": "ok", **_backup_info(zip_path)}
    await _audit(None, user_id, "server.backup.trigger", "backup", None,
                 {"path": info.get("path"), "size": info.get("size")})
    return info


async def download_backup(
    user_id: int,
    lang: str,
):
    """下载当天 / 最近一份备份 zip（仅服务器控制台管理员）；文件名用 ascii 安全名。

    纯读动作（无状态变更），按契约 §1.5「写动作覆盖面」不进审计表。
    """
    await _require_server_admin(user_id, lang)
    import os as _os
    from fastapi.responses import FileResponse
    mod = _load_backup_module()
    candidate = None
    today = mod.backup_day_key()  # 批 2b：同 trigger_backup，取「应用本地时区」的今天
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

