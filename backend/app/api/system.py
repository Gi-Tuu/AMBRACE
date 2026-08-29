"""系统状态 API + API 自主配置预留"""
from app.utils.logger import get_logger
from fastapi import APIRouter, Depends, HTTPException, Header, WebSocket
from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.db.database import get_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone

from app.utils.version import get_project_version
from app.services.permission_service import is_admin_user

router = APIRouter(prefix="/api/v1/system", tags=["System"])
_logger = get_logger("api.system")


@router.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/ready")
async def ready_check():
    """就绪检查（P2-4）：数据库可连接 + 向量模型可用。任一失败返回 503 + 明细。"""
    from app.db.database import async_session_factory
    from app.memory.embedding import check_model_available

    db_ok = False
    try:
        async with async_session_factory() as db:
            await db.execute(select(1))
        db_ok = True
    except Exception as e:
        _logger.error("ready db check failed: %s", e)

    model_ok = False
    try:
        model_ok = bool(check_model_available())
    except Exception as e:
        _logger.error("ready model check failed: %s", e)

    ok = db_ok and model_ok
    payload = {"status": "ok" if ok else "error", "db": db_ok, "model": model_ok}
    if not ok:
        raise HTTPException(status_code=503, detail=payload)
    return payload


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


@router.get("/status")
async def system_status():
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


# ── API 自主配置（BYOK：通用 LLM 用户级覆盖，OpenAI 兼容端点）──

@router.get("/api-config")
async def get_api_config(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """读取用户级 API 配置（api_key 不回传明文）"""
    from sqlalchemy import select
    from app.models.api_config import ApiConfig
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


@router.put("/api-config")
async def update_api_config(data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """写入用户级 API 配置（BYOK：聊天主链路启用后优先于服务器默认）"""
    from sqlalchemy import select
    from app.models.api_config import ApiConfig
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


# ── 服务器级全局 API 配置（开源部署：填一次全局 key，代码/.env 零密钥；仅主账号 user_id=1 可读写）──

async def _require_admin(user_id: int, lang: str = "zh") -> None:
    if not await is_admin_user(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "admin_config_only"))


@router.get("/api-config/server")
async def get_server_api_config(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """读取服务器级全局 API 配置（api_key 不回传明文）"""
    await _require_admin(user_id, lang)
    from app.models.api_config import ApiConfig
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


@router.put("/api-config/server")
async def update_server_api_config(data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入服务器级全局 API 配置（影响所有未配 BYOK 的调用；仅主账号）"""
    await _require_admin(user_id, lang)
    from app.models.api_config import ApiConfig
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


# ── 任务专用 LLM 配置（按用途指定模型 + 密钥池 + 连接测试；P1②，2026-08-12）──
# task_llm_configs 表：user_id=0=服务器级、>0=用户级；task 见 llm_client.TASK_LLM_CATALOG


@router.get("/api-config/tasks")
async def get_task_llm_catalog(user_id: int = Depends(get_current_user_id)):
    """任务目录（供前端渲染任务选择）"""
    from app.agent.llm_client import TASK_LLM_CATALOG
    return {"tasks": TASK_LLM_CATALOG}


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
    from app.models.task_llm_config import TaskLlmConfig
    result = await db.execute(
        select(TaskLlmConfig).where(TaskLlmConfig.user_id == user_id, TaskLlmConfig.task == task)
    )
    return result.scalar_one_or_none()


@router.get("/api-config/task/{task}")
async def get_task_api_config(task: str, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """读取用户级任务 LLM 配置（api_key 不回传明文）"""
    cfg = await _get_task_cfg(db, user_id, task)
    return _task_cfg_payload(cfg)


@router.put("/api-config/task/{task}")
async def update_task_api_config(task: str, data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """写入用户级任务 LLM 配置（upsert）"""
    from app.models.task_llm_config import TaskLlmConfig
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


@router.get("/api-config/task/server/{task}")
async def get_server_task_api_config(task: str, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """读取服务器级任务 LLM 配置（仅主账号）"""
    await _require_admin(user_id, lang)
    from app.agent.llm_client import SERVER_CONFIG_UID
    cfg = await _get_task_cfg(db, SERVER_CONFIG_UID, task)
    return _task_cfg_payload(cfg)


@router.put("/api-config/task/server/{task}")
async def update_server_task_api_config(task: str, data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入服务器级任务 LLM 配置（仅主账号；影响所有用户的该任务调用）"""
    await _require_admin(user_id, lang)
    from app.models.task_llm_config import TaskLlmConfig
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


@router.post("/api-config/test")
async def test_api_connection(body: dict, user_id: int = Depends(get_current_user_id)):
    """连接测试：最小请求校验 {base_url, api_key, model}；api_key 为空则用服务器级全局配置。
    多 Key（密钥池）逐个尝试，任一成功即 ok；返回耗时与命中 Key 尾号。"""
    import asyncio
    import time
    from app.agent.llm_client import (
        get_llm_client, get_server_llm_config, _split_api_keys,
    )
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
            await asyncio.wait_for(
                client.chat.completions.create(
                    model=model or "gpt-4o-mini",
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                ),
                timeout=30,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            return {"ok": True, "model": model or "gpt-4o-mini", "latency_ms": latency_ms,
                    "api_key_tail": key[-6:] if len(key) > 6 else key, "provider": provider or None}
        except Exception as e:
            last_err = str(e)[:300]
    return {"ok": False, "error": last_err}


# ── 生图服务器级全局配置（开源部署：填一次，key 不进 .env；仅主账号 user_id=1 可读写）──


@router.get("/image-gen-config/server")
async def get_image_gen_server_config(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """读取服务器级生图配置（api_key 不回传明文）"""
    await _require_admin(user_id, lang)
    from app.models.image_gen_config import ImageGenConfig
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


@router.put("/image-gen-config/server")
async def update_image_gen_server_config(data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入服务器级生图配置（影响聊天内 AI 发图与 /images 接口；仅主账号）"""
    await _require_admin(user_id, lang)
    from app.models.image_gen_config import ImageGenConfig
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


# ── 识图（图片理解）服务器级全局配置（开源部署：填一次，key 不进 .env；仅主账号 user_id=1 可读写）──


@router.get("/vlm-config/server")
async def get_vlm_server_config(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """读取服务器级识图配置（api_key 不回传明文）"""
    await _require_admin(user_id, lang)
    from app.models.vlm_config import VlmConfig
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


@router.put("/vlm-config/server")
async def update_vlm_server_config(data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入服务器级识图配置（影响聊天/手机感知等图片理解；仅主账号）"""
    await _require_admin(user_id, lang)
    from app.models.vlm_config import VlmConfig
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



# ── 语音大模型服务器级全局配置（开源部署：填一次，key 不进 .env；仅主账号 user_id=1 可读写）──
# 说明：当前语音转写仍走本地 faster-whisper，本配置先落库占位，云端 ASR 调用后续接入


@router.get("/speech-config/server")
async def get_speech_server_config(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """读取服务器级语音大模型配置（api_key 不回传明文）"""
    await _require_admin(user_id, lang)
    from app.models.speech_config import SpeechConfig
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


@router.put("/speech-config/server")
async def update_speech_server_config(data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入服务器级语音大模型配置（仅主账号）"""
    await _require_admin(user_id, lang)
    from app.models.speech_config import SpeechConfig
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


@router.post("/speech-preview")
async def speech_preview(data: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """音色试听：用固定基础文案合成当前音色/语速/语调，返回音频 URL（角色编辑页试听用）"""
    from app.services.tts_service import synthesize
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


# ── 全模态大模型服务器级全局配置（开源部署：填一次，key 不进 .env；仅主账号 user_id=1 可读写）──


@router.get("/multimodal-config/server")
async def get_multimodal_server_config(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """读取服务器级全模态大模型配置（api_key 不回传明文）"""
    await _require_admin(user_id, lang)
    from app.models.multimodal_config import MultimodalConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    result = await db.execute(select(MultimodalConfig).where(MultimodalConfig.user_id == SERVER_CONFIG_UID))
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


@router.put("/multimodal-config/server")
async def update_multimodal_server_config(data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入服务器级全模态大模型配置（仅主账号）"""
    await _require_admin(user_id, lang)
    from app.models.multimodal_config import MultimodalConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    result = await db.execute(select(MultimodalConfig).where(MultimodalConfig.user_id == SERVER_CONFIG_UID))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = MultimodalConfig(user_id=SERVER_CONFIG_UID)
        db.add(cfg)
        await db.flush()
    for field in ("provider", "base_url", "api_key", "model"):
        if field in data:
            setattr(cfg, field, (data.get(field) or "").strip() or None)
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    await db.commit()
    _logger.info("server multimodal-config updated user=%d enabled=%s provider=%s", user_id, bool(cfg.enabled), cfg.provider)
    return {"status": "ok", "enabled": bool(cfg.enabled), "configured": True}


@router.get("/updates")
async def get_updates():
    """更新公告：解析 docs/changelog.md，按天折叠（最新在前），供 app 内「更新公告」页展示"""
    from pathlib import Path

    changelog_path = Path(__file__).resolve().parents[3] / "docs" / "changelog.md"
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except Exception as e:
        _logger.warning("Changelog read failed: %s", e)
        return {"days": []}
    return {"days": _parse_changelog(text)}


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



# ── LLM token 用量与免费额度（2026-08-11：用量落库展示；总量仅主账号可设）──
@router.get("/llm-usage")
async def get_llm_usage(user_id: int = Depends(get_current_user_id)):
    """token 用量统计：今日/近7天/本月/累计 + 按模型汇总 + 剩余额度（#68 P6 组聚合）

    主账号：统计范围 = 自己 + 直属子账号（user_id IN family_member_ids）+ user_id IS NULL 服务器级行；
    子账号：仅统计自己，不返回 by_user。
    """
    from sqlalchemy import or_
    from app.db.database import async_session_factory
    from app.models.llm_usage import LlmUsage, LlmUsageLimit
    from app.models.user import User
    from app.services.family_service import is_sub_account, get_family_member_ids

    now = datetime.now()
    today0 = datetime(now.year, now.month, now.day)
    week0 = today0 - timedelta(days=6)
    month0 = datetime(now.year, now.month, 1)

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


@router.put("/llm-usage/limit")
async def update_llm_usage_limit(body: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """设置免费额度总量（tokens，仅主账号；0=清除总额设置）"""
    await _require_admin(user_id, lang)
    from app.db.database import async_session_factory
    from app.models.llm_usage import LlmUsageLimit
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


# ── 运行时 Feature Flag 开关（2026-08-18：DB 覆盖 + API 热更新，无需重启）──

@router.get('/feature-flags')
async def get_feature_flags(user_id: int = Depends(get_current_user_id), lang: str = Header(default='zh')):
    '''读取全部运行时 Feature Flag（主账号）；source: db=DB 覆盖 / default=硬编码默认'''
    await _require_admin(user_id, lang)
    from app.services import flag_service
    return {'status': 'ok', 'flags': await flag_service.get_all_flags()}


@router.put('/feature-flags/{key}')
async def update_feature_flag(key: str, data: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default='zh')):
    '''切换 Feature Flag（主账号）：写 DB + 热更新内存立即生效；未知 key 返回 404'''
    await _require_admin(user_id, lang)
    if 'enabled' not in data:
        raise HTTPException(status_code=400, detail='enabled required')
    from app.services import flag_service
    ok = await flag_service.set_runtime_flag(key, bool(data.get('enabled')))
    if not ok:
        raise HTTPException(status_code=404, detail='unknown feature flag: ' + key)
    return {'status': 'ok', 'key': key, 'enabled': bool(data.get('enabled'))}


# ── 备份一键导出（#54，2026-08-23：仅主账号；复用 scripts/backup.do_backup）──

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


@router.post("/backup")
async def trigger_backup(user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
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


@router.get("/backup/download")
async def download_backup(user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
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


# ── 用户级通知 WebSocket（#55 Android 前台服务保活：后台 isolate 维持长连接实时收推送）──
# 与 chat.py 的会话级 WS 不同：以 user_id 为粒度广播「新 AI 消息 / 主动消息」事件。

@router.websocket("/notifications/ws")
async def notifications_ws(websocket: WebSocket):
    """用户级通知长连接（?token= 鉴权）。

    服务端把主动消息 / 新 AI 消息事件实时推给该用户的所有连接（主 isolate + 后台 isolate），
    客户端可发 {"type": "ping"} 维持连接，服务端回 {"type": "pong"}。
    """
    from jose import jwt, JWTError
    from app.auth.config import auth_settings as _as

    token = websocket.query_params.get("token", "")
    try:
        payload = jwt.decode(token, _as.secret_key, algorithms=[_as.algorithm])
        ws_user_id = payload.get("user_id")
    except JWTError:
        ws_user_id = None
    if ws_user_id is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    from app.ws.notify_manager import register, unregister
    _uid = int(ws_user_id)
    await register(_uid, websocket)
    try:
        # 连接建立即上报一次，客户端可用它确认在线状态
        await websocket.send_json({"type": "connected"})
        while True:
            data = await websocket.receive_json()
            if isinstance(data, dict) and data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except Exception:
        pass
    finally:
        await unregister(_uid, websocket)
