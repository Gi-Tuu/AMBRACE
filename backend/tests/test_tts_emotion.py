# -*- coding: utf-8 -*-
"""Phase 0 P0：情绪→TTS 参数映射（edge 先行）与回归保护。

覆盖：
- emotion_edge_adjust 纯函数映射：sad/excited/happy/calm/tired/angry、None/空/未知、
  中英文别名（upset/难过/开心/兴奋/平静/疲惫/生气）、复合/大小写；
- synthesize 传 emotion 时 edge_tts.Communicate 收到叠加后的 rate/pitch（mock）；
- synthesize emotion=None 时 edge 参数与旧行为完全一致（回归）；
- 百炼路径 emotion 传入不改变 payload（parameters 仅 format/sample_rate）。
"""
import asyncio
import json
from pathlib import Path

from app.application import tts_service


# ── 纯函数：emotion_edge_adjust ─────────────────────────────────────

def test_emotion_edge_adjust_known_labels():
    assert tts_service.emotion_edge_adjust("sad") == (-0.15, -10.0)
    assert tts_service.emotion_edge_adjust("excited") == (0.10, 15.0)
    assert tts_service.emotion_edge_adjust("happy") == (0.10, 15.0)
    assert tts_service.emotion_edge_adjust("calm") == (-0.05, 0.0)
    assert tts_service.emotion_edge_adjust("tired") == (-0.05, 0.0)
    assert tts_service.emotion_edge_adjust("angry") == (0.05, 5.0)


def test_emotion_edge_adjust_none_unknown_unchanged():
    # None / 空 / 未知 / 中性 → (0,0)：不改变 rate/pitch（零行为变化）
    assert tts_service.emotion_edge_adjust(None) == (0.0, 0.0)
    assert tts_service.emotion_edge_adjust("") == (0.0, 0.0)
    assert tts_service.emotion_edge_adjust("   ") == (0.0, 0.0)
    assert tts_service.emotion_edge_adjust("unknown") == (0.0, 0.0)
    assert tts_service.emotion_edge_adjust("neutral") == (0.0, 0.0)


def test_emotion_edge_adjust_aliases():
    # 中文/别名归一到对应英文标签
    assert tts_service.emotion_edge_adjust("upset") == (-0.15, -10.0)  # → sad
    assert tts_service.emotion_edge_adjust("难过") == (-0.15, -10.0)
    assert tts_service.emotion_edge_adjust("低落") == (-0.15, -10.0)
    assert tts_service.emotion_edge_adjust("委屈") == (-0.15, -10.0)
    assert tts_service.emotion_edge_adjust("开心") == (0.10, 15.0)
    assert tts_service.emotion_edge_adjust("高兴") == (0.10, 15.0)
    assert tts_service.emotion_edge_adjust("兴奋") == (0.10, 15.0)
    assert tts_service.emotion_edge_adjust("激动") == (0.10, 15.0)
    assert tts_service.emotion_edge_adjust("平静") == (-0.05, 0.0)
    assert tts_service.emotion_edge_adjust("疲惫") == (-0.05, 0.0)
    assert tts_service.emotion_edge_adjust("累") == (-0.05, 0.0)
    assert tts_service.emotion_edge_adjust("生气") == (0.05, 5.0)
    assert tts_service.emotion_edge_adjust("愤怒") == (0.05, 5.0)


def test_emotion_edge_adjust_composite_and_case():
    assert tts_service.emotion_edge_adjust("HAPPY") == (0.10, 15.0)   # 大小写不敏感
    assert tts_service.emotion_edge_adjust("very_sad") == (-0.15, -10.0)
    assert tts_service.emotion_edge_adjust("sad,tired") == (-0.15, -10.0)
    assert tts_service.emotion_edge_adjust("开心,疲惫") == (0.10, 15.0)


# ── synthesize：edge 兜底 + 情绪叠加（mock edge_tts.Communicate）────

class _FakeCommunicate:
    """记录 edge_tts.Communicate 收到的参数；save 写一个合法（>=200B）的假 mp3。"""
    calls: list[tuple] = []

    def __init__(self, text, voice, rate, pitch):
        self.text, self.voice, self.rate, self.pitch = text, voice, rate, pitch
        _FakeCommunicate.calls.append((text, voice, rate, pitch))

    async def save(self, path):
        Path(path).write_bytes(b"x" * 300)


async def _synthesize_edge(monkeypatch, tmp_path, **kw):
    """跑到 edge-tts 兜底链路（禁用百炼），返回 (结果, 最近一次 Communicate 调用参数)。"""
    _FakeCommunicate.calls.clear()
    monkeypatch.setattr(tts_service, "TTS_DIR", Path(tmp_path))
    monkeypatch.setattr("edge_tts.Communicate", _FakeCommunicate)

    async def _no_cfg():
        return {}
    monkeypatch.setattr(tts_service, "_server_speech_config", _no_cfg)

    res = await tts_service.synthesize(text="你好。", subdir="s", **kw)
    return res, _FakeCommunicate.calls[-1] if _FakeCommunicate.calls else None


def test_synthesize_edge_emotion_stacks_rate_pitch(monkeypatch, tmp_path):
    """emotion 叠加到现有 voice_rate/voice_pitch：sad → rate -15%、pitch -10Hz。"""
    res, call = asyncio.run(_synthesize_edge(
        monkeypatch, tmp_path,
        voice_rate=1.2, voice_pitch=5.0, emotion="sad",
    ))
    assert res is not None
    assert res.startswith("/uploads/tts/s/")
    assert call is not None
    text, voice, rate, pitch = call
    assert rate == "+5%"     # (1.2 - 0.15) - 1.0 = +5%
    assert pitch == "-5Hz"   # 5 - 10 = -5Hz


def test_synthesize_edge_emotion_excited_stacks(monkeypatch, tmp_path):
    """excited → rate +10%、pitch +15Hz；叠加在现有值上。"""
    _, call = asyncio.run(_synthesize_edge(
        monkeypatch, tmp_path, voice_rate=1.0, voice_pitch=0.0, emotion="excited",
    ))
    assert call is not None
    assert call[2] == "+10%"
    assert call[3] == "+15Hz"


def test_synthesize_edge_emotion_none_matches_legacy(monkeypatch, tmp_path):
    """回归：emotion=None（默认）时 edge 参数与旧行为完全一致。"""
    # 旧行为：edge_rate = clamp(voice_rate)-1.0；edge_pitch = clamp(voice_pitch) Hz
    res, call = asyncio.run(_synthesize_edge(
        monkeypatch, tmp_path, voice_rate=1.0, voice_pitch=0.0, emotion=None,
    ))
    assert res is not None
    assert call is not None
    assert call[2] == "+0%"
    assert call[3] == "+0Hz"

    # 非默认 rate/pitch 同样回归（emotion=None）
    _, call2 = asyncio.run(_synthesize_edge(
        monkeypatch, tmp_path, voice_rate=1.5, voice_pitch=30.0, emotion=None,
    ))
    assert call2[2] == f"{0.5:+.0%}"     # +50%
    assert call2[3] == f"{30:+.0f}Hz"    # +30Hz

    # 未知情绪也回归（不变）
    _, call3 = asyncio.run(_synthesize_edge(
        monkeypatch, tmp_path, voice_rate=1.5, voice_pitch=30.0, emotion="whatever",
    ))
    assert call3[2] == "+50%"
    assert call3[3] == "+30Hz"


# ── synthesize：百炼路径 payload 不受 emotion 影响 ──────────────────

def test_dashscope_payload_unchanged_by_emotion(monkeypatch, tmp_path):
    """百炼链路：emotion 不改变 dashscope payload（parameters 仅 format/sample_rate）。"""
    captured: list[bytes] = []

    class _Resp:
        def __init__(self, data):
            self._d = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._d

    def fake_urlopen(req, timeout=None, **kw):
        captured.append(req.data)
        if getattr(req, "method", None) == "POST":
            body = {"output": {"audio": {"url": "http://example.com/a.wav"}}}
            return _Resp(json.dumps(body).encode("utf-8"))
        return _Resp(b"x" * 300)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    cfg = {"enabled": True, "base_url": "https://x.example.com/v1", "api_key": "k", "model": "qwen-tts"}
    # 用 emotion 参数调用底层同步函数（函数签名不含 emotion，验证不凭空加字段）
    out = tts_service._synth_dashscope_sync("你好。", "Cherry", cfg, Path(tmp_path), "a.wav")
    assert out is not None
    post_payloads = [json.loads(b) for b in captured if b]
    assert post_payloads, "should have issued at least one dashscope POST"
    for payload in post_payloads:
        assert payload["parameters"] == {"format": "wav", "sample_rate": 24000}
        assert set(payload["input"].keys()) == {"text", "voice"}
        assert "emotion" not in payload["input"]
        assert "emotion" not in payload["parameters"]
        assert "emotion" not in payload


def test_synth_dashscope_sync_signature_has_no_emotion(monkeypatch, tmp_path):
    """回归：_synth_dashscope_sync 的函数签名与现状一致（不接 emotion，杜绝盲猜字段）。"""
    import inspect
    sig = inspect.signature(tts_service._synth_dashscope_sync)
    assert "emotion" not in sig.parameters
    # 返回 (rate 增量, pitch 增量) 的纯函数存在且可被 synthesize 使用
    assert hasattr(tts_service, "emotion_edge_adjust")


# ── 接入点贯通：emotion 从调用方透传到 synthesize ─────────────────

def test_synth_stream_block_passes_emotion(monkeypatch):
    """nodes._synth_stream_block：把 state['emotional_state'] 透传给 synthesize。"""
    from app.agent import nodes
    calls: list[tuple] = []

    async def fake_synthesize(*a, **k):
        calls.append((a, k))
        return "/uploads/tts/x.mp3"
    monkeypatch.setattr("app.application.tts_service.synthesize", fake_synthesize)

    state = {"voice_params": {"gender": "female", "voice": "xiaoyi"},
             "tts_subdir": "7", "user_id": 1, "emotional_state": "sad"}
    res = asyncio.run(nodes._synth_stream_block("你好。", state))
    assert res == "/uploads/tts/x.mp3"
    assert calls[0][1]["emotion"] == "sad"


def test_synth_stream_block_emotion_empty_is_none(monkeypatch):
    """nodes._synth_stream_block：emotional_state 空/缺失时透传 None（零行为变化）。"""
    from app.agent import nodes
    calls: list[tuple] = []

    async def fake_synthesize(*a, **k):
        calls.append((a, k))
        return "/uploads/tts/x.mp3"
    monkeypatch.setattr("app.application.tts_service.synthesize", fake_synthesize)

    for emotional in ("", None):
        state = {"voice_params": {}, "tts_subdir": "7", "user_id": 1, "emotional_state": emotional}
        asyncio.run(nodes._synth_stream_block("你好。", state))
        assert calls[-1][1]["emotion"] is None


def test_synthesize_chunks_tts_passes_emotion(monkeypatch):
    """streaming._synthesize_chunks_tts：把 emotion 透传给 synthesize（批量回退路径）。"""
    from app.application.chat import streaming
    calls: list[tuple] = []

    async def fake_synthesize(*a, **k):
        calls.append((a, k))
        return "/uploads/tts/x.mp3"
    monkeypatch.setattr("app.application.tts_service.synthesize", fake_synthesize)

    async def fake_load(cid):
        return {"gender": "male", "voice": "yunxi", "voice_rate": 1.0, "voice_pitch": 0.0}
    monkeypatch.setattr("app.voice.voice_mode.load_character_voice_params", fake_load)

    res = asyncio.run(streaming._synthesize_chunks_tts(["你好。"], 5, 9, 1, emotion="angry"))
    assert res == ["/uploads/tts/x.mp3"]
    assert calls[0][1]["emotion"] == "angry"
