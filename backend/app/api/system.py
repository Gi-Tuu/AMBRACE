"""系统状态 API + API 自主配置预留"""
from app.utils.logger import get_logger
from fastapi import APIRouter, Depends, HTTPException, Header
from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.db.database import get_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone

router = APIRouter(prefix="/api/v1/system", tags=["System"])
_logger = get_logger("api.system")


@router.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


def _get_lan_ip() -> str:
    """探测局域网 IP（UDP 路由法，不实际发包；失败返回空串）"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        pass
    try:
        import socket
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
        "version": "0.1.0",
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
    await db.commit()
    _logger.info("api-config updated user=%d enabled=%s base_url=%s provider=%s", user_id, bool(cfg.enabled), cfg.base_url, cfg.provider)
    return {"status": "ok", "enabled": bool(cfg.enabled), "configured": True}


# ── 服务器级全局 API 配置（开源部署：填一次全局 key，代码/.env 零密钥；仅主账号 user_id=1 可读写）──

def _require_admin(user_id: int, lang: str = "zh") -> None:
    from app.config import settings
    if user_id not in settings.admin_user_ids:
        raise HTTPException(status_code=403, detail=tr_lang(lang, "admin_config_only"))


@router.get("/api-config/server")
async def get_server_api_config(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """读取服务器级全局 API 配置（api_key 不回传明文）"""
    _require_admin(user_id, lang)
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
    _require_admin(user_id, lang)
    from app.models.api_config import ApiConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    result = await db.execute(select(ApiConfig).where(ApiConfig.user_id == SERVER_CONFIG_UID))
    cfg = result.scalar_one_or_none()
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
    _require_admin(user_id, lang)
    from app.agent.llm_client import SERVER_CONFIG_UID
    cfg = await _get_task_cfg(db, SERVER_CONFIG_UID, task)
    return _task_cfg_payload(cfg)


@router.put("/api-config/task/server/{task}")
async def update_server_task_api_config(task: str, data: dict, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入服务器级任务 LLM 配置（仅主账号；影响所有用户的该任务调用）"""
    _require_admin(user_id, lang)
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
    _require_admin(user_id, lang)
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
    _require_admin(user_id, lang)
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
    _require_admin(user_id, lang)
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
    _require_admin(user_id, lang)
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
    _require_admin(user_id, lang)
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
    _require_admin(user_id, lang)
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
    _require_admin(user_id, lang)
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
    _require_admin(user_id, lang)
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
    import re as _re
    from pathlib import Path

    changelog_path = Path(__file__).resolve().parents[3] / "docs" / "changelog.md"
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except Exception as e:
        _logger.warning("Changelog read failed: %s", e)
        return {"days": []}

    days_map: dict[str, dict] = {}
    order: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        m = _re.match(r"^##\s*(\d{4}-\d{2}-\d{2})\s*（(.+)）$", line)
        if m:
            date, title = m.group(1), m.group(2)
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
    return {"days": out[:30]}



# ── LLM token 用量与免费额度（2026-08-11：用量落库展示；总量仅主账号可设）──
@router.get("/llm-usage")
async def get_llm_usage(user_id: int = Depends(get_current_user_id)):
    """token 用量统计：今日/近7天/本月/累计 + 按模型汇总 + 剩余额度"""
    from app.config import settings
    from app.db.database import async_session_factory
    from app.models.llm_usage import LlmUsage, LlmUsageLimit

    now = datetime.now()
    today0 = datetime(now.year, now.month, now.day)
    week0 = today0 - timedelta(days=6)
    month0 = datetime(now.year, now.month, 1)

    async with async_session_factory() as db:
        rows = (await db.execute(select(LlmUsage))).scalars().all()
        limit_row = (await db.execute(
            select(LlmUsageLimit).where(LlmUsageLimit.id == 1)
        )).scalar_one_or_none()

    total = today = week = month = 0
    by_model: dict[str, int] = {}
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
        "can_edit_limit": user_id in settings.admin_user_ids,
    }


@router.put("/llm-usage/limit")
async def update_llm_usage_limit(body: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """设置免费额度总量（tokens，仅主账号；0=清除总额设置）"""
    _require_admin(user_id, lang)
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
