# -*- coding: utf-8 -*-
"""Phase 2 P2-1：轻量角色情感规则器（零 LLM）与 emotional_state 写入/透传。

覆盖：
- ai_emotion.emotion_from_character_states：sad/angry/tired/happy、阈值边界、缺省安全、
  冲突优先级、用户负向情绪抑制、标签集与 emotion_edge_adjust 对齐；
- 文本链路：chat_service._resolve_emotional_state 由 get_character_states 推出标签
  （写入 final_state 前的调用），异常返回空串（零行为变化）；
- 语音链路：gateway._process_utterance 写 state['emotional_state'] 并把 emotion 传给
  _synthesize_sentence；打断后不再 send tts_audio。
"""
import asyncio

from app.services import chat_service as cs
from app.services import tts_service
from app.utils import ai_emotion
from app.voice import gateway


# ── 纯函数：emotion_from_character_states ─────────────────────────

def test_emotion_from_sad():
    assert ai_emotion.emotion_from_character_states({"mood": 30, "anger": 50, "fatigue": 50}) == "sad"


def test_emotion_from_angry():
    assert ai_emotion.emotion_from_character_states({"mood": 50, "anger": 80, "fatigue": 50}) == "angry"


def test_emotion_from_tired():
    assert ai_emotion.emotion_from_character_states({"mood": 50, "anger": 50, "fatigue": 90}) == "tired"


def test_emotion_from_happy():
    assert ai_emotion.emotion_from_character_states({"mood": 85, "anger": 50, "fatigue": 50}) == "happy"


def test_emotion_default_neutral_is_empty():
    # 全中性/无匹配 → 空串（TTS 零行为变化）
    assert ai_emotion.emotion_from_character_states({"mood": 50, "anger": 50, "fatigue": 50}) == ""
    assert ai_emotion.emotion_from_character_states({}) == ""


def test_emotion_threshold_boundaries():
    # mood=40 → 不触发 sad（须 <40）；mood=70 → 不触发 happy（须 >70）
    assert ai_emotion.emotion_from_character_states({"mood": 40, "anger": 50, "fatigue": 50}) == ""
    # anger=70 → 不触发 angry（须 >70）
    assert ai_emotion.emotion_from_character_states({"mood": 50, "anger": 70, "fatigue": 50}) == ""
    # fatigue=70 → 不触发 tired（须 >70）
    assert ai_emotion.emotion_from_character_states({"mood": 50, "anger": 50, "fatigue": 70}) == ""
    # 临界以下/以上恰好触发
    assert ai_emotion.emotion_from_character_states({"mood": 39, "anger": 50, "fatigue": 50}) == "sad"
    assert ai_emotion.emotion_from_character_states({"mood": 71, "anger": 50, "fatigue": 50}) == "happy"


def test_emotion_missing_dims_safe():
    # 缺省维度按 50（中性）处理，不抛异常
    assert ai_emotion.emotion_from_character_states({"mood": None}) == ""
    assert ai_emotion.emotion_from_character_states({"mood": "abc"}) == ""
    assert ai_emotion.emotion_from_character_states(None) == ""


def test_emotion_direct_kwargs():
    # 关键字覆盖（单测/无 DB 场景）
    assert ai_emotion.emotion_from_character_states(mood=20) == "sad"
    assert ai_emotion.emotion_from_character_states(anger=90) == "angry"
    assert ai_emotion.emotion_from_character_states(fatigue=95) == "tired"
    assert ai_emotion.emotion_from_character_states(mood=80) == "happy"


def test_emotion_precedence_negative_first():
    # 冲突：mood<40（sad）且 anger>70（angry）→ 取 angry（angry > sad）
    assert ai_emotion.emotion_from_character_states({"mood": 20, "anger": 90, "fatigue": 50}) == "angry"


def test_emotion_user_negative_suppresses_happy():
    # 用户明显低落时，不让 AI 配「开心/激动」音色；抑制正向但不捏造负向
    assert ai_emotion.emotion_from_character_states(
        {"mood": 85, "anger": 50, "fatigue": 50}, user_emotion_hint="我好难过"
    ) == ""
    # 无用户提示 → 正常 happy
    assert ai_emotion.emotion_from_character_states({"mood": 85, "anger": 50, "fatigue": 50}) == "happy"


def test_emotion_labels_align_with_tts_emotion_edge_adjust():
    """规则器输出的标签集必须与 P0 emotion_edge_adjust 对齐（sad/happy/excited/calm/tired/angry）。"""
    for label in ("sad", "happy", "angry", "tired"):
        # 每个规则器可产出的标签都在 emotion_edge_adjust 的映射范围内（返回非 (0,0) 修改）
        assert label in tts_service._EMOTION_EDGE_ADJUST
    # 空串/未知 → 零变化
    assert tts_service.emotion_edge_adjust("") == (0.0, 0.0)


# ── 文本链路：_resolve_emotional_state 写入（mock）────────────────

def test_resolve_emotional_state_writes_from_character_states(monkeypatch):
    async def _fake_get_states(cid):
        return {"mood": 30, "anger": 50, "fatigue": 50}
    monkeypatch.setattr("app.services.character_state_service.get_character_states", _fake_get_states)
    assert asyncio.run(cs._resolve_emotional_state(7)) == "sad"


def test_resolve_emotional_state_empty_on_exception(monkeypatch):
    async def _boom(cid):
        raise RuntimeError("db down")
    monkeypatch.setattr("app.services.character_state_service.get_character_states", _boom)
    # 异常 → 空串（零行为变化）
    assert asyncio.run(cs._resolve_emotional_state(7)) == ""


# ── 语音链路：gateway._process_utterance 写 emotional_state + 透传 ──

class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)


def _voice_state(**over):
    st = {
        "user_id": 1, "session_id": 9, "character_id": 3,
        "voice_params": {"gender": "female", "voice": "xiaoyi"},
        "thinking_audio_url": None, "last_interrupted": None,
        "sent_text": "", "interrupt": False, "turn_id": 1,
        "emotional_state": "", "vad_ignored": 0,
    }
    st.update(over)
    return st


def test_process_utterance_writes_emotion_and_passes_to_tts(monkeypatch):
    """语音回合：写 state['emotional_state']=sad，并把 emotion 传给 _synthesize_sentence。"""
    synth_calls: list[dict] = []

    async def _fake_asr(audio_bytes):
        return "我今天好难过啊"

    async def _fake_synth(text, params, subdir, emotion=None):
        synth_calls.append({"text": text, "emotion": emotion})
        return "/uploads/tts/x.mp3"

    async def _fake_get_states(cid):
        return {"mood": 30, "anger": 50, "fatigue": 50}  # → sad

    async def _fake_build(*a, **k):
        return [{"role": "user", "content": a[3] if len(a) > 3 else ""}]

    async def _fake_stream(messages, **kw):
        yield "别难过，我陪着你。"

    monkeypatch.setattr("app.voice.audio_gate.should_transcribe", lambda audio_bytes: True)
    monkeypatch.setattr(gateway, "_asr_text", _fake_asr)
    monkeypatch.setattr(gateway, "_refresh_emotional_state", gateway._refresh_emotional_state)
    monkeypatch.setattr("app.services.character_state_service.get_character_states", _fake_get_states)
    monkeypatch.setattr(gateway, "_synthesize_sentence", _fake_synth)
    monkeypatch.setattr("app.voice.voice_mode.build_voice_messages", _fake_build)
    monkeypatch.setattr(gateway, "chat_completion_stream", _fake_stream)

    ws = _FakeWS()
    state = _voice_state()
    asyncio.run(gateway._process_utterance(ws, state, b"\x00audio"))

    # emotional_state 已被写入且透传给 TTS
    assert state["emotional_state"] == "sad"
    assert synth_calls and synth_calls[0]["emotion"] == "sad"
    # 正常回合：llm_sentence 与 tts_audio 均下发
    sent_types = [p["type"] for p in ws.sent]
    assert "llm_sentence" in sent_types
    assert "tts_audio" in sent_types


def test_process_utterance_no_tts_after_interrupt(monkeypatch):
    """打断后：合成完成但不再 send tts_audio（防御）。"""
    synth_calls: list[dict] = []

    async def _fake_asr(audio_bytes):
        return "我今天好难过啊"

    async def _fake_synth(text, params, subdir, emotion=None):
        synth_calls.append({"text": text, "emotion": emotion})
        # 模拟合成期间被新一轮语音打断
        state["interrupt"] = True
        return "/uploads/tts/x.mp3"

    async def _fake_get_states(cid):
        return {"mood": 30, "anger": 50, "fatigue": 50}

    async def _fake_build(*a, **k):
        return [{"role": "user", "content": "x"}]

    async def _fake_stream(messages, **kw):
        yield "别难过，我陪着你。"

    monkeypatch.setattr("app.voice.audio_gate.should_transcribe", lambda audio_bytes: True)
    monkeypatch.setattr(gateway, "_asr_text", _fake_asr)
    monkeypatch.setattr("app.services.character_state_service.get_character_states", _fake_get_states)
    monkeypatch.setattr(gateway, "_synthesize_sentence", _fake_synth)
    monkeypatch.setattr("app.voice.voice_mode.build_voice_messages", _fake_build)
    monkeypatch.setattr(gateway, "chat_completion_stream", _fake_stream)

    ws = _FakeWS()
    state = _voice_state()
    asyncio.run(gateway._process_utterance(ws, state, b"\x00audio"))

    # 合成中途被中断 → tts_audio 不再发送（llm_sentence 已在合成前发送）
    sent_types = [p["type"] for p in ws.sent]
    assert "llm_sentence" in sent_types
    assert "tts_audio" not in sent_types


def test_process_utterance_vad_ignores_silent_frame(monkeypatch):
    """VAD 判定 False（静音/极短）→ 不触发 ASR，回「没听清」并计数。"""
    asr_called = False

    async def _fake_asr(audio_bytes):
        nonlocal asr_called
        asr_called = True
        return "x"

    monkeypatch.setattr("app.voice.audio_gate.should_transcribe", lambda audio_bytes: False)
    monkeypatch.setattr(gateway, "_asr_text", _fake_asr)

    ws = _FakeWS()
    state = _voice_state()
    asyncio.run(gateway._process_utterance(ws, state, b"\x00audio"))

    assert asr_called is False
    assert state["vad_ignored"] == 1
    sent_types = [p["type"] for p in ws.sent]
    assert "asr_final" in sent_types
    assert "error" in sent_types
