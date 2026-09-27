# -*- coding: utf-8 -*-
"""S3 音色（形态 A：云端音色可选）——清单 / 解析 / 透传 / 默认零行为。

覆盖：
- 云端音色清单：默认集＝Ethan/Cherry，服务端用 data/tts_voices.json 扩充，坏 JSON/缺文件退化，
  按 id 去重且不硬编码供应商全量表；
- resolve_cloud_voice：命中清单返回 id、未命中/空返回 None；清单 id 与既有预设 key 两值域不相交；
- **默认零行为**：voice 为空或命中既有预设时，百炼 payload 与 edge-tts 参数逐字节等于既有口径
  （两条链共用 synthesize，参数级钉住 + 四个调用点各自钉住）；
- 命中清单：云端链路改用该音色；edge-tts 兜底不吞云端 id，仍按性别取默认；
- 透传：voice_mode 装载函数透出 tts_voice，nodes / streaming / gateway / chat_service 各 1 行；
- 值域校验与只读清单接口。

全程不连生产库（清单文件指向 tmp_path，装载函数用假 session）。
"""
import asyncio
import inspect
import json
from pathlib import Path

import pytest

from app.application import tts_service

CATALOG_NAME = "tts_voices.json"


@pytest.fixture(autouse=True)
def _isolated_catalog(tmp_path, monkeypatch):
    """清单来源与缓存按用例隔离：不读仓库 data/、不带上一用例的缓存"""
    monkeypatch.setattr(tts_service, "_CLOUD_VOICE_FILE", tmp_path / CATALOG_NAME)
    monkeypatch.setattr(tts_service, "_cloud_voice_cache", (0.0, []))
    yield


def _write_catalog(tmp_path, entries):
    (tmp_path / CATALOG_NAME).write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )


# ── 既有口径参照物（改动前的音色选择/参数算式，逐字节对照用）────────────
_LEGACY_DASH = {"male": "Ethan", "female": "Cherry", None: "Cherry"}
_LEGACY_EDGE = {
    "male": "zh-CN-YunxiNeural",
    "female": "zh-CN-XiaoxiaoNeural",
    None: "zh-CN-XiaoxiaoNeural",
}


def _legacy_gender_key(gender):
    g = (gender or "").strip().lower()
    if g in ("male", "男", "m"):
        return "male"
    if g in ("female", "女", "f"):
        return "female"
    return None


def _legacy_dash_voice(gender, voice):
    preset = tts_service.VOICE_PRESETS.get(voice or "") if voice else None
    return preset["dashscope"] if preset else _LEGACY_DASH[_legacy_gender_key(gender)]


def _legacy_edge_call(gender, voice, voice_rate, voice_pitch, emotion):
    preset = tts_service.VOICE_PRESETS.get(voice or "") if voice else None
    edge_voice = preset["edge"] if preset else _LEGACY_EDGE[_legacy_gender_key(gender)]
    rate_delta, pitch_delta = tts_service.emotion_edge_adjust(emotion)
    rate = f"{max(0.5, min(2.0, (voice_rate or 1.0) + rate_delta)) - 1.0:+.0%}"
    pitch = f"{max(-50.0, min(50.0, (voice_pitch or 0.0) + pitch_delta)):+.0f}Hz"
    return edge_voice, rate, pitch


def _run_edge(monkeypatch, tmp_path, **kw):
    """关百炼走 edge-tts 兜底，返回 (结果 URL, Communicate 实收 (text, voice, rate, pitch))"""
    calls = []

    class _FakeCommunicate:
        def __init__(self, text, voice, rate, pitch):
            calls.append((text, voice, rate, pitch))

        async def save(self, path):
            Path(path).write_bytes(b"x" * 300)

    async def _no_cfg():
        return {}

    monkeypatch.setattr("edge_tts.Communicate", _FakeCommunicate)
    monkeypatch.setattr(tts_service, "_server_speech_config", _no_cfg)
    monkeypatch.setattr(tts_service, "TTS_DIR", Path(tmp_path) / "tts")
    res = asyncio.run(tts_service.synthesize(text="你好。", subdir="s", **kw))
    return res, (calls[-1] if calls else None)


def _run_dashscope(monkeypatch, tmp_path, **kw):
    """开百炼云端链路，返回 (结果 URL, 合成实收 {"text","voice"})"""
    calls = []

    def _fake_synth(text, voice, cfg, target_dir, fname):
        calls.append({"text": text, "voice": voice})
        path = Path(target_dir) / fname
        path.write_bytes(b"x" * 300)
        return str(path)

    async def _enabled_cfg():
        return {"enabled": True, "base_url": "", "api_key": "k", "model": "qwen-tts", "provider": ""}

    monkeypatch.setattr(tts_service, "_synth_dashscope_sync", _fake_synth)
    monkeypatch.setattr(tts_service, "_tts_runner_via_registry", lambda cfg: None)
    monkeypatch.setattr(tts_service, "_server_speech_config", _enabled_cfg)
    monkeypatch.setattr(tts_service, "TTS_DIR", Path(tmp_path) / "tts")
    res = asyncio.run(tts_service.synthesize(text="你好。", subdir="s", **kw))
    return res, (calls[-1] if calls else None)


# 覆盖：空 / NULL / 每个预设 key / 未知值 × 男 / 女 / 未设 / 中文性别
_LEGACY_ROWS = [
    (None, None, 1.0, 0.0, None),
    ("male", None, 1.0, 0.0, None),
    ("female", "", 1.0, 0.0, "sad"),
    (None, "not-a-preset", 1.2, 5.0, None),
    ("男", "xiaoxiao", 1.0, 0.0, None),
    ("女", "xiaoyi", 0.9, -8.0, "excited"),
    ("male", "yunxi", 1.5, 30.0, None),
    ("male", "wanlung", 1.0, 0.0, "平静"),
    ("female", "hiumaan", 2.5, -90.0, None),
    (None, "hsiaochen", 1.0, 0.0, "whatever"),
]


@pytest.mark.parametrize("gender,voice,rate,pitch,emotion", _LEGACY_ROWS)
def test_default_zero_behavior_edge_params_unchanged(
    monkeypatch, tmp_path, gender, voice, rate, pitch, emotion
):
    """零行为（edge-tts 兜底链）：voice 为空/命中既有预设时，音色与 rate/pitch 逐字节不变"""
    res, call = _run_edge(
        monkeypatch, tmp_path,
        gender=gender, voice=voice, voice_rate=rate, voice_pitch=pitch, emotion=emotion,
    )
    assert res is not None and res.startswith("/uploads/tts/s/")
    expected = _legacy_edge_call(gender, voice, rate, pitch, emotion)
    assert (call[1], call[2], call[3]) == expected


@pytest.mark.parametrize("gender,voice,rate,pitch,emotion", _LEGACY_ROWS)
def test_default_zero_behavior_cloud_params_unchanged(
    monkeypatch, tmp_path, gender, voice, rate, pitch, emotion
):
    """零行为（百炼云端链）：未命中清单时 input.voice 与降级/性别默认口径逐字节不变"""
    res, call = _run_dashscope(
        monkeypatch, tmp_path,
        gender=gender, voice=voice, voice_rate=rate, voice_pitch=pitch, emotion=emotion,
    )
    assert res is not None and res.endswith(".wav")
    assert call["voice"] == _legacy_dash_voice(gender, voice)


def test_new_kwarg_default_equals_explicit_none(monkeypatch, tmp_path):
    """新增形参 tts_voice 的默认值不改变任何参数（省略 vs 显式 None 逐字节一致）"""
    _, omitted = _run_dashscope(monkeypatch, tmp_path, gender="female", voice="xiaoxiao")
    _, explicit = _run_dashscope(
        monkeypatch, tmp_path, gender="female", voice="xiaoxiao", tts_voice=None
    )
    assert omitted == explicit


def test_presets_and_cloud_catalog_are_disjoint(tmp_path):
    """两值域不相交：预设 key 不得出现在云端清单里（否则 ① 会盖掉 ②，优先级不再是空话）"""
    _write_catalog(tmp_path, [{"id": k} for k in tts_service.VOICE_PRESETS])
    ids = {e["id"] for e in tts_service.list_cloud_voices(refresh=True)}
    assert ids == {"Ethan", "Cherry"}  # 与预设同名的扩充项被拒（值域以预设为准）


# ── 清单本身 ──────────────────────────────────────────────────────

def test_cloud_catalog_defaults():
    """默认集＝本部署实测可用的 Ethan / Cherry（不硬编码供应商全量音色表）"""
    entries = tts_service.list_cloud_voices()
    assert [e["id"] for e in entries] == ["Ethan", "Cherry"]
    assert all(e["label"] and e["gender"] for e in entries)


def test_cloud_catalog_server_extensible_and_dedup(tmp_path):
    """服务端扩充：追加 Serena/Chelsie 生效；重复 id 与缺 id 项不影响既有顺序"""
    _write_catalog(tmp_path, [
        {"id": "Serena", "label": "Serena"},
        {"id": "Ethan"},
        {"label": "无 id 项"},
        {"id": "Chelsie", "label": "Chelsie", "gender": "female"},
    ])
    entries = tts_service.list_cloud_voices(refresh=True)
    assert [e["id"] for e in entries] == ["Ethan", "Cherry", "Serena", "Chelsie"]
    assert entries[2]["label"] == "Serena"


def test_cloud_catalog_broken_file_falls_back(tmp_path):
    """清单文件坏 JSON / 非数组：退化成默认集，不抛异常（合成链路绝不受影响）"""
    (tmp_path / CATALOG_NAME).write_text("{not json", encoding="utf-8")
    assert [e["id"] for e in tts_service.list_cloud_voices(refresh=True)] == ["Ethan", "Cherry"]
    (tmp_path / CATALOG_NAME).write_text('{"voices": []}', encoding="utf-8")
    assert [e["id"] for e in tts_service.list_cloud_voices(refresh=True)] == ["Ethan", "Cherry"]


def test_resolve_cloud_voice_hit_miss_and_empty(tmp_path):
    _write_catalog(tmp_path, [{"id": "Serena"}])
    tts_service.list_cloud_voices(refresh=True)
    assert tts_service.resolve_cloud_voice("Cherry") == "Cherry"
    assert tts_service.resolve_cloud_voice("Serena") == "Serena"
    assert tts_service.resolve_cloud_voice("  Ethan ") == "Ethan"  # 两侧空白不影响命中
    assert tts_service.resolve_cloud_voice("xiaoxiao") is None  # 预设 key 不当云端音色
    assert tts_service.resolve_cloud_voice("nobody") is None
    assert tts_service.resolve_cloud_voice("") is None
    assert tts_service.resolve_cloud_voice(None) is None


@pytest.mark.parametrize("gender,voice,rate,pitch,emotion", _LEGACY_ROWS)
def test_default_zero_behavior_survives_catalog_growth(
    monkeypatch, tmp_path, gender, voice, rate, pitch, emotion
):
    """服务端扩了清单也不冲既有行为：同一批「空/预设」参数的 edge 结果保持逐字节一致"""
    _write_catalog(tmp_path, [{"id": "Serena"}, {"id": "Ryan"}])
    _, call = _run_edge(
        monkeypatch, tmp_path,
        gender=gender, voice=voice, voice_rate=rate, voice_pitch=pitch, emotion=emotion,
    )
    assert (call[1], call[2], call[3]) == _legacy_edge_call(gender, voice, rate, pitch, emotion)


# ── 命中清单 ──────────────────────────────────────────────────────

def test_cloud_voice_used_on_dashscope(monkeypatch, tmp_path):
    """voice 命中清单 ⇒ 云端链路直接用它合成（不再降级成性别默认音色）"""
    _, call = _run_dashscope(monkeypatch, tmp_path, gender="male", voice="Cherry")
    assert call["voice"] == "Cherry"


def test_resolved_tts_voice_wins_over_preset(monkeypatch, tmp_path):
    """显式传已解析音色 ⇒ 当云端音色用（优先级 ① 高于预设 ②）"""
    _, call = _run_dashscope(
        monkeypatch, tmp_path, gender="female", voice="xiaoxiao", tts_voice="Ethan"
    )
    assert call["voice"] == "Ethan"


def test_cloud_voice_edge_fallback_stays_gender_based(monkeypatch, tmp_path):
    """edge-tts 不认云端音色 id：兜底链仍按性别取默认（不把 "Cherry" 塞给 edge）"""
    _, call = _run_edge(monkeypatch, tmp_path, gender="male", voice="Cherry")
    assert call[1] == _LEGACY_EDGE["male"]


# ── 装载函数与四处透传 ─────────────────────────────────────────────

class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return _FakeResult(self._row)


def _fake_row(**kw):
    row = type("Row", (), {})()
    row.gender = kw.get("gender")
    row.voice = kw.get("voice")
    row.voice_rate = kw.get("voice_rate")
    row.voice_pitch = kw.get("voice_pitch")
    row.name = kw.get("name", "小伴")
    return row


def test_voice_mode_loader_surfaces_resolved_voice(monkeypatch, tmp_path):
    from app.voice import voice_mode

    _write_catalog(tmp_path, [{"id": "Serena"}])
    monkeypatch.setattr(
        voice_mode, "async_session_factory",
        lambda: _FakeSession(_fake_row(gender="female", voice="Serena", voice_rate=1.0, voice_pitch=0.0)),
    )
    params = asyncio.run(voice_mode.load_character_voice_params(7))
    assert params["voice"] == "Serena"
    assert params["tts_voice"] == "Serena"


def test_voice_mode_loader_tts_voice_none_for_legacy(monkeypatch):
    """装载函数对既有值（预设 key / 空）透出 None ⇒ 透传后各链与今天完全一致"""
    from app.voice import voice_mode

    for value in ("xiaoxiao", None, ""):
        monkeypatch.setattr(
            voice_mode, "async_session_factory",
            lambda: _FakeSession(_fake_row(gender="female", voice=value)),
        )
        params = asyncio.run(voice_mode.load_character_voice_params(7))
        assert params["tts_voice"] is None


def _capture_synthesize(monkeypatch):
    calls = []

    async def _fake(*a, **k):
        calls.append((a, k))
        return "/uploads/tts/x.mp3"

    monkeypatch.setattr("app.application.tts_service.synthesize", _fake)
    return calls


def test_nodes_stream_block_forwards_tts_voice(monkeypatch):
    """流式链（逐句实时合成）：nodes._synth_stream_block 透传已解析音色"""
    from app.agent import nodes

    calls = _capture_synthesize(monkeypatch)
    state = {
        "voice_params": {"gender": "male", "voice": "Cherry", "tts_voice": "Cherry",
                         "voice_rate": 1.0, "voice_pitch": 0.0},
        "tts_subdir": "7", "user_id": 1, "emotional_state": "sad",
    }
    assert asyncio.run(nodes._synth_stream_block("你好。", state)) == "/uploads/tts/x.mp3"
    assert calls[-1][1]["tts_voice"] == "Cherry"


def test_nodes_stream_block_legacy_params_unchanged(monkeypatch):
    """流式链零行为：既有 voice_params（无 tts_voice）时透传值为 None，其余参数不动"""
    from app.agent import nodes

    calls = _capture_synthesize(monkeypatch)
    state = {
        "voice_params": {"gender": "female", "voice": "xiaoyi", "voice_rate": 1.1, "voice_pitch": 2.0},
        "tts_subdir": "7", "user_id": 1, "emotional_state": "",
    }
    asyncio.run(nodes._synth_stream_block("你好。", state))
    kw = calls[-1][1]
    assert kw["tts_voice"] is None
    assert (kw["gender"], kw["voice"], kw["voice_rate"], kw["voice_pitch"], kw["emotion"]) == (
        "female", "xiaoyi", 1.1, 2.0, None,
    )


def test_streaming_chunks_forwards_tts_voice(monkeypatch):
    """流式回退链（整段逐句补合成）：streaming._synthesize_chunks_tts 透传已解析音色"""
    from app.application.chat import streaming

    calls = _capture_synthesize(monkeypatch)

    async def _fake_load(_cid):
        return {"gender": "male", "voice": "Ethan", "tts_voice": "Ethan",
                "voice_rate": 1.0, "voice_pitch": 0.0}

    monkeypatch.setattr("app.voice.voice_mode.load_character_voice_params", _fake_load)
    urls = asyncio.run(streaming._synthesize_chunks_tts(["你好。"], 5, 9, 1, emotion=None))
    assert urls == ["/uploads/tts/x.mp3"]
    assert calls[-1][1]["tts_voice"] == "Ethan"


def test_gateway_sentence_forwards_tts_voice(monkeypatch):
    """语音通话链（逐句下发音频）：gateway._synthesize_sentence 透传已解析音色"""
    from app.voice import gateway

    calls = _capture_synthesize(monkeypatch)
    params = {"gender": "female", "voice": "Cherry", "tts_voice": "Cherry",
              "voice_rate": 1.0, "voice_pitch": 0.0, "name": "小伴"}
    url = asyncio.run(gateway._synthesize_sentence("你好。", params, "7", emotion=None))
    assert url == "/uploads/tts/x.mp3"
    assert calls[-1][1]["tts_voice"] == "Cherry"

    legacy = {"gender": "female", "voice": "xiaoxiao", "voice_rate": 1.0, "voice_pitch": 0.0}
    asyncio.run(gateway._synthesize_sentence("你好。", legacy, "7", emotion=None))
    assert calls[-1][1]["tts_voice"] is None


def test_chat_service_non_stream_chain_forwards_tts_voice():
    """非流式链（文本对话出语音）：chat_service 直接读列，透传前就地解析"""
    from app.application import chat_service

    src = inspect.getsource(chat_service.send_and_receive_chunked)
    assert "tts_voice=resolve_cloud_voice(voice)" in src


# ── 值域校验与清单接口 ─────────────────────────────────────────────

def test_character_voice_write_validation(tmp_path):
    from fastapi import HTTPException

    from app.api import characters as characters_api

    _write_catalog(tmp_path, [{"id": "Serena"}])
    check = characters_api._check_voice_or_400
    for ok in (None, "", "   ", "xiaoxiao", "wanlung", "Cherry", "Serena"):
        check(ok)  # 空 / 既有预设 key / 清单内 id ⇒ 放行
    with pytest.raises(HTTPException) as bad:
        check("not-a-voice")
    assert bad.value.status_code == 400


def test_tts_voices_endpoint_is_read_only_catalog(tmp_path):
    from app.api import system as system_api

    _write_catalog(tmp_path, [{"id": "Serena", "label": "Serena"}])
    body = asyncio.run(system_api.get_tts_voices(user_id=1))
    assert [v["id"] for v in body["voices"]] == ["Ethan", "Cherry", "Serena"]
    src = inspect.getsource(system_api)
    assert '@router.get("/tts-voices")' in src and '@router.post("/tts-voices")' not in src
