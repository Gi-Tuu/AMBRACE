"""语音转写服务：faster-whisper 本地 ASR（懒加载，首次转写时初始化；失败降级返回 None 不阻塞）"""
import asyncio
import threading

from app.config import settings
from app.utils.logger import get_logger

_logger = get_logger("services.speech")

MODEL_DIR = settings.PROJECT_ROOT / "models" / "whisper-small"

_model = None
_model_lock = threading.Lock()


def _resolve_model_dir() -> str:
    """定位 whisper 模型目录：优先 HF hub 缓存布局（models--Systran--faster-whisper-small/snapshots/<hash>/），
    否则直接用 MODEL_DIR（平铺 model.bin 场景）。"""
    cache_root = MODEL_DIR / "models--Systran--faster-whisper-small" / "snapshots"
    if cache_root.is_dir():
        snaps = sorted(p for p in cache_root.iterdir() if p.is_dir())
        if snaps:
            return str(snaps[-1])
    return str(MODEL_DIR)


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                try:
                    from faster_whisper import WhisperModel
                    _model = WhisperModel(_resolve_model_dir(), device="cpu", compute_type="int8")
                    _logger.info("Whisper model loaded from %s", MODEL_DIR)
                except Exception as e:
                    _logger.warning("Whisper model load failed (voice transcribe disabled): %s\n  语音转写为可选功能，需安装：pip install -r backend/requirements-voice.txt（README 详见语音一节）", e)
                    _model = False  # 加载失败标记，避免反复尝试
    return _model if _model is not False else None


async def transcribe(audio_path: str, language: str = "zh", user_id: int | None = None) -> str | None:
    """本地 ASR 转写。user_id 非空时受「语音转写」权限约束（ask/forbid 返回 None）"""
    if user_id is not None:
        try:
            from app.application import permission_service
            _mode = await permission_service.check_mode(user_id, permission_service.SCOPE_ASR)
            if _mode != "allow":
                return None
        except Exception:
            pass
    """转写音频为文本；失败/模型不可用返回 None"""
    model = await asyncio.to_thread(_get_model)
    if model is None:
        return None
    try:
        def _run():
            segments, _info = model.transcribe(audio_path, language=language, beam_size=1)
            return "".join(s.text for s in segments).strip()
        text = await asyncio.to_thread(_run)
        return text or None
    except Exception as e:
        _logger.warning("Voice transcribe failed %s: %s", audio_path, e)
        return None
