"""各模态服务配置读写与连通性探针应用服务（A22 第二刀，2026-10-02）。

本模块自 ``app/application/system.py`` 逐字节搬入。边界＝各模态（api/vlm/image/speech/task）
服务配置读写 + 连通性探针 + 语音试听；不做用量统计、不碰备份与账号。

跨块调用（``_require_admin`` / ``_require_server_admin`` / ``_cfg_snapshot`` / ``_audit``
仍驻留 system.py）一律在函数内 ``from app.application import system as _sys`` 后走
``_sys.<name>``——不放模块顶层，避免与 system.py 的重导出形成导入环。
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import tr_lang
from app.utils.logger import get_logger

_logger = get_logger("application.system")


# 服务器级配置审计快照字段（api_key 由 admin_audit_service._dump 统一脱敏为 "***"，不进审计明文）
_API_CFG_FIELDS = ("base_url", "api_key", "model", "provider", "enabled")
_VLM_CFG_FIELDS = ("base_url", "api_key", "model", "enabled")
_IMAGE_CFG_FIELDS = ("provider", "base_url", "api_key", "model", "enabled", "daily_limit")
_SPEECH_CFG_FIELDS = ("provider", "base_url", "api_key", "model", "enabled")


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
    from app.application import system as _sys
    await _sys._require_admin(user_id, lang)
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
    from app.application import system as _sys
    await _sys._require_server_admin(user_id, lang)
    from app.application.llm_config_service import (
        get_or_create_server_modality_row,
        get_server_modality_row,
    )
    cfg = await get_server_modality_row(db, "llm")  # 四模态统一出口（账号独立 P1）
    is_new = cfg is None
    _before = _sys._cfg_snapshot(cfg, _API_CFG_FIELDS)
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
    await _sys._audit(db, user_id, "server.api_config.update", "modality:llm",
                 _before, _sys._cfg_snapshot(cfg, _API_CFG_FIELDS))
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
    from app.application import system as _sys
    await _sys._require_admin(user_id, lang)
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
    from app.application import system as _sys
    await _sys._require_server_admin(user_id, lang)
    from app.models.agent import TaskLlmConfig
    from app.agent.llm_client import SERVER_CONFIG_UID
    cfg = await _get_task_cfg(db, SERVER_CONFIG_UID, task)
    _before = _sys._cfg_snapshot(cfg, _API_CFG_FIELDS)
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
    await _sys._audit(db, user_id, "server.task_api_config.update", "task:" + task,
                 _before, _sys._cfg_snapshot(cfg, _API_CFG_FIELDS))
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
    from app.application import system as _sys
    await _sys._require_admin(user_id, lang)
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
    from app.application import system as _sys
    await _sys._require_server_admin(user_id, lang)
    from app.application.llm_config_service import (
        get_or_create_server_modality_row,
        get_server_modality_row,
    )
    # 审计前置快照：先读既有行（无行不建行，避免只读动作顺手造哨兵行）
    _before = _sys._cfg_snapshot(await get_server_modality_row(db, "image"), _IMAGE_CFG_FIELDS)
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
    await _sys._audit(db, user_id, "server.image_gen_config.update", "modality:image",
                 _before, _sys._cfg_snapshot(cfg, _IMAGE_CFG_FIELDS))
    await db.commit()
    _logger.info("server image-gen-config updated user=%d enabled=%s provider=%s", user_id, bool(cfg.enabled), cfg.provider)
    return {"status": "ok", "enabled": bool(cfg.enabled), "configured": True}


async def get_vlm_server_config(
    db: AsyncSession,
    user_id: int,
    lang: str,
):
    """读取服务器级识图配置（api_key 不回传明文）"""
    from app.application import system as _sys
    await _sys._require_admin(user_id, lang)
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
    from app.application import system as _sys
    await _sys._require_server_admin(user_id, lang)
    from app.application.llm_config_service import (
        get_or_create_server_modality_row,
        get_server_modality_row,
    )
    _before = _sys._cfg_snapshot(await get_server_modality_row(db, "vlm"), _VLM_CFG_FIELDS)
    cfg = await get_or_create_server_modality_row(db, "vlm")  # 四模态统一出口（账号独立 P1）
    for field in ("base_url", "api_key", "model"):
        if field in data:
            setattr(cfg, field, (data.get(field) or "").strip() or None)
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    await _sys._audit(db, user_id, "server.vlm_config.update", "modality:vlm",
                 _before, _sys._cfg_snapshot(cfg, _VLM_CFG_FIELDS))
    await db.commit()
    _logger.info("server vlm-config updated user=%d enabled=%s base_url=%s", user_id, bool(cfg.enabled), cfg.base_url)
    return {"status": "ok", "enabled": bool(cfg.enabled), "configured": True}


async def get_speech_server_config(
    db: AsyncSession,
    user_id: int,
    lang: str,
):
    """读取服务器级语音大模型配置（api_key 不回传明文）"""
    from app.application import system as _sys
    await _sys._require_admin(user_id, lang)
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
    from app.application import system as _sys
    await _sys._require_server_admin(user_id, lang)
    from app.application.llm_config_service import (
        get_or_create_server_modality_row,
        get_server_modality_row,
    )
    _before = _sys._cfg_snapshot(await get_server_modality_row(db, "speech"), _SPEECH_CFG_FIELDS)
    cfg = await get_or_create_server_modality_row(db, "speech")  # 四模态统一出口（账号独立 P1）
    for field in ("provider", "base_url", "api_key", "model"):
        if field in data:
            setattr(cfg, field, (data.get(field) or "").strip() or None)
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    await _sys._audit(db, user_id, "server.speech_config.update", "modality:speech",
                 _before, _sys._cfg_snapshot(cfg, _SPEECH_CFG_FIELDS))
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
