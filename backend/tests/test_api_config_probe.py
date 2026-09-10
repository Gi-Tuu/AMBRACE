# -*- coding: utf-8 -*-
"""API 配置「检测连接」按模态探测（生图模型被 chat 误判 503）测试：
- image 走 /v1/models 零成本探测，**绝不**打 chat.completions
- /models 404 回落 images.generate；/models 401 直接判 Key 失败（不兜底生图）
- 503「only supported on …」/ 401 / 404 错误分类文案
- speech：OpenAI audio.speech 不通 → 回落百炼私有端点；都不通给友好提示
- modality 缺省/非法 → llm（老 App 不带该字段时零变化）+ 多 Key 逐个尝试旧语义保留
"""
import asyncio

import httpx
import pytest

from app.agent import llm_client
from app.application import system as system_service
from app.application import tts_service

PRIVATE_TTS_PATH = "/api/v1/services/aigc/multimodal-generation/generation"


# ── 假异常（openai SDK 挂 status_code，httpx 挂 response.status_code）──

class _FakeStatusError(Exception):
    def __init__(self, status_code, msg=""):
        super().__init__(msg)
        self.status_code = status_code


class _FakeHttpStatusError(Exception):
    def __init__(self, status_code, msg=""):
        super().__init__(msg)
        self.response = _FakeHttpResponse(status_code, {})


class _FakeHttpResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.text = ""
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _FakeHttpStatusError(self.status_code, f"HTTP {self.status_code}")


# ── 假 AsyncOpenAI（chat / models / images / audio）──

class _NeverHere(Exception):
    """探测走错资源时抛出（如 image 探测误调 chat）。"""


class _FakeCompletions:
    def __init__(self, fail=None):
        self._fail = fail
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        if self._fail:
            raise self._fail
        return object()


class _FakeChat:
    def __init__(self, fail=None):
        self.completions = _FakeCompletions(fail)


class _FakeImages:
    def __init__(self, fail=None):
        self._fail = fail
        self.calls = []

    async def generate(self, **kw):
        self.calls.append(kw)
        if self._fail:
            raise self._fail
        return object()


class _FakeModel:
    def __init__(self, mid):
        self.id = mid


class _FakeModelsResp:
    def __init__(self, ids):
        self.data = [_FakeModel(i) for i in ids]


class _FakeModels:
    def __init__(self, ids=None, fail=None):
        self._ids = ids or []
        self._fail = fail

    async def list(self):
        if self._fail:
            raise self._fail
        return _FakeModelsResp(self._ids)


class _FakeSpeech:
    def __init__(self, fail=None):
        self._fail = fail
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        if self._fail:
            raise self._fail
        return object()


class _FakeAudio:
    def __init__(self, fail=None):
        self.speech = _FakeSpeech(fail)


class _FakeClient:
    def __init__(self, models=None, images=None, chat=None, audio=None):
        self.models = models or _FakeModels()
        self.images = images or _FakeImages()
        self.chat = chat or _FakeChat()
        self.audio = audio or _FakeAudio()


def _image_client(models, images=None) -> _FakeClient:
    """生图探测专用 client：chat 被调用即视为回归。"""
    return _FakeClient(
        models=models,
        images=images or _FakeImages(),
        chat=_FakeChat(fail=_NeverHere("image 探测绝不能打 chat.completions")),
    )


# ── 1. 生图：优先 /v1/models，绝不打 chat ──

def test_image_probe_uses_models_list_not_chat():
    client = _image_client(_FakeModels(ids=["gpt-image-2", "dall-e-3"]))
    probe, model = asyncio.run(
        system_service._probe_image(client, "sk-x", "https://gw.example.com/v1", "gpt-image-2", "openai")
    )
    assert probe == "models_list"
    assert model == "gpt-image-2"
    assert client.images.calls == []  # 零成本：未真实生图


def test_image_probe_models_list_without_model_only_notes():
    client = _image_client(_FakeModels(ids=["other-model"]))
    probe, model = asyncio.run(
        system_service._probe_image(client, "sk-x", "https://gw.example.com/v1", "gpt-image-2", "openai")
    )
    assert probe.startswith("models_list")
    assert "未包含该模型" in probe  # 不冤判，仅备注
    assert model == "gpt-image-2"
    assert client.images.calls == []


def test_image_probe_falls_back_to_images_generate_on_404():
    client = _image_client(_FakeModels(fail=_FakeStatusError(404, "Not Found")))
    probe, model = asyncio.run(
        system_service._probe_image(client, "sk-x", "https://gw.example.com/v1", "gpt-image-2", "openai")
    )
    assert probe.startswith("images_generates")
    assert model == "gpt-image-2"
    assert client.images.calls[0]["model"] == "gpt-image-2"
    assert client.images.calls[0]["size"] == "1024x1024"


def test_image_probe_401_does_not_fall_back():
    client = _image_client(_FakeModels(fail=_FakeStatusError(401, "invalid api key")))
    with pytest.raises(_FakeStatusError):
        asyncio.run(
            system_service._probe_image(client, "sk-x", "https://gw.example.com/v1", "gpt-image-2", "openai")
        )
    assert client.images.calls == []  # Key 错直接失败，不消耗生图额度


def test_image_probe_dashscope_uses_chat_content_list(monkeypatch):
    posted = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append((url, json))
            return _FakeHttpResponse(200, {})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _Client())
    client = _image_client(_FakeModels(fail=_FakeStatusError(404, "Not Found")))
    probe, model = asyncio.run(
        system_service._probe_image(
            client, "sk-x", "https://dashscope.example.com/compatible-mode/v1", "qwen-image", "dashscope"
        )
    )
    assert probe.startswith("dashscope_chat_image")
    assert posted[0][0] == "https://dashscope.example.com/compatible-mode/v1/chat/completions"
    assert posted[0][1]["messages"][0]["content"][0]["type"] == "text"  # content 列表格式
    assert client.images.calls == []


# ── 2. 错误分类文案 ──

def test_classify_503_only_supported_on():
    e = _FakeStatusError(503, "model gpt-image-2 is only supported on /v1/images/generations and /v1/images/edits")
    txt = system_service._classify_probe_error(e, "image")
    assert "模型与模态不匹配" in txt
    assert "生图" in txt


def test_classify_401():
    txt = system_service._classify_probe_error(_FakeStatusError(401, "bad key"), "image")
    assert "API Key 无效" in txt
    assert "生图" in txt


def test_classify_404_path_vs_model():
    txt = system_service._classify_probe_error(_FakeStatusError(404, "Not Found"), "speech")
    assert "接口路径不存在" in txt
    txt2 = system_service._classify_probe_error(_FakeStatusError(404, "The model does not exist"), "image")
    assert "模型不存在" in txt2


def test_classify_httpx_status_error():
    txt = system_service._classify_probe_error(_FakeHttpStatusError(429, "rate limited"), "llm")
    assert "限流" in txt


# ── 3. 语音：OpenAI audio.speech 不通 → 回落百炼私有端点 ──

def test_speech_falls_back_to_private_tts_endpoint(monkeypatch):
    posted = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append((url, json))
            return _FakeHttpResponse(200, {"output": {"audio": {"url": "https://cdn.example.com/a.mp3"}}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _Client())
    monkeypatch.setattr(
        tts_service, "_tts_endpoints",
        lambda cfg: [(f"https://gw.example.com{PRIVATE_TTS_PATH}", ["qwen-tts"])],
    )
    client = _FakeClient(audio=_FakeAudio(fail=_FakeStatusError(404, "Not Found")))
    probe, model = asyncio.run(
        system_service._probe_speech(client, "sk-x", "https://gw.example.com/v1", "qwen-tts", "")
    )
    assert probe == "dashscope_tts"
    assert model == "qwen-tts"
    assert posted[0][0].endswith(PRIVATE_TTS_PATH)
    assert posted[0][1]["model"] == "qwen-tts"
    assert posted[0][1]["parameters"]["format"] == "mp3"


def test_speech_skips_endpoints_on_other_hosts(monkeypatch):
    """_tts_endpoints 另附的公开兜底主机不在检测范围：不把用户 Key 发到无关主机。"""
    posted = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append(url)
            return _FakeHttpResponse(200, {"output": {"audio": {"url": "https://cdn.example.com/a.mp3"}}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _Client())
    monkeypatch.setattr(
        tts_service, "_tts_endpoints",
        lambda cfg: [("https://gw.example.com" + PRIVATE_TTS_PATH, ["qwen-tts"]),
                     ("https://dashscope.aliyuncs.com" + PRIVATE_TTS_PATH, ["qwen-tts"])],
    )
    client = _FakeClient(audio=_FakeAudio(fail=_FakeStatusError(404, "Not Found")))
    probe, _ = asyncio.run(
        system_service._probe_speech(client, "sk-x", "https://gw.example.com/v1", "qwen-tts", "")
    )
    assert probe == "dashscope_tts"
    assert all("gw.example.com" in u for u in posted)
    assert not any("dashscope.aliyuncs.com" in u for u in posted)


def test_speech_both_fail_gives_friendly_hint(monkeypatch):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            return _FakeHttpResponse(400, {"message": "model not supported"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _Client())
    monkeypatch.setattr(
        tts_service, "_tts_endpoints",
        lambda cfg: [(f"https://gw.example.com{PRIVATE_TTS_PATH}", ["qwen-tts"])],
    )
    client = _FakeClient(audio=_FakeAudio(fail=_FakeStatusError(404, "Not Found")))
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(system_service._probe_speech(client, "sk-x", "https://gw.example.com/v1", "qwen-tts", ""))
    msg = str(ei.value)
    assert "试听" in msg and "语音(TTS)检测未通过" in msg
    # 友好提示要能穿过分类层，不能被翻译成生硬的 503/超时
    assert system_service._classify_probe_error(ei.value, "speech") == msg


def test_speech_401_raises_original(monkeypatch):
    monkeypatch.setattr(tts_service, "_tts_endpoints", lambda cfg: [])
    client = _FakeClient(audio=_FakeAudio(fail=_FakeStatusError(401, "invalid api key")))
    with pytest.raises(_FakeStatusError):
        asyncio.run(system_service._probe_speech(client, "sk-x", "https://gw.example.com/v1", "qwen-tts", ""))


# ── 4. modality 缺省/非法 → llm（老 App 零变化）+ 多 Key 旧语义 ──

def test_modality_default_is_llm(monkeypatch):
    client = _FakeClient(
        chat=_FakeChat(),
        models=_FakeModels(fail=_NeverHere("llm 不应走 /models")),
        images=_FakeImages(fail=_NeverHere("llm 不应走生图")),
    )
    monkeypatch.setattr(llm_client, "get_llm_client", lambda api_key=None, base_url=None: client)
    res = asyncio.run(system_service.test_api_connection(
        {"base_url": "https://gw.example.com/v1", "api_key": "sk-1234567890", "model": "gpt-4o-mini"}, 1
    ))
    assert res["ok"] is True
    assert res["modality"] == "llm"
    assert res["probe"] == "chat_completions"
    assert client.chat.completions.calls[0]["model"] == "gpt-4o-mini"


def test_modality_invalid_falls_back_to_llm(monkeypatch):
    client = _FakeClient(chat=_FakeChat())
    monkeypatch.setattr(llm_client, "get_llm_client", lambda api_key=None, base_url=None: client)
    res = asyncio.run(system_service.test_api_connection(
        {"base_url": "https://gw.example.com/v1", "api_key": "sk-1", "model": "m", "modality": "banana"}, 1
    ))
    assert res["ok"] is True and res["modality"] == "llm"


def test_image_modality_end_to_end_avoids_chat(monkeypatch):
    client = _image_client(_FakeModels(ids=["gpt-image-2"]))
    monkeypatch.setattr(llm_client, "get_llm_client", lambda api_key=None, base_url=None: client)
    res = asyncio.run(system_service.test_api_connection(
        {"base_url": "https://gw.example.com/v1", "api_key": "sk-1234567890",
         "model": "gpt-image-2", "modality": "image"}, 1
    ))
    assert res["ok"] is True, res
    assert res["modality"] == "image"
    assert res["probe"] == "models_list"


def test_image_modality_401_returns_classified_error(monkeypatch):
    client = _image_client(_FakeModels(fail=_FakeStatusError(401, "invalid api key")))
    monkeypatch.setattr(llm_client, "get_llm_client", lambda api_key=None, base_url=None: client)
    res = asyncio.run(system_service.test_api_connection(
        {"base_url": "https://gw.example.com/v1", "api_key": "sk-x",
         "model": "gpt-image-2", "modality": "image"}, 1
    ))
    assert res["ok"] is False
    assert "API Key 无效" in res["error"]
    assert res["modality"] == "image"


def test_multi_key_pool_tries_each_key(monkeypatch):
    made = {}

    def _factory(api_key=None, base_url=None):
        fail = _FakeStatusError(401, "bad key") if api_key == "sk-bad-000001" else None
        made[api_key] = _FakeClient(chat=_FakeChat(fail=fail))
        return made[api_key]

    monkeypatch.setattr(llm_client, "get_llm_client", _factory)
    res = asyncio.run(system_service.test_api_connection(
        {"base_url": "https://gw.example.com/v1", "api_key": "sk-bad-000001,sk-good-2", "model": "m"}, 1
    ))
    assert res["ok"] is True
    assert res["api_key_tail"] == "good-2"
    assert set(made) == {"sk-bad-000001", "sk-good-2"}
