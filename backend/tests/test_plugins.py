# -*- coding: utf-8 -*-
import asyncio
import pytest
from app.plugins import manifest, registry, sdk

EXAMPLE = registry.EXAMPLE_DIR


@pytest.fixture(scope="module", autouse=True)
def _ensure_example_plugins_loaded():
    """确保内置示例插件已加载：修复本文件 hook 测试对 test_示例插件可加载 的隐式依赖，
    使 `pytest -k 'bm25 or memory'` 这类按名筛选（不选中加载测试）时也能顺序无关地通过。"""
    if not EXAMPLE.is_dir():
        return
    for p in EXAMPLE.iterdir():
        if p.is_dir() and (p / "manifest.json").is_file():
            registry.load_plugin_dir(p)


def test_新示例插件_manifest_合法():
    for name in ("good_night_topic", "http_echo"):
        m = manifest.load_manifest(str(EXAMPLE / name / "manifest.json"))
        assert m is not None, name
        assert m["name"] == name


def test_示例插件可加载():
    for name in ("good_night_topic", "http_echo"):
        info = registry.load_plugin_dir(EXAMPLE / name)
        assert info is not None, name
        assert info["name"] == name
    # hook / 权限注册完整性
    assert "proactive_candidate" in registry._loaded["good_night_topic"]["hooks"]
    assert "send_message" in registry._loaded["good_night_topic"]["info"]["permissions"]
    assert "memory_search" in registry._loaded["http_echo"]["hooks"]
    assert "http_router" in registry._loaded["http_echo"]["info"]["hooks"]
    assert registry._loaded["http_echo"].get("router") is not None


def test_memory_search_hook_注入():
    registry._enabled["http_echo"] = True
    try:
        results = asyncio.run(registry.run_hook_collect(
            "memory_search", {"query": "什么是插件", "results": [], "limit": 5, "character_id": 1},
        ))
        assert results, "应收集到 http_echo 注入结果"
        injected = [r["result"] for r in results if r.get("plugin") == "http_echo"]
        assert injected and injected[0] and injected[0][0]["id"] == -1001
        assert "插件" in injected[0][0]["content"]
    finally:
        registry._enabled.pop("http_echo", None)


def test_memory_search_无关关键词不注入():
    registry._enabled["http_echo"] = True
    try:
        results = asyncio.run(registry.run_hook_collect(
            "memory_search", {"query": "今天天气怎么样", "results": [], "limit": 5, "character_id": 1},
        ))
        assert not any(r.get("plugin") == "http_echo" for r in results)
    finally:
        registry._enabled.pop("http_echo", None)


def test_hook_分发对未知hook安全():
    assert asyncio.run(registry.run_hook("not_exist_hook", {})) is None
    assert asyncio.run(registry.run_hook_collect("not_exist_hook", {})) == []


def test_send_message_权限检查():
    registry._sdk_ctx["current"] = "good_night_topic"
    try:
        sdk.require_permission("send_message")  # 已声明，不抛
    finally:
        registry._sdk_ctx.pop("current", None)
    registry._sdk_ctx["current"] = "http_echo"  # 未声明 send_message
    try:
        try:
            sdk.require_permission("send_message")
            raise AssertionError("应抛 PermissionError")
        except PermissionError:
            pass
    finally:
        registry._sdk_ctx.pop("current", None)
