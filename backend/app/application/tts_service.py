"""AI 语音回复 TTS 服务：优先云端百炼 TTS（speech_configs 启用时），失败自动回退 edge-tts（免费）

2026-08-10 接入：
- 服务器级「语音大模型」配置（speech_configs，user_id=0）启用时，走 DashScope multimodal-generation HTTP 接口；
  先尝试配置的模型（如 qwen3-tts-vd-2026-01-26），若该模型 HTTP 不可用则自动用 qwen-tts（实测可用，返回 wav URL）；
- 均失败回退 edge-tts（微软 Edge 免费接口，mp3）；
- ASR（语音转写）仍走本地 faster-whisper（speech_service），云端 ASR 需 qwen-audio 系列模型再接入。
"""
import asyncio
import json
import re
import time as _time
import uuid
from pathlib import Path

from app.config import settings
from app.utils.logger import get_logger

_logger = get_logger("services.tts")

TTS_DIR = settings.PROJECT_ROOT / "data" / "uploads" / "tts"
# 音色：按角色性别选择（百炼 qwen-tts 音色 / edge-tts 音色）
# Phase 0 P0：百炼性别兜底用实测可用音色（女 Cherry / 男 Ethan）；预设见 VOICE_PRESETS。
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
# Phase 0 P0（2026-08-30 实测）：本部署 qwen-tts 可用音色仅 Cherry / Serena / Chelsie（女声）+
# Ethan（男声）；Serenai/Orion/Aria/Indigo/Clare/Morgan 等均 400。故女声预设映射这三档轮换，
# 男声预设统一 Ethan；方言（东北/陕西/粤语/台湾）百炼无法保留 → 走 edge-tts 兜底音色完整生效。
VOICE_PRESETS = {
    "xiaoxiao": {"label": "晓晓 · 自然女声", "gender": "female", "edge": "zh-CN-XiaoxiaoNeural", "dashscope": "Cherry"},
    "xiaoyi": {"label": "晓伊 · 年轻女声", "gender": "female", "edge": "zh-CN-XiaoyiNeural", "dashscope": "Serena"},
    "xiaobei": {"label": "晓北 · 东北女声", "gender": "female", "edge": "zh-CN-liaoning-XiaobeiNeural", "dashscope": "Chelsie"},
    "xiaoni": {"label": "晓妮 · 陕西女声", "gender": "female", "edge": "zh-CN-shaanxi-XiaoniNeural", "dashscope": "Cherry"},
    "hiugaai": {"label": "曉佳 · 粤语女声", "gender": "female", "edge": "zh-HK-HiuGaaiNeural", "dashscope": "Serena"},
    "hiumaan": {"label": "曉曼 · 粤语女声", "gender": "female", "edge": "zh-HK-HiuMaanNeural", "dashscope": "Chelsie"},
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


# ── 情绪→TTS 参数映射（Phase 0 P0，edge 先行）──────────────────────────
# 返回 (rate 倍率增量, pitch Hz 增量)。倍率增量叠加到 voice_rate（1.0=正常；
# -0.15=语速慢 15%），Hz 增量叠加到 voice_pitch（edge-tts 的音调单位为 Hz）。
# 仅 edge-tts 兜底链路生效；百炼 qwen-tts 参数目前仅支持 format/sample_rate（情感参数待实测）。
_EMOTION_EDGE_ADJUST: dict[str, tuple[float, float]] = {
    "sad": (-0.15, -10.0),      # 低落 → 语速 -15%、音调 -10Hz
    "happy": (0.10, 15.0),      # 开心 → +10%、+15Hz
    "excited": (0.10, 15.0),    # 激动 → +10%、+15Hz
    "calm": (-0.05, 0.0),       # 平静 → 语速 -5%、音调不变
    "tired": (-0.05, 0.0),      # 疲惫 → 语速 -5%、音调不变
    "angry": (0.05, 5.0),       # 生气 → +5%、+5Hz
}
# 中文/别名归一（首个小写单词或别名命中即映射）
_EMOTION_ALIASES: dict[str, str] = {
    "sad": "sad", "难过": "sad", "伤心": "sad", "低落": "sad", "委屈": "sad", "upset": "sad",
    "happy": "happy", "开心": "happy", "高兴": "happy", "喜悦": "happy",
    "excited": "excited", "兴奋": "excited", "激动": "excited",
    "calm": "calm", "平静": "calm", "冷静": "calm", "平淡": "calm",
    "tired": "tired", "疲惫": "tired", "累": "tired", "疲倦": "tired", "困": "tired", "劳累": "tired",
    "angry": "angry", "生气": "angry", "愤怒": "angry", "恼怒": "angry", "恼火": "angry",
}


def emotion_edge_adjust(emotion: str | None) -> tuple[float, float]:
    """按情绪映射 edge-tts 的（rate 倍率增量, pitch Hz 增量）；未知/None 返回 (0,0)（零行为变化）。

    sad → 语速 -15%、音调 -10Hz；excited/happy → +10%/+15Hz；
    calm/tired → 语速 -5%、音调不变；angry → +5%/+5Hz；其余/None → 不变。
    支持中英文标签、复合/带前缀（very_sad / sad,tired / 开心,疲惫）与大小写。
    """
    raw = (emotion or "").strip().lower()
    if not raw:
        return (0.0, 0.0)
    # 优先整体别名命中
    key = _EMOTION_ALIASES.get(raw, raw)
    if key in _EMOTION_EDGE_ADJUST:
        return _EMOTION_EDGE_ADJUST[key]
    # 复合/带分隔符：按常见分隔符切 token 逐个匹配
    for tok in re.split(r"[,;+/ _-]+", raw):
        if not tok:
            continue
        canonical = _EMOTION_ALIASES.get(tok, tok)
        if canonical in _EMOTION_EDGE_ADJUST:
            return _EMOTION_EDGE_ADJUST[canonical]
        # 子串兜底（如 sadness→sad、furious→angry）
        for name, delta in _EMOTION_EDGE_ADJUST.items():
            if name in tok:
                return delta
    return (0.0, 0.0)


async def _server_speech_config() -> dict:
    """读取服务器级语音大模型配置（speech_configs user_id=0）；未启用/异常返回空 dict"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.config import SpeechConfig
        async with async_session_factory() as db:
            cfg = (await db.execute(select(SpeechConfig).where(SpeechConfig.user_id == 0))).scalars().first()
            if not cfg or not cfg.enabled or not cfg.api_key:
                return {}
            return {
                "enabled": True,
                "base_url": cfg.base_url or "",
                "api_key": cfg.api_key or "",
                "model": cfg.model or "",
                "provider": cfg.provider or "",  # X3：provider 选择标签（与注册名精确匹配，空=内置兜底）
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



def _tts_runner_via_registry(cfg: dict):
    """X3（2026-08-31）：经 provider 注册表解析 kind=tts 合成器（flag provider_registry，默认开）。

    返回 async (text, voice, out_dir, fname) -> str|None；flag 关/注册表无命中/异常返回 None
    （调用方走内置直连 _synth_dashscope_sync，与旧链路逐字节一致；工厂体晚绑定本模块，
    monkeypatch 接缝不变）。cfg["provider"] 与注册名精确匹配可选中插件 TTS provider。
    """
    try:
        from app.agent.loop import AGENT_FLAGS as _af
        if not _af.get("provider_registry", True):
            return None
    except Exception:
        pass
    try:
        from app.providers.registry import resolve_provider
        hit = resolve_provider("tts", {"provider": cfg.get("provider")})
        if hit is None:
            return None
        _name, factory = hit
        return factory(cfg)
    except Exception as e:
        _logger.warning("provider registry tts resolve failed, fallback to builtin: %s", e)
        return None


async def synthesize(
    text: str,
    subdir: str,
    gender: str | None = None,
    voice: str | None = None,
    voice_rate: float | None = None,
    voice_pitch: float | None = None,
    user_id: int | None = None,
    emotion: str | None = None,
) -> str | None:
    """合成语音到 uploads/tts/{subdir}/，返回 /uploads/tts/... URL；失败返回 None（不阻塞主流程）。
    优先百炼（speech_configs 启用时）→ edge-tts 兜底。user_id 非空时受「语音回复」权限约束。

    emotion（Phase 0 P0，可空）：AI 的当前情绪标记（如 AgentState.emotional_state 的
    angry/sad/upset，或情感/感知派生的 sad/happy/excited/calm/tired 等；含中文别名）。
    - edge-tts 兜底链路：经 emotion_edge_adjust 映射为 (rate 倍率增量, pitch Hz 增量)，
      叠加到 voice_rate/voice_pitch 上（现有 rate/pitch 仍生效）；
    - 百炼链路：目前 parameters 仅 format/sample_rate，**未接入**情感参数（qwen-tts /
      qwen3-tts-vd 是否支持 instruction/emotion 等字段待实测，禁止盲猜字段名；见报告）。
    - emotion=None / 空 / 未知：边缘参数与旧行为完全一致（零行为变化）。
    """
    if user_id is not None:
        try:
            from app.application import permission_service
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
    # Phase 0 P0：按情绪映射叠加（emotion=None/未知时增量为 0，参数与旧行为一致）
    _rate_delta, _pitch_delta = emotion_edge_adjust(emotion)
    edge_rate = f"{max(0.5, min(2.0, (voice_rate or 1.0) + _rate_delta)) - 1.0:+.0%}"
    edge_pitch = f"{max(-50.0, min(50.0, (voice_pitch or 0.0) + _pitch_delta)):+.0f}Hz"

    # 1) 云端 TTS（X3：经 provider 注册口解析实现；flag 关/无命中回退内置百炼直连）
    cfg = await _server_speech_config()
    if cfg.get("enabled"):
        voice = dash_voice
        fname = f"{ts}_{uid}.wav"
        runner = _tts_runner_via_registry(cfg)
        if runner is not None:
            path = await runner(clean, voice, sub, fname)
        else:
            path = await asyncio.to_thread(_synth_dashscope_sync, clean, voice, cfg, sub, fname)
        if path:
            return f"/uploads/tts/{subdir}/{Path(path).name}"
        # F8 回退观测：云端已启用但未产出（含内部降级/失败）→ 落到下方 edge 兜底
        try:
            from app.memory.observability import obs_event
            obs_event(None, "fb_tts_cloud_miss", {"voice": str(voice)[:30]})
        except Exception:
            pass

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
