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
    # A2 M4（2026-09-20）：registry._sdk_ctx 由进程级 dict 改为 ContextVar（并发身份不串），
    # 这里改用 registry.sdk_context(...) 上下文管理器设置插件身份；断言口径逐条不变。
    with registry.sdk_context("good_night_topic"):
        sdk.require_permission("send_message")  # 已声明，不抛
    with registry.sdk_context("http_echo"):  # 未声明 send_message
        try:
            sdk.require_permission("send_message")
            raise AssertionError("应抛 PermissionError")
        except PermissionError:
            pass


def test_list_plugins_按flag分别断言_账号收敛(monkeypatch):
    """A2 M3（2026-09-20）：list_plugins 的可见性过滤按 flag 分别断言。

    - flag 关（默认）：viewer 参数被忽略，全量列表 = 既有旧断言；
    - flag 开：只保留「内置 ∪ 本家庭安装 ∪ 服务级（owner 为空）」，且默认 None = 旧行为。
    本用例只注入内存缓存（不加载真实插件、不碰库）。
    """
    from app.agent.loop import AGENT_FLAGS

    def _info(name):
        return {"name": name, "version": "0.0.1", "description": "", "author": "",
                "category": "plugin", "type": "http", "icon": "", "page": "",
                "has_page": False, "hooks": [], "permissions": [], "config": {},
                "usage": "", "display_name": "", "hook_timeout": None,
                "context_keys": [], "content": {}, "path": ""}

    names = ("builtin_x", "mine_local", "other_local", "service_local")
    monkeypatch.setattr(registry, "_loaded",
                        {n: {"info": _info(n), "module": None, "hooks": {},
                             "actions": {}, "router": None} for n in names})
    monkeypatch.setattr(registry, "_enabled", {n: True for n in names})
    monkeypatch.setattr(registry, "_db_config", {})
    monkeypatch.setattr(registry, "_db_prov", {
        "builtin_x": {"source": "builtin", "owner_user_id": None, "owner_tenant_id": None},
        "mine_local": {"source": "local", "owner_user_id": 7, "owner_tenant_id": 7},
        "other_local": {"source": "local", "owner_user_id": 8, "owner_tenant_id": 8},
        "service_local": {"source": "local", "owner_user_id": None, "owner_tenant_id": None},
    })
    expect_all = {"builtin_x", "mine_local", "other_local", "service_local"}

    # flag 关：即使传 viewer，仍是全量旧口径；不传 viewer 同样全量
    monkeypatch.setitem(AGENT_FLAGS, "plugin_user_scope", False)
    assert {p["name"] for p in registry.list_plugins(viewer_user_id=7, viewer_tenant_id=7)} == expect_all
    assert {p["name"] for p in registry.list_plugins()} == expect_all

    # flag 开：本家庭 + 内置 + 服务级可见；别家安装的不可见
    monkeypatch.setitem(AGENT_FLAGS, "plugin_user_scope", True)
    assert {p["name"] for p in registry.list_plugins(viewer_user_id=7, viewer_tenant_id=7)} == {
        "builtin_x", "mine_local", "service_local"}
    # flag 开 + 不传 viewer（既有调用方）→ 仍逐字节旧行为
    assert {p["name"] for p in registry.list_plugins()} == expect_all
    # flag 开 + 家庭根解析失败（viewer_tenant_id=None）→ fail-closed 到内置 ∪ 服务级
    assert {p["name"] for p in registry.list_plugins(viewer_user_id=7)} == {"builtin_x", "service_local"}
