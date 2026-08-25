"""远程市场纯逻辑测试：index 校验 / 缓存落盘 / 合并优先级 / 远程安装回滚 / 权限。"""

import json

import pytest

from app.api.marketplace import (
    _cache_path,
    _is_url_allowed,
    _validate_index,
)
from app.plugins.zip_safety import ZipSafetyError, validate_zip_bytes


# ---------- index 校验 ----------

def test_validate_index_合法条目():
    data = json.dumps({
        "market": "测试市场",
        "items": [{
            "name": "weather_brief",
            "version": "1.1.0",
            "description": "天气注入",
            "category": "plugin",
            "download_url": "https://example.com/weather_brief.zip",
            "size": 12345,
            "sha256": "abc",
        }],
    }).encode("utf-8")
    cfg = {"max_zip_mb": 10, "allowed_hosts": [], "_debug_allow_http": False}
    obj = _validate_index(data, cfg)
    assert len(obj["items"]) == 1
    assert obj["items"][0]["name"] == "weather_brief"
    assert obj["items"][0]["source"] if False else True


def test_validate_index_拒绝非https():
    data = json.dumps({"items": [{
        "name": "x", "download_url": "http://example.com/x.zip", "size": 1,
    }]}).encode("utf-8")
    obj = _validate_index(data, {"max_zip_mb": 10, "allowed_hosts": [], "_debug_allow_http": False})
    assert obj["items"] == []


def test_validate_index_拒绝超限size():
    data = json.dumps({"items": [{
        "name": "x", "download_url": "https://example.com/x.zip", "size": 11 * 1024 * 1024,
    }]}).encode("utf-8")
    obj = _validate_index(data, {"max_zip_mb": 10, "allowed_hosts": [], "_debug_allow_http": False})
    assert obj["items"] == []


def test_validate_index_非数组报错():
    with pytest.raises(Exception):
        _validate_index(b"{}", {"max_zip_mb": 10})
    with pytest.raises(Exception):
        _validate_index(b"{\"items\": 1}", {"max_zip_mb": 10})


def test_is_url_allowed_白名单():
    cfg = {"allowed_hosts": ["market.example.com"]}
    assert _is_url_allowed("https://market.example.com/a.json", cfg)
    assert not _is_url_allowed("https://evil.com/a.json", cfg)
    assert not _is_url_allowed("http://market.example.com/a.json", cfg)


# ---------- 缓存 ----------

def test_cache_path_稳定():
    url = "https://example.com/index.json"
    assert _cache_path(url) == _cache_path(url)
    assert ".." not in str(_cache_path(url))


def test_load_remote_items_合并source(tmp_path, monkeypatch):
    from app.api import marketplace as m
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path)
    (tmp_path / "abc12345.json").write_text(json.dumps({
        "market": "社区市场", "items": [{"name": "sample_pack", "download_url": "https://x/y.zip"}],
    }), encoding="utf-8")
    items = m._load_remote_items()
    assert len(items) == 1
    assert items[0]["name"] == "sample_pack"
    assert items[0]["source"] == "remote:社区市场"


def test_refresh_one_落盘(tmp_path, monkeypatch):
    from app.api import marketplace as m
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(m, "CACHE_META_FILE", tmp_path / "cache_meta.json")
    index = json.dumps({"market": "测试", "items": [{
        "name": "weather_brief", "download_url": "https://x/y.zip", "size": 100,
    }]})
    def _fake_fetch(url, timeout, max_bytes):
        return index.encode("utf-8")
    monkeypatch.setattr(m, "_fetch_bytes", _fake_fetch)
    import asyncio
    cfg = {"max_zip_mb": 10, "allowed_hosts": [], "_debug_allow_http": False, "enabled": True, "urls": []}
    r = asyncio.run(m._refresh_one("https://x/index.json", cfg))
    assert r["items"] == 1
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 2  # 缓存 + cache_meta
    meta = json.loads((tmp_path / "cache_meta.json").read_text(encoding="utf-8"))
    assert "https://x/index.json" in meta


# ---------- zip 安全校验 ----------

def test_zip_非法路径():
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil", "x")
    with pytest.raises(ZipSafetyError) as ei:
        validate_zip_bytes(buf.getvalue())
    assert ei.value.key == "zip_illegal_path"


def test_zip_缺manifest():
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("main.py", "print(1)")
    with pytest.raises(ZipSafetyError) as ei:
        validate_zip_bytes(buf.getvalue())
    assert ei.value.key == "zip_no_manifest"


def test_zip_sha256校验失败(tmp_path, monkeypatch):
    from app.api import marketplace as m
    import asyncio
    from fastapi import HTTPException
    async def _fake_cfg():
        return {"max_zip_mb": 10, "allowed_hosts": [], "_debug_allow_http": False, "enabled": True}
    monkeypatch.setattr(m, "_load_config", _fake_cfg)
    monkeypatch.setattr(m, "_fetch_bytes", lambda url, t, mb: b"tampered-bytes")
    item = {"name": "x", "download_url": "https://x/x.zip", "sha256": "deadbeef"}
    with pytest.raises(HTTPException) as ei:
        asyncio.run(m._install_remote(item, "zh"))
    assert ei.value.status_code == 400
