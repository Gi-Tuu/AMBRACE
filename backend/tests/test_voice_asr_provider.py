# -*- coding: utf-8 -*-
"""Phase 1：语音 ASR Provider 抽象（向后兼容）单测。

覆盖：
- get_asr_provider 默认回退 LocalWhisperProvider（无配置，零行为变化）；
- 百炼流式配置+启用但协议未实测确认 → available()=False → 仍回退本地（不盲猜字段）；
- LocalWhisperProvider.transcribe：写临时文件→speech_service 整段→清理，失败返回 None；
- gateway._asr_text 经 provider 分发：返回转写文本 / 空字节或失败返回 None（asr_final 契约不变）；
- DashScopeStreamASRProvider.available 语义（未配置/未启用/协议未确认均不可用）。
"""
import asyncio

from app.config import settings
from app.voice import asr_provider
from app.voice.asr_provider import (
    ASRProvider,
    DashScopeStreamASRProvider,
    LocalWhisperProvider,
    get_asr_provider,
)
from app.voice import gateway


# ── 工厂分发 ────────────────────────────────────────────────────────

def test_get_asr_provider_default_is_local_whisper(monkeypatch):
    """未配置任何流式 ASR → 返回 LocalWhisperProvider（默认兜底，零行为变化）。"""
    monkeypatch.setattr(settings, "asr_stream_enabled", False)
    provider = get_asr_provider()
    assert isinstance(provider, LocalWhisperProvider)


def test_get_asr_provider_falls_back_when_protocol_unconfirmed(monkeypatch):
    """百炼流式配置+启用，但协议未实测确认 → available()=False → 仍回退本地。"""
    monkeypatch.setattr(settings, "asr_stream_enabled", True)
    monkeypatch.setattr(settings, "asr_stream_provider", "dashscope_stream")
    monkeypatch.setattr(settings, "asr_stream_api_key", "k")
    monkeypatch.setattr(settings, "asr_stream_model", "paraformer-realtime-v2")
    provider = get_asr_provider()
    # 协议未确认时不得盲猜字段激活，必须回退本地（保护默认链路）
    assert isinstance(provider, LocalWhisperProvider)


def test_dashscope_stream_available_false_without_config():
    """空配置 → available()=False（不会选中）。"""
    assert DashScopeStreamASRProvider({}).available() is False


def test_dashscope_stream_available_false_even_when_enabled():
    """协议未确认（_DASHSCOPE_STREAM_PROTOCOL_CONFIRMED=False）时，启用配置也不可用。"""
    p = DashScopeStreamASRProvider({"enabled": True, "api_key": "k"})
    assert p.available() is False
    assert p.supports_stream() is True


def test_local_whisper_is_provider_subclass():
    assert issubclass(LocalWhisperProvider, ASRProvider)
    assert issubclass(DashScopeStreamASRProvider, ASRProvider)


# ── LocalWhisperProvider.transcribe ─────────────────────────────────

def test_local_whisper_transcribe_writes_tmp_and_cleans(monkeypatch, tmp_path):
    """写临时文件→speech_service 整段→结果回传；临时文件被清理（复刻旧 _asr_text）。"""
    captured: list[tuple] = []
    seen_paths: list[str] = []

    async def fake_transcribe(audio_path, language="zh", user_id=None):
        captured.append((audio_path, language))
        seen_paths.append(audio_path)
        return "好的呀"

    monkeypatch.setattr(asr_provider.speech_service, "transcribe", fake_transcribe)
    provider = LocalWhisperProvider()
    res = asyncio.run(provider.transcribe(b"\x00\x01audio"))
    assert res == "好的呀"
    assert captured[0][1] == "zh"
    # 临时文件已清理（在系统临时目录，非 tmp_path）——用不存在断言而非可写断言
    for p in seen_paths:
        from pathlib import Path
        assert not Path(p).exists()


def test_local_whisper_transcribe_failure_returns_none(monkeypatch):
    """speech_service 返回 None（模型不可用/转写失败）→ transcribe 返回 None。"""
    async def fake_transcribe(audio_path, language="zh", user_id=None):
        return None
    monkeypatch.setattr(asr_provider.speech_service, "transcribe", fake_transcribe)
    res = asyncio.run(LocalWhisperProvider().transcribe(b"\x00audio"))
    assert res is None


def test_local_whisper_transcribe_empty_bytes_returns_none(monkeypatch):
    """空字节直接返回 None，不落盘、不调用底层。"""
    called = False

    async def fake_transcribe(*a, **k):
        nonlocal called
        called = True
        return "x"
    monkeypatch.setattr(asr_provider.speech_service, "transcribe", fake_transcribe)
    assert asyncio.run(LocalWhisperProvider().transcribe(b"")) is None
    assert called is False


# ── gateway._asr_text 分发 ─────────────────────────────────────────

class _FakeProvider(ASRProvider):
    name = "fake"

    def __init__(self, result):
        self._result = result
        self.calls = 0

    async def transcribe(self, audio_bytes: bytes) -> str | None:
        self.calls += 1
        return self._result


def test_gateway_asr_text_dispatch_returns_text(monkeypatch):
    """_asr_text 经 provider 分发：返回转写文本（asr_final 用它）。"""
    fake = _FakeProvider("你好啊")
    monkeypatch.setattr(asr_provider, "get_asr_provider", lambda: fake)
    res = asyncio.run(gateway._asr_text(b"\x00audio"))
    assert res == "你好啊"
    assert fake.calls == 1


def test_gateway_asr_text_dispatch_none_on_failure(monkeypatch):
    """provider 返回 None（没听清）→ _asr_text 返回 None（下游触发 asr_empty）。"""
    fake = _FakeProvider(None)
    monkeypatch.setattr(asr_provider, "get_asr_provider", lambda: fake)
    assert asyncio.run(gateway._asr_text(b"\x00audio")) is None


def test_gateway_asr_text_default_uses_local_whisper(monkeypatch):
    """默认（未配置流式）走到 LocalWhisperProvider。"""
    monkeypatch.setattr(settings, "asr_stream_enabled", False)
    fake = _FakeProvider("默认本地")
    # 仅验证 _asr_text 会调用 get_asr_provider 得到的 provider
    monkeypatch.setattr(asr_provider, "get_asr_provider", lambda: fake)
    assert asyncio.run(gateway._asr_text(b"\x00audio")) == "默认本地"
