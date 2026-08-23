"""AI 语音回复 TTS 服务：优先云端百炼 TTS（speech_configs 启用时），失败自动回退 edge-tts（免费）

2026-08-10 接入：
- 服务器级「语音大模型」配置（speech_configs，user_id=0）启用时，走 DashScope multimodal-generation HTTP 接口；
  先尝试配置的模型（如 qwen3-tts-vd-2026-01-26），若该模型 HTTP 不可用则自动用 qwen-tts（实测可用，返回 wav URL）；
- 均失败回退 edge-tts（微软 Edge 免费接口，mp3）；
- ASR（语音转写）仍走本地 faster-whisper（speech_service），云端 ASR 需 qwen-audio 系列模型再接入。
"""
import asyncio
import json
import time as _time
import uuid
from pathlib import Path

from app.config import settings
from app.utils.logger import get_logger

_logger = get_logger("services.tts")

TTS_DIR = settings.PROJECT_ROOT / "data" / "uploads" / "tts"
# 音色：按角色性别选择（百炼 qwen-tts 音色 / edge-tts 音色）
_DASHSCOPE_VOICES = {
    "male": "Ethan",
    "female": "Cherry",
    None: "Cherry",
}
_EDGE_VOICES = {
    "male": "zh-CN-YunxiNeural",
    "female": "zh-CN-XiaoxiaoNeural",
    None: "zh-CN-XiaoxiaoNeural",
}
# 音色预设库（2026-08-11 自定义声色）：key → label / 性别 / edge-tts 音色 / dashscope 近似音色
VOICE_PRESETS = {
    "xiaoxiao": {"label": "晓晓 · 自然女声", "gender": "female", "edge": "zh-CN-XiaoxiaoNeural", "dashscope": "Cherry"},
    "xiaoyi": {"label": "晓伊 · 年轻女声", "gender": "female", "edge": "zh-CN-XiaoyiNeural", "dashscope": "Cherry"},
    "xiaobei": {"label": "晓北 · 东北女声", "gender": "female", "edge": "zh-CN-liaoning-XiaobeiNeural", "dashscope": "Cherry"},
    "xiaoni": {"label": "晓妮 · 陕西女声", "gender": "female", "edge": "zh-CN-shaanxi-XiaoniNeural", "dashscope": "Cherry"},
    "hiugaai": {"label": "曉佳 · 粤语女声", "gender": "female", "edge": "zh-HK-HiuGaaiNeural", "dashscope": "Cherry"},
    "hiumaan": {"label": "曉曼 · 粤语女声", "gender": "female", "edge": "zh-HK-HiuMaanNeural", "dashscope": "Cherry"},
    "hsiaochen": {"label": "曉臻 · 台湾女声", "gender": "female", "edge": "zh-TW-HsiaoChenNeural", "dashscope": "Cherry"},
    "yunxi": {"label": "云希 · 青年男声", "gender": "male", "edge": "zh-CN-YunxiNeural", "dashscope": "Ethan"},
    "yunjian": {"label": "云健 · 磁性男声", "gender": "male", "edge": "zh-CN-YunjianNeural", "dashscope": "Ethan"},
    "yunyang": {"label": "云扬 · 新闻男声", "gender": "male", "edge": "zh-CN-YunyangNeural", "dashscope": "Ethan"},
    "yunfeng": {"label": "云枫 · 成熟男声", "gender": "male", "edge": "zh-CN-YunfengNeural", "dashscope": "Ethan"},
    "wanlung": {"label": "雲龍 · 粤语男声", "gender": "male", "edge": "zh-HK-WanLungNeural", "dashscope": "Ethan"},
}
MAX_TEXT_CHARS = 500  # 单条回复合成上限（超出截断，控制时长与存储）
_DASHSCOPE_TTS_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
# 兜底模型：配置的模型（qwen3-tts-vd 等）HTTP 不可用时自动降级
# - 私有 MaaS 端点（speech_configs.base_url 指向）用 qwen-tts-2025-05-22（2026-08-12 实测可用）
# - 公开 dashscope 端点用 qwen-tts（2026-08-10 实测可用）
_FALLBACK_TTS_MODEL = "qwen-tts"
_PRIVATE_FALLBACK_TTS_MODEL = "qwen-tts-2025-05-22"
# 模型失败冷却（2026-08-12 Phase B）：HTTP 400（配置/参数问题）后 5 分钟内不再重试，
# 避免每句合成都先白等一次失败再降级，拖慢语音首字延迟
_TTS_MODEL_COOLDOWN: dict[str, float] = {}
_TTS_COOLDOWN_SECONDS = 300


def _tts_model_blocked(model: str) -> bool:
    return _TTS_MODEL_COOLDOWN.get(model, 0.0) > _time.time()


def _tts_model_block(model: str) -> None:
    _TTS_MODEL_COOLDOWN[model] = _time.time() + _TTS_COOLDOWN_SECONDS


def _voice_for(gender: str | None, voices: dict) -> str:
    g = (gender or "").strip().lower()
    if g in ("male", "男", "m"):
        return voices["male"]
    if g in ("female", "女", "f"):
        return voices["female"]
    return voices[None]


async def _server_speech_config() -> dict:
    """读取服务器级语音大模型配置（speech_configs user_id=0）；未启用/异常返回空 dict"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.speech_config import SpeechConfig
        async with async_session_factory() as db:
            cfg = (await db.execute(select(SpeechConfig).where(SpeechConfig.user_id == 0))).scalars().first()
            if not cfg or not cfg.enabled or not cfg.api_key:
                return {}
            return {
                "enabled": True,
                "base_url": cfg.base_url or "",
                "api_key": cfg.api_key or "",
                "model": cfg.model or "",
            }
    except Exception as e:
        _logger.warning("read speech config failed: %s", e)
        return {}


def _tts_endpoints(cfg: dict) -> list[tuple[str, list[str]]]:
    """TTS 端点与模型组合（2026-08-12 修复：使用配置的 base_url，不再硬编码公开端点）。

    配置 base_url 为私有 MaaS 端点（compatible-mode 网关）时，原生 TTS 接口在其
    根主机 /api/v1/services/aigc/multimodal-generation/generation；该私有端点上的
    可用模型为配置模型（qwen3-tts-vd 等，实测 400）+ qwen-tts-2025-05-22（实测可用）。
    公开 dashscope 端点保留 qwen-tts 兜底。
    """
    from urllib.parse import urlparse
    cfg_model = cfg.get("model") or ""
    result: list[tuple[str, list[str]]] = []
    if cfg.get("base_url"):
        try:
            p = urlparse(cfg["base_url"])
            if p.scheme and p.netloc:
                private = (
                    f"{p.scheme}://{p.netloc}"
                    "/api/v1/services/aigc/multimodal-generation/generation"
                )
                result.append((private, [cfg_model, _PRIVATE_FALLBACK_TTS_MODEL]))
        except Exception:
            pass
    result.append((_DASHSCOPE_TTS_ENDPOINT, [_FALLBACK_TTS_MODEL]))
    return result


def _synth_dashscope_sync(text: str, voice: str, cfg: dict, target_dir: Path, fname: str) -> str | None:
    """同步调 DashScope TTS（multimodal-generation），返回本地文件路径；全部模型失败返回 None。

    按 _tts_endpoints 顺序尝试（配置私有端点 → 公开端点），HTTP 400/空音频自动降级下一模型。
    """
    import urllib.request
    import urllib.error

    headers = {"Authorization": f"Bearer {cfg.get('api_key') or ''}", "Content-Type": "application/json"}
    for endpoint, models in _tts_endpoints(cfg):
        for model in models:
            if not model or _tts_model_blocked(model):
                continue
            try:
                payload = {
                    "model": model,
                    "input": {"text": text, "voice": voice},
                    "parameters": {"format": "wav", "sample_rate": 24000},
                }
                req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=60) as r:
                    resp = json.loads(r.read().decode("utf-8", "replace"))
                au = (resp.get("output") or {}).get("audio") or {}
                url = str(au.get("url") or "")
                if not url.startswith("http"):
                    _logger.warning("DashScope TTS %s: no audio url", model)
                    continue
                dreq = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(dreq, timeout=60) as dr:
                    raw = dr.read()
                if len(raw) < 200:
                    _logger.warning("DashScope TTS %s: empty audio", model)
                    continue
                path = target_dir / fname
                path.write_bytes(raw)
                if path.stat().st_size < 200:
                    continue
                _logger.info("DashScope TTS ok: model=%s voice=%s size=%d", model, voice, path.stat().st_size)
                return str(path)
            except urllib.error.HTTPError as e:
                _logger.warning("DashScope TTS %s HTTP %s: %s", model, e.code, (e.read() or b"")[:200].decode("utf-8", "replace"))
                if e.code == 400:
                    _tts_model_block(model)  # 参数/配置问题，短期内不重复尝试
                continue
            except Exception as e:
                _logger.warning("DashScope TTS %s failed: %s", model, e)
                continue
    return None



async def synthesize(
    text: str,
    subdir: str,
    gender: str | None = None,
    voice: str | None = None,
    voice_rate: float | None = None,
    voice_pitch: float | None = None,
    user_id: int | None = None,
) -> str | None:
    """合成语音到 uploads/tts/{subdir}/，返回 /uploads/tts/... URL；失败返回 None（不阻塞主流程）。
    优先百炼（speech_configs 启用时）→ edge-tts 兜底。user_id 非空时受「语音回复」权限约束。"""
    if user_id is not None:
        try:
            from app.services import permission_service
            _mode = await permission_service.check_mode(user_id, permission_service.SCOPE_TTS)
            if _mode != "allow":
                return None
        except Exception:
            pass
    clean = (text or "").strip().replace("\n", " ")[:MAX_TEXT_CHARS]
    if not clean:
        return None
    sub = TTS_DIR / str(subdir)
    sub.mkdir(parents=True, exist_ok=True)
    ts = _time.strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    # 自定义声色：音色 key → 预设；无则按性别默认（2026-08-11）
    preset = VOICE_PRESETS.get(voice or "") if voice else None
    dash_voice = preset["dashscope"] if preset else _voice_for(gender, _DASHSCOPE_VOICES)
    edge_voice = preset["edge"] if preset else _voice_for(gender, _EDGE_VOICES)
    # 语速/语调仅 edge-tts 兜底链路生效（百炼 qwen-tts 参数仅支持 format/sample_rate）
    edge_rate = f"{max(0.5, min(2.0, voice_rate or 1.0)) - 1.0:+.0%}"
    edge_pitch = f"{max(-50.0, min(50.0, voice_pitch or 0.0)):+.0f}Hz"

    # 1) 百炼 TTS
    cfg = await _server_speech_config()
    if cfg.get("enabled"):
        voice = dash_voice
        fname = f"{ts}_{uid}.wav"
        path = await asyncio.to_thread(_synth_dashscope_sync, clean, voice, cfg, sub, fname)
        if path:
            return f"/uploads/tts/{subdir}/{Path(path).name}"

    # 2) edge-tts 兜底
    try:
        import edge_tts
    except Exception as e:
        _logger.warning("edge-tts not available: %s", e)
        return None
    fname_mp3 = f"{ts}_{uid}.mp3"
    path_mp3 = sub / fname_mp3
    try:
        c = edge_tts.Communicate(clean, voice=edge_voice, rate=edge_rate, pitch=edge_pitch)
        await c.save(str(path_mp3))
        if not path_mp3.exists() or path_mp3.stat().st_size < 200:
            raise RuntimeError("empty audio")
        return f"/uploads/tts/{subdir}/{fname_mp3}"
    except Exception as e:
        _logger.warning("TTS synthesize failed: %s", e)
        try:
            if path_mp3.exists():
                path_mp3.unlink()
        except Exception:
            pass
        return None
