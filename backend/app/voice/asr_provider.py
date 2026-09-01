"""语音 ASR Provider 抽象（Phase 1，向后兼容、不改协议）

目标：把 voice/gateway 的 `_asr_text`（当前整段 faster-whisper）抽象为 provider 分发，
在「配置了流式 ASR 且启用且协议已确认」时走流式，否则整段 whisper（现状零变化）。
保留 asr_final 事件契约不变。

接口：
- ASRProvider                —— 抽象基类：transcribe（整段字节）＋ 可选 stream（增量）语义；
- LocalWhisperProvider       —— 内置，faster-whisper 整段（默认兜底，与现状一致）；
- DashScopeStreamASRProvider —— 百炼 Paraformer 流式（可选）。百炼实时语音识别 WS 协议与
  配置字段【待实测确认】——禁止盲猜字段，故 `available()` 恒为 False：即使配置了也
  不激活，一律回退 LocalWhisper，待协议实测后再接通 stream()。

未启用/未配置/协议未确认 → get_asr_provider() 返回 LocalWhisperProvider（零行为变化）。
"""
import asyncio
import tempfile
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from app.config import settings
from app.services import speech_service
from app.utils.logger import get_logger

_logger = get_logger("voice.asr")

# 百炼实时语音识别（Paraformer 流式）WS 协议是否已实测确认。
# 2026-08-31：协议/配置字段需在真机或脚本实测确认后才置 True 并接通 stream()；
# 在此之前即使配置了百炼也**不激活**，确保不盲猜字段、不破坏默认 whisper 链路。
_DASHSCOPE_STREAM_PROTOCOL_CONFIRMED = False


class ASRProvider(ABC):
    """语音识别 provider 抽象基类。

    transcribe(audio_bytes)：整段音频字节 → 文本（失败返回 None，与旧 _asr_text 契约一致）。
    supports_stream()/stream()：可选流式语义（DashScope/Sherpa 等增量识别用），基类未实现。
    available()：该 provider 当前是否可用（影响 get_asr_provider 分发）。
    """

    name: str = "base"

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes) -> str | None:
        """整段音频字节 → 文本（失败返回 None）。"""
        raise NotImplementedError

    def supports_stream(self) -> bool:
        return False

    async def stream(self, audio_chunks: "asyncio.Queue | type[None]") -> None:  # pragma: no cover - 基类桩
        raise NotImplementedError

    def available(self) -> bool:
        return True


class LocalWhisperProvider(ASRProvider):
    """内置本地 faster-whisper 整段转写（默认兜底，逐字节复刻旧 _asr_text 行为）。"""

    name = "local_whisper"

    async def transcribe(self, audio_bytes: bytes) -> str | None:
        """音频字节（m4a/wav）→ 临时文件 → faster-whisper 整段转写（失败返回 None）"""
        if not audio_bytes:
            return None
        tmp = Path(tempfile.gettempdir()) / f"voice_asr_{uuid.uuid4().hex[:8]}.m4a"
        tmp.write_bytes(audio_bytes)
        try:
            return await speech_service.transcribe(str(tmp), language="zh")
        finally:
            try:
                tmp.unlink()
            except Exception:
                pass


class DashScopeStreamASRProvider(ASRProvider):
    """百炼 Paraformer 流式 ASR（可选）。

    配置字段使用通用命名（base_url/api_key/model），**不盲猜**百炼实时识别 WS 的
    二进制帧格式与鉴权字段（app_key/token 等，待实测）。`available()` 恒为 False，
    因为 `_DASHSCOPE_STREAM_PROTOCOL_CONFIRMED = False`；配置启用也不激活，回退本地。
    """

    name = "dashscope_stream"

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}

    def available(self) -> bool:
        # 协议未实测确认前一律不可用（即使 enabled=True），回退 LocalWhisper。
        return bool(
            self.cfg.get("enabled")
            and _DASHSCOPE_STREAM_PROTOCOL_CONFIRMED
        )

    def supports_stream(self) -> bool:
        return True

    async def transcribe(self, audio_bytes: bytes) -> str | None:
        # TODO(待实测)：百炼 Paraformer 实时语音识别 WS 协议（连接鉴权、二进制 PCM 帧、
        # 分帧格式、事件回包）需在真机/脚本实测确认字段后才实现；此前禁止盲猜。
        # 未确认时该 provider 不会被 get_asr_provider 选中，本方法不会走到。
        raise NotImplementedError(
            "DashScopeStreamASRProvider: 百炼实时识别协议待实测确认，暂不可用（回退本地 whisper）"
        )


def asr_stream_config() -> dict:
    """读取服务器级流式 ASR 配置（settings 环境变量，与 TTS 的 SpeechConfig 表解耦）。

    未配置/未启用返回空 dict。字段命名通用（base_url/api_key/model），
    具体百炼实时识别的鉴权字段待实测后按需扩展。
    """
    if not getattr(settings, "asr_stream_enabled", False):
        return {}
    return {
        "enabled": True,
        "provider": getattr(settings, "asr_stream_provider", "") or "",
        "base_url": getattr(settings, "asr_stream_base_url", "") or "",
        "api_key": getattr(settings, "asr_stream_api_key", "") or "",
        "model": getattr(settings, "asr_stream_model", "") or "",
    }


def get_asr_provider() -> ASRProvider:
    """返回当前生效的 ASR provider：流式已配置+启用+协议已确认 → 流式；否则本地 whisper。"""
    cfg = asr_stream_config()
    if cfg:
        stream = DashScopeStreamASRProvider(cfg)
        if stream.available():
            _logger.info("Voice ASR using DashScopeStreamASRProvider")
            return stream
    return LocalWhisperProvider()
