# -*- coding: utf-8 -*-
"""配置哨兵 0 号 ＋ 三种语音/生图/识图协议适配（2026-11-18）。

现场：用户部署后「语音/识图/生图/任务」保存全 500，只有服务器级 LLM 能存。
根因：服务器级配置用 ``user_id = 0`` 哨兵，而 ``users`` 表没有 id=0 的行；这几张表带
``FOREIGN KEY(user_id) REFERENCES users(id)``，首次保存走 INSERT 就被外键拦下；
LLM 那张早就有 0 号行，保存走 UPDATE，不校验外键，所以"唯独它能存"。

本文件钉三件事：
1. ``task_llm_configs.user_id`` 不再挂 users 外键（开着 FK 的库上插 0 号必须成功）；
2. 语音的第三种协议（MiMo 式 chat+audio）判定、合成取 base64、探测优先顺序；
3. 生图协议自适应降级、识图真发图、图片 MIME 按文件头判断。
"""
import asyncio
import base64
import json
import types

import pytest
import sqlalchemy as sa

from app.application import system_config_service as system_service
from app.application import tts_service


# ── 1. 哨兵 0 号与外键 ──────────────────────────────────────────────

def test_task_llm_config_user_id_has_no_foreign_key():
    """模型层：user_id 不许挂 users 外键（0 号哨兵对 FK 违约）。"""
    from app.models.agent import TaskLlmConfig

    fk_cols = {c.name for fk in TaskLlmConfig.__table__.foreign_keys for c in fk.columns}
    assert "user_id" not in fk_cols, "task_llm_configs.user_id 又挂上外键了——服务器级保存会 500"


def test_sentinel_zero_insert_succeeds_with_foreign_keys_on():
    """实战复刻：开着 FK 的库上插入 user_id=0 必须成功（原来这里必 IntegrityError）。"""
    from app.models.agent import TaskLlmConfig

    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("PRAGMA foreign_keys=ON"))
        TaskLlmConfig.__table__.create(conn)
        assert conn.execute(sa.text("PRAGMA foreign_keys")).scalar() == 1
        # users 里确实没有 id=0 —— 与现场一致
        assert conn.execute(sa.text("SELECT COUNT(*) FROM users WHERE id=0")).scalar() == 0
        conn.execute(sa.text(
            "INSERT INTO task_llm_configs (user_id, task, enabled) VALUES (0, 'memory', 1)"))
        conn.commit()
        assert conn.execute(sa.text(
            "SELECT COUNT(*) FROM task_llm_configs WHERE user_id=0")).scalar() == 1


def test_migration_declares_task_table_in_no_fk_list():
    """迁移文件必须把 task_llm_configs 写进去外键清单（防止再"漏一项"）。"""
    import importlib.util as _ilu
    import os as _os

    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "alembic", "versions", "c6f8b0d2e4a7_config_sentinel_no_fk.py")
    spec = _ilu.spec_from_file_location("_mig_c6f8", path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert "task_llm_configs" in mod._NO_FK_TABLES
    assert mod.down_revision == "c5e7a9b1d3f6"


# ── 2. 语音：第三种协议（MiMo 式 chat+audio）────────────────────────

@pytest.mark.parametrize("cfg,expected", [
    ({"provider": "mimo"}, True),
    ({"model": "mimo-v2.5-tts"}, True),
    ({"base_url": "https://api.xiaomimimo.com/v1"}, True),
    ({"provider": "dashscope", "model": "qwen-tts"}, False),
    ({"provider": "openai", "model": "tts-1"}, False),
])
def test_is_chat_audio_tts_fingerprints(cfg, expected):
    assert tts_service.is_chat_audio_tts(cfg) is expected


def test_chat_audio_voice_falls_back_to_known_id():
    """传我们自己的音色 key（如 Cherry）必须换成它认的 id，否则服务端报 Unknown voice。"""
    assert tts_service.pick_chat_audio_voice("Cherry", "female") == "冰糖"
    assert tts_service.pick_chat_audio_voice("白桦", "male") == "白桦"
    assert tts_service.pick_chat_audio_voice(None, "male") == "白桦"


def test_chat_audio_synth_writes_audio_from_base64(tmp_path, monkeypatch):
    """合成：文本进 assistant、音色进 audio、从 choices[0].message.audio.data 取 base64 落盘。"""
    raw = b"RIFF" + b"0" * 400
    payload = {"choices": [{"message": {"audio": {"data": base64.b64encode(raw).decode()}}}]}
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    def _urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    monkeypatch.setattr("app.utils.net_guard.ensure_outbound_url", lambda *a, **k: None)

    out = tmp_path / "a.wav"
    got = tts_service._synth_chat_audio_sync(
        "你好", "冰糖",
        {"base_url": "https://api.xiaomimimo.com/v1", "api_key": "sk-x", "model": "mimo-v2.5-tts"},
        tmp_path, "a.wav")
    assert got == str(out)
    assert out.read_bytes() == raw
    assert seen["url"] == "https://api.xiaomimimo.com/v1/chat/completions"
    assert seen["body"]["messages"] == [{"role": "assistant", "content": "你好"}]  # 硬性规则：assistant
    assert seen["body"]["audio"]["voice"] == "冰糖"


def test_speech_probe_prefers_chat_audio_for_mimo(monkeypatch):
    """探测：mimo 的 /audio/speech 会 404（官方如此），第二步 chat+audio 必须接住。"""
    posted = []

    class _Client:
        def __init__(self, *a, **kw):
            self.audio = types.SimpleNamespace(
                speech=types.SimpleNamespace(create=self._boom))

        async def _boom(self, **kw):
            raise RuntimeError("404 Not Found for url /audio/speech")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append((url, json))
            return types.SimpleNamespace(
                status_code=200,
                json=lambda: {"choices": [{"message": {"audio": {"data": "QUJD"}}}]},
                raise_for_status=lambda: None,
            )

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _Client())

    probe, model = asyncio.run(system_service._probe_speech(
        _Client(), "sk-x", "https://api.xiaomimimo.com/v1", "mimo-v2.5-tts", "mimo"))
    assert probe == "chat_audio"
    assert model == "mimo-v2.5-tts"
    assert posted and posted[0][0].endswith("/chat/completions")
    assert posted[0][1]["messages"][0]["role"] == "assistant"


# ── 3. 生图协议自适应 / 识图真发图 / MIME ──────────────────────────

def test_image_provider_falls_back_to_the_other_protocol(monkeypatch):
    """首选协议 404 时，自动换另一种协议——选错 provider 不再等于"用不了"。"""
    from app.application import image_gen_service as igs

    calls = []

    class _P:
        def __init__(self, name, ok):
            self.name, self.ok = name, ok

        async def generate(self, prompt):
            calls.append(self.name)
            if not self.ok:
                e = RuntimeError("404 Not Found")
                e.status_code = 404
                raise e
            return b"PNG"

    monkeypatch.setattr(igs, "OpenAICompatImageProvider",
                        lambda **kw: _P("images", False))
    monkeypatch.setattr(igs, "DashScopeChatImageProvider",
                        lambda **kw: _P("chat", True))
    got = asyncio.run(igs.AutoImageProvider(
        "https://x/v1", "sk", "m", prefer="openai").generate("p"))
    assert got == b"PNG"
    assert calls == ["images", "chat"]  # 先按 provider，失败后降级


def test_image_provider_does_not_retry_on_401(monkeypatch):
    from app.application import image_gen_service as igs

    calls = []

    class _P:
        async def generate(self, prompt):
            calls.append(1)
            e = RuntimeError("401")
            e.status_code = 401
            raise e

    monkeypatch.setattr(igs, "OpenAICompatImageProvider", lambda **kw: _P())
    monkeypatch.setattr(igs, "DashScopeChatImageProvider", lambda **kw: _P())
    with pytest.raises(RuntimeError):
        asyncio.run(igs.AutoImageProvider("https://x/v1", "sk", "m").generate("p"))
    assert len(calls) == 1  # Key 错不浪费第二次请求


def test_vlm_probe_sends_an_actual_image():
    """识图探测必须带 image_url；只发 "hi" 是假通过。"""
    seen = {}

    class _Completions:
        async def create(self, **kw):
            seen.update(kw)
            return types.SimpleNamespace(choices=[])

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Completions()))
    probe, model = asyncio.run(system_service._probe_vlm(client, "qwen-vl-max"))
    assert probe == "vision_chat" and model == "qwen-vl-max"
    content = seen["messages"][0]["content"]
    assert any(p.get("type") == "image_url" for p in content)
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_image_mime_reads_file_header(tmp_path):
    from app.application.image_understanding_service import _image_mime

    png = tmp_path / "a.bin"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    assert _image_mime(str(png)) == "image/png"

    jpg = tmp_path / "b.bin"
    jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 20)
    assert _image_mime(str(jpg)) == "image/jpeg"

    webp = tmp_path / "c.bin"
    webp.write_bytes(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"0" * 8)
    assert _image_mime(str(webp)) == "image/webp"


def test_no_sentinel_config_table_may_carry_a_user_foreign_key():
    """防复发：**哨兵型**配置表（用 user_id=0 表示服务器级）不许挂 users 外键。

    服务器级配置用 user_id=0 哨兵，而 users 表没有 id=0 的行；一旦挂 FK，首次保存必然
    FOREIGN KEY constraint failed（2026-11-18 现场 500 就是这个）。

    这里用「显式归类」而不是按表名自动扫：`user_llm_configs` 是**用户级**表（user_id 永远 >0），
    挂 FK 是对的；哨兵表与用户级表必须各自登记，新增配置表不登记就直接红——
    逼作者当场决定它属于哪一类，而不是等上线后炸。
    """
    from app.models.base import Base

    SENTINEL_TABLES = {
        "api_configs", "vlm_configs", "speech_configs",
        "multimodal_configs", "image_gen_configs", "task_llm_configs",
    }
    USER_LEVEL_TABLES = {"user_llm_configs"}   # user_id 永远 >0，允许挂 FK

    problems = []
    for name, table in Base.metadata.tables.items():
        if not name.endswith("_configs"):
            continue
        cols = {c.name for c in table.columns}
        if "user_id" not in cols:
            continue
        if name not in SENTINEL_TABLES and name not in USER_LEVEL_TABLES:
            problems.append(f"{name}（未归类：请登记到 SENTINEL_TABLES 或 USER_LEVEL_TABLES）")
            continue
        if name in SENTINEL_TABLES:
            fk_cols = {fk.parent.name for fk in table.foreign_keys}
            if "user_id" in fk_cols:
                problems.append(f"{name}（哨兵表却挂了 users 外键，服务器级保存会 500）")
    assert not problems, "；".join(problems)

