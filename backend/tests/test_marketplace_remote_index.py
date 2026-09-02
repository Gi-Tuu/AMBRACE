# -*- coding: utf-8 -*-
"""AMBRACE 3.13 插件市场远程索引接线（2026-09-02/03）测试。

覆盖：
- plugin_market_url 配置回显（config GET 带 allow_remote_install，默认 False=3.9闸）；
- 远程索引成功拉取（source=remote，含 name/download_url/sha256/permissions 字段）；
- 远程索引拉取失败降级本地 plugins/marketplace/index.json（source=remote:local）；
- 内存 TTL 缓存命中不重复拉取；
- 未配置 URL 时返回空且不触发网络；
- 远程索引条目安装仍受默认开关 403 拦截。

全部纯逻辑 + monkeypatch，不触碰真实库/网络/插件目录。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import marketplace as m

_INDEX_URL = "https://example.com/market/index.json"


# ---------------- 夹具 ----------------

@pytest.fixture(autouse=True)
def _reset_remote_index_cache():
    """每个用例重置内存远程索引缓存，避免跨用例污染。"""
    m.clear_remote_index_cache()
    yield
    m.clear_remote_index_cache()


async def _owner_true(user_id: int) -> bool:
    return True


async def _default_cfg() -> dict:
    return {"enabled": False, "urls": [], "refresh_interval_hours": 24,
            "allowed_hosts": [], "max_zip_mb": 10}


# ---------------- 配置回显 ----------------

def test_config_回显远程安装开关默认关闭(monkeypatch):
    monkeypatch.setattr(m, "_is_owner", _owner_true)
    monkeypatch.setattr(m, "settings", SimpleNamespace(plugin_allow_remote_install=False))
    monkeypatch.setattr(m, "_load_config", _default_cfg)
    cfg = asyncio.run(m.get_marketplace_config(user_id=1))
    # 3.9 安全闸默认关闭，且应回显到配置读取接口
    assert cfg["allow_remote_install"] is False


def test_plugin_market_url默认空(monkeypatch):
    # 默认未启用远程索引（空串）；设置后 _plugin_market_url 返回去空格值
    monkeypatch.setattr(m.settings, "plugin_market_url", "")
    assert m._plugin_market_url() == ""
    monkeypatch.setattr(m.settings, "plugin_market_url", "  https://example.com/index.json  ")
    assert m._plugin_market_url() == "https://example.com/index.json"


# ---------------- 远程索引拉取 ----------------

def test_remote_index_成功拉取_source_remote(monkeypatch):
    monkeypatch.setattr(m.settings, "plugin_market_url", _INDEX_URL)
    monkeypatch.setattr(m, "_load_config", _default_cfg)
    index = json.dumps({"market": "测试市场", "items": [
        {"name": "remote_plug", "version": "1.2.3", "description": "远程插件",
         "download_url": "https://example.com/remote_plug.zip", "size": 1024,
         "sha256": "abc123", "permissions": ["write_memory"], "tags": ["test"]},
    ]}).encode("utf-8")
    monkeypatch.setattr(m, "_fetch_bytes", lambda url, t, mb: index)
    items = asyncio.run(m.get_remote_index())
    assert len(items) == 1
    it = items[0]
    assert it["source"] == "remote"
    assert it["name"] == "remote_plug"
    assert it["download_url"] == "https://example.com/remote_plug.zip"
    assert it["sha256"] == "abc123"
    assert it["permissions"] == ["write_memory"]


def test_remote_index_未配置URL返回空不触发网络(monkeypatch):
    monkeypatch.setattr(m.settings, "plugin_market_url", "")
    called = {"n": 0}

    def _boom(url, t, mb):
        called["n"] += 1
        raise AssertionError("未配置时不应触发网络")

    monkeypatch.setattr(m, "_fetch_bytes", _boom)
    assert asyncio.run(m.get_remote_index()) == []
    assert called["n"] == 0


def test_remote_index_缓存命中不重复拉取(monkeypatch):
    monkeypatch.setattr(m.settings, "plugin_market_url", _INDEX_URL)
    monkeypatch.setattr(m, "_load_config", _default_cfg)
    good = json.dumps({"items": [{"name": "a", "download_url": "https://example.com/a.zip"}]}).encode("utf-8")
    calls = {"n": 0}

    def _fake_fetch(url, t, mb):
        calls["n"] += 1
        return good

    monkeypatch.setattr(m, "_fetch_bytes", _fake_fetch)
    assert asyncio.run(m.get_remote_index())[0]["name"] == "a"
    assert calls["n"] == 1
    # TTL 内命中缓存，不再拉取
    assert asyncio.run(m.get_remote_index())[0]["name"] == "a"
    assert calls["n"] == 1


def test_remote_index_失败降级本地(monkeypatch, tmp_path):
    local_index = tmp_path / "index.json"
    local_index.write_text(json.dumps([
        {"name": "local_fallback", "description": "本地兜底", "download_url": "https://example.com/lf.zip"},
    ]), encoding="utf-8")
    monkeypatch.setattr(m.settings, "plugin_market_url", _INDEX_URL)
    monkeypatch.setattr(m, "INDEX_FILE", local_index)

    def _boom(url, t, mb):
        raise OSError("network down")

    monkeypatch.setattr(m, "_fetch_bytes", _boom)
    items = asyncio.run(m.get_remote_index())
    assert len(items) == 1
    assert items[0]["name"] == "local_fallback"
    assert items[0]["source"] == "remote:local"


def test_remote_index_失败优先保留旧缓存(monkeypatch):
    monkeypatch.setattr(m.settings, "plugin_market_url", _INDEX_URL)
    monkeypatch.setattr(m, "_load_config", _default_cfg)
    good = json.dumps({"items": [{"name": "a", "download_url": "https://example.com/a.zip"}]}).encode("utf-8")
    state = {"n": 0}

    def _flaky(url, t, mb):
        state["n"] += 1
        if state["n"] == 1:
            return good
        raise OSError("down now")

    monkeypatch.setattr(m, "_fetch_bytes", _flaky)
    assert asyncio.run(m.get_remote_index())[0]["name"] == "a"
    # 强制过期后再次失败 → 命中旧缓存
    m._remote_index_cache["fetched_at"] = 0.0
    assert asyncio.run(m.get_remote_index())[0]["name"] == "a"


# ---------------- 安装仍被 3.9 默认开关拦截 ----------------

def test_远程索引条目_安装仍被默认开关拦截(monkeypatch):
    # 3.9 默认 plugin_allow_remote_install=False，远程条目安装一律 403
    monkeypatch.setattr(m, "settings", SimpleNamespace(plugin_allow_remote_install=False))
    item = {"name": "x", "download_url": "https://x/x.zip", "sha256": "abc", "source": "remote"}
    with pytest.raises(HTTPException) as ei:
        asyncio.run(m._install_remote(item, "zh"))
    assert ei.value.status_code == 403
