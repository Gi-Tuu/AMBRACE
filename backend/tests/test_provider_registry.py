# -*- coding: utf-8 -*-
"""X3 Provider 端口测试：register_provider 校验/来源过滤/解析规则 + 内置 LLM/TTS 样板经注册口可用"""
import asyncio

import pytest

from app.providers import registry as pr


# ── 内置样板注册 ────────────────────────────────────────────────────


def test_builtin_llm_tts_registered():
    assert ("llm", "openai_compatible") in pr._ENTRIES
    assert ("tts", "dashscope") in pr._ENTRIES
    assert pr._ENTRIES[("llm", "openai_compatible")]["source"] == "builtin"
    names = {(p["kind"], p["name"]) for p in pr.list_providers()}
    assert ("llm", "openai_compatible") in names
    assert ("tts", "dashscope") in names


def test_llm_factory_returns_openai_client():
    """样板 LLM：resolve→工厂→OpenAI 兼容客户端（离线构造，不发包）。"""
    from openai import AsyncOpenAI

    hit = pr.resolve_provider("llm", {})
    assert hit is not None and hit[0] == "openai_compatible"
    client = hit[1]({"api_key": "k-test", "base_url": "http://127.0.0.1:9/v1"})
    assert isinstance(client, AsyncOpenAI)


def test_tts_factory_runner_calls_builtin(monkeypatch, tmp_path):
    """样板 TTS：resolve→工厂→async runner；runner 晚绑定 _synth_dashscope_sync（patch 生效）。"""
    from app.application import tts_service

    def _fake_sync(text, voice, cfg, target_dir, fname):
        p = target_dir / fname
        p.write_bytes(b"x" * 300)
        return str(p)

    monkeypatch.setattr(tts_service, "_synth_dashscope_sync", _fake_sync)
    hit = pr.resolve_provider("tts", {})
    assert hit is not None and hit[0] == "dashscope"
    runner = hit[1]({"api_key": "k", "base_url": "", "model": "qwen-tts"})
    out = asyncio.run(runner("你好", "Cherry", tmp_path, "f.wav"))
    assert out == str(tmp_path / "f.wav")


# ── 注册口校验 ──────────────────────────────────────────────────────


def test_register_validation_and_duplicate():
    with pytest.raises(ValueError, match="kind"):
        pr.register_provider("nope", "valid_name", lambda c: None)
    with pytest.raises(ValueError, match="name"):
        pr.register_provider("llm", "Bad-Name!", lambda c: None)
    with pytest.raises(ValueError, match="factory"):
        pr.register_provider("llm", "valid_name2", "not-callable")
    with pytest.raises(ValueError, match="already registered"):
        pr.register_provider("llm", "openai_compatible", lambda c: None)


def test_plugin_source_filter_and_unregister(monkeypatch):
    """插件来源 provider：停用→不可选且 list 隐藏；启用+config tag 精确匹配→选中；builtin 兜底不受影响。"""
    import app.plugins.registry as plugin_registry

    pr.register_provider("tts", "dummy_tts", lambda c: None, {"label": "假TTS"}, source="some_plugin")
    try:
        monkeypatch.setattr(plugin_registry, "_enabled", {"some_plugin": False})
        assert pr.provider_factory("tts", "dummy_tts") is None
        assert all(p["name"] != "dummy_tts" for p in pr.list_providers("tts"))
        # 未指定 tag → 内置兜底，不受停用插件影响
        assert pr.resolve_provider("tts", {})[0] == "dashscope"

        monkeypatch.setattr(plugin_registry, "_enabled", {"some_plugin": True})
        assert pr.provider_factory("tts", "dummy_tts") is not None
        assert pr.resolve_provider("tts", {"provider": "dummy_tts"})[0] == "dummy_tts"
    finally:
        pr.unregister_providers_for_source("some_plugin")
    assert ("tts", "dummy_tts") not in pr._ENTRIES


def test_unregister_providers_not_in():
    pr.unregister_providers_not_in(set())
    pr.register_provider("llm", "stale_llm", lambda c: None, source="gone_plugin")
    removed = pr.unregister_providers_not_in({"other_plugin"})
    assert ("llm", "stale_llm") in removed
    assert ("llm", "stale_llm") not in pr._ENTRIES
    # builtin 不受影响
    assert ("llm", "openai_compatible") in pr._ENTRIES


def test_resolve_unknown_kind_returns_none():
    assert pr.resolve_provider("not_a_kind", {}) is None
    assert pr.resolve_provider("push", {}) is None  # 槽位 kind，尚无内置实现


# ── 接入点贯通：llm_client / tts_service 走注册口 ──────────────────


def test_llm_client_via_registry(monkeypatch):
    from openai import AsyncOpenAI

    from app.agent import llm_client
    from app.agent.loop import AGENT_FLAGS

    cfg = {"provider": None, "base_url": "http://127.0.0.1:9/v1"}

    # flag 开（默认）：经注册口 → 内置工厂晚绑定 get_llm_client（patch 接缝不变）
    monkeypatch.setitem(AGENT_FLAGS, "provider_registry", True)
    monkeypatch.setattr(llm_client, "get_llm_client", lambda api_key=None, base_url=None: "DIRECT")
    assert llm_client._client_via_registry(cfg, "k0") == "DIRECT"

    # flag 关：直连（与旧链路逐字节一致）
    monkeypatch.setitem(AGENT_FLAGS, "provider_registry", False)
    assert llm_client._client_via_registry(cfg, "k0") == "DIRECT"

    # 不 patch：flag 开 → 注册口工厂返回真实 AsyncOpenAI 客户端
    monkeypatch.setattr(llm_client, "get_llm_client", lambda api_key=None, base_url=None: AsyncOpenAI(
        api_key=api_key, base_url=base_url))
    monkeypatch.setitem(AGENT_FLAGS, "provider_registry", True)
    assert isinstance(llm_client._client_via_registry(cfg, "k1"), AsyncOpenAI)

    # 插件 provider 经 cfg.provider 精确匹配选中：工厂收到运行时 {api_key, base_url}
    # （来源启用过滤：插件须在 _enabled 中，与 games 同规则）
    import app.plugins.registry as plugin_registry
    from app.providers import registry as _pr
    monkeypatch.setattr(plugin_registry, "_enabled", {"plug1": True, "plug2": True})
    seen: dict = {}

    def _dummy_factory(config):
        seen.update(config)
        return "PLUGIN"

    _pr.register_provider("llm", "dummy_llm", _dummy_factory, source="plug1")
    try:
        got = llm_client._client_via_registry({"provider": "dummy_llm", "base_url": "u"}, "k2")
        assert got == "PLUGIN"
        assert seen == {"api_key": "k2", "base_url": "u"}
    finally:
        _pr.unregister_providers_for_source("plug1")

    # 工厂抛错 → fail-open 回退直连
    def _boom(config):
        raise RuntimeError("boom")

    monkeypatch.setattr(llm_client, "get_llm_client", lambda api_key=None, base_url=None: "DIRECT")
    _pr.register_provider("llm", "boom_llm", _boom, source="plug2")
    try:
        assert llm_client._client_via_registry({"provider": "boom_llm"}, "k3") == "DIRECT"
    finally:
        _pr.unregister_providers_for_source("plug2")


def test_tts_runner_via_registry(monkeypatch, tmp_path):
    from app.agent.loop import AGENT_FLAGS
    from app.application import tts_service

    def _fake_sync(text, voice, cfg, target_dir, fname):
        p = target_dir / fname
        p.write_bytes(b"z" * 300)
        return str(p)

    monkeypatch.setattr(tts_service, "_synth_dashscope_sync", _fake_sync)

    # flag 关 → None（调用方直连内置）
    monkeypatch.setitem(AGENT_FLAGS, "provider_registry", False)
    assert tts_service._tts_runner_via_registry({"provider": ""}) is None

    # flag 开 → 经注册口拿到内置 runner（晚绑定，上面的 patch 经 runner 生效）
    monkeypatch.setitem(AGENT_FLAGS, "provider_registry", True)
    runner = tts_service._tts_runner_via_registry({"provider": "", "model": "qwen-tts"})
    assert runner is not None
    out = asyncio.run(runner("你好", "Cherry", tmp_path, "f.wav"))
    assert out == str(tmp_path / "f.wav")


def test_server_speech_config_carries_provider(monkeypatch):
    """_server_speech_config 返回 dict 带 provider 选择标签（X3 选中通道，additive）。"""
    from app.application import tts_service

    class _Cfg:
        enabled = True
        base_url = "https://x"
        api_key = "k"
        model = "qwen-tts"
        provider = "dummy_tts"

    class _Result:
        @staticmethod
        def scalars():
            class _S:
                @staticmethod
                def first():
                    return _Cfg()
            return _S()

    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, q):
            return _Result()

    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", lambda: _DB(), raising=False)
    out = asyncio.run(tts_service._server_speech_config())
    assert out["enabled"] is True and out["provider"] == "dummy_tts"


# ── SDK 注册口 ──────────────────────────────────────────────────────


def test_sdk_register_provider_requires_plugin_context():
    from app.plugins import sdk
    with pytest.raises(RuntimeError, match="register_provider"):
        sdk.register_provider("llm", "some_llm", lambda c: None)
