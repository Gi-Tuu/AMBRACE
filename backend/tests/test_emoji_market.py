# -*- coding: utf-8 -*-
"""表情市场测试：索引解析/缓存降级（mock HTTP）、manifest 强校验、安装卸载（临时目录+临时库）、packs 合并。"""

import asyncio
import hashlib
import io
import json
import os
import tempfile
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.base import Base
from app.models.emoji_pack import UserEmojiPack
from app.plugins.zip_safety import ZipSafetyError
from app.services import emoji_market as m

_INDEX_URL = "https://raw.githubusercontent.com/Gi-Tuu/AMBRACE-emoji/main/index.json"


# ---------------- 夹具 ----------------

@pytest.fixture(autouse=True)
def _reset_market_cache():
    """每个用例重置内存索引缓存，避免跨用例污染。"""
    m._index_cache["data"] = None
    m._index_cache["fetched_at"] = 0.0
    yield
    m._index_cache["data"] = None
    m._index_cache["fetched_at"] = 0.0


@pytest.fixture()
def market_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch emoji_market.async_session_factory（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix="emoji_market_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(m, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _make_zip(files_raw: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files_raw.items():
            if name.endswith(".json"):
                zf.writestr(name, json.dumps(content, ensure_ascii=False))
            else:
                zf.writestr(name, content)
    return buf.getvalue()


def _manifest(pid="cute") -> dict:
    return {
        "id": pid,
        "name": "可爱",
        "description": "可爱表情包",
        "version": "1.0.0",
        "icon": "icon.png",
        "emojis": [
            {"file": "a.png", "name": "开心", "meaning": "开心"},
            {"file": "b.png", "name": "难过", "meaning": "难过"},
        ],
    }


def _valid_zip_bytes(manifest=None) -> bytes:
    return _make_zip({
        "manifest.json": manifest or _manifest(),
        "icon.png": b"\x89PNG",
        "a.png": b"\x89PNGa",
        "b.png": b"\x89PNGb",
    })


# ---------------- 索引解析 / 域名白名单 ----------------

def test_parse_index_合法与非法条目():
    data = json.dumps([
        {"id": "cute", "name": "可爱", "description": "d", "version": "1.0.0",
         "icon": "icon.png", "file": "cute.zip", "sha256": "abc", "size": 100, "emoji_count": 2},
        {"name": "无id", "file": "x.zip"},          # 缺 id 丢弃
        {"id": "bad/id", "name": "坏id", "file": "y.zip"},  # id 含非法字符丢弃
        "not-a-dict",                               # 非对象丢弃
    ]).encode("utf-8")
    idx = m._parse_index(data)
    assert len(idx) == 1
    assert idx[0]["id"] == "cute"
    assert idx[0]["emoji_count"] == 2


def test_parse_index_非数组报错():
    with pytest.raises(ValueError):
        m._parse_index(b"{}")
    with pytest.raises(ValueError):
        m._parse_index(b"\xff\xfe")


def test_is_allowed_host():
    assert m._is_allowed_host("https://raw.githubusercontent.com/Gi-Tuu/AMBRACE-emoji/main/a.zip")
    assert m._is_allowed_host("https://github.com/x/release/download/v1/a.zip")
    assert not m._is_allowed_host("https://evil.com/a.zip")
    assert not m._is_allowed_host("http://raw.githubusercontent.com/a.zip")


# ---------------- 索引缓存 / 失败降级 ----------------

def test_get_market_index_缓存命中不重复拉取(monkeypatch):
    monkeypatch.setattr(m.settings, "emoji_market_url", _INDEX_URL)
    calls = {"n": 0}
    good = json.dumps([{"id": "a", "name": "A", "file": "a.zip"}]).encode("utf-8")

    def _fake_fetch(url, t, mb):
        calls["n"] += 1
        return good

    monkeypatch.setattr(m, "_fetch_bytes", _fake_fetch)
    first = asyncio.run(m.get_market_index())
    assert calls["n"] == 1
    assert first[0]["id"] == "a"
    second = asyncio.run(m.get_market_index())
    assert calls["n"] == 1  # TTL 内命中缓存，不再拉取
    assert second == first


def test_get_market_index_失败降级空列表(monkeypatch):
    monkeypatch.setattr(m.settings, "emoji_market_url", _INDEX_URL)

    def _boom(url, t, mb):
        raise OSError("network down")

    monkeypatch.setattr(m, "_fetch_bytes", _boom)
    assert asyncio.run(m.get_market_index()) == []


def test_get_market_index_失败保留旧缓存(monkeypatch):
    monkeypatch.setattr(m.settings, "emoji_market_url", _INDEX_URL)
    good = json.dumps([{"id": "a", "name": "A", "file": "a.zip"}]).encode("utf-8")
    state = {"n": 0}

    def _flaky(url, t, mb):
        state["n"] += 1
        if state["n"] == 1:
            return good
        raise OSError("down now")

    monkeypatch.setattr(m, "_fetch_bytes", _flaky)
    assert asyncio.run(m.get_market_index())[0]["id"] == "a"
    # 强制过期缓存后再次拉取失败 → 返回旧缓存
    m._index_cache["fetched_at"] = 0.0
    idx = asyncio.run(m.get_market_index())
    assert idx[0]["id"] == "a"


# ---------------- manifest 强校验 ----------------

def test_manifest_缺必填字段():
    fm = {"icon.png": ("icon.png", 100), "a.png": ("a.png", 100)}
    base = _manifest()
    for field in ("id", "name", "description", "version", "icon", "emojis"):
        bad = dict(base)
        bad.pop(field, None)
        err = m.validate_emoji_manifest(bad, fm)
        assert err, f"缺少 {field} 未报错"


def test_manifest_emojis_empty_rejected():
    fm = {"icon.png": ("icon.png", 100)}
    man = _manifest()
    man["emojis"] = []
    assert m.validate_emoji_manifest(man, fm) is not None


def test_manifest_坏扩展名_rejected():
    fm = {"icon.png": ("icon.png", 100), "a.gif": ("a.gif", 100)}
    man = _manifest()
    man["emojis"][0]["file"] = "a.gif"
    assert "扩展名不在白名单" in m.validate_emoji_manifest(man, fm)


def test_manifest_文件不存在_rejected():
    fm = {"icon.png": ("icon.png", 100)}
    man = _manifest()
    assert "文件不存在" in m.validate_emoji_manifest(man, fm)


def test_manifest_单贴图超2MB_rejected():
    fm = {"icon.png": ("icon.png", 100), "a.png": ("a.png", 2 * 1024 * 1024 + 1), "b.png": ("b.png", 100)}
    assert "超过 2MB" in m.validate_emoji_manifest(_manifest(), fm)


def test_manifest_总重超20MB_rejected():
    fm = {"icon.png": ("icon.png", 100)}
    emojis = []
    for i in range(11):
        fname = f"e{i}.png"
        fm[fname] = (fname, 2 * 1024 * 1024)  # 每个 ≤2MB，11 个共 22MB
        emojis.append({"file": fname, "name": f"n{i}", "meaning": f"m{i}"})
    man = _manifest()
    man["emojis"] = emojis
    assert "超过 20MB" in m.validate_emoji_manifest(man, fm)


def test_manifest_合法通过():
    fm = {"icon.png": ("icon.png", 100), "a.png": ("a.png", 200), "b.png": ("b.png", 200)}
    assert m.validate_emoji_manifest(_manifest(), fm) is None


# ---------------- zip 安全校验 ----------------

def test_validate_emoji_zip_合法():
    data = _valid_zip_bytes()
    man, names = m.validate_emoji_zip(data)
    assert man["id"] == "cute"
    assert "manifest.json" in names


def test_validate_emoji_zip_缺manifest():
    with pytest.raises(ZipSafetyError) as ei:
        m.validate_emoji_zip(_make_zip({"a.png": b"x"}))
    assert ei.value.key == "zip_no_manifest"


def test_validate_emoji_zip_路径穿越():
    with pytest.raises(ZipSafetyError) as ei:
        m.validate_emoji_zip(_make_zip({"../evil": b"x", "manifest.json": _manifest()}))
    assert ei.value.key == "zip_illegal_path"


def test_validate_emoji_zip_重名文件():
    # 两个不同目录落到同一解压文件名 → 拒绝
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(_manifest(), ensure_ascii=False))
        zf.writestr("a.png", b"1")
        zf.writestr("x/a.png", b"2")
    with pytest.raises(ZipSafetyError) as ei:
        m.validate_emoji_zip(buf.getvalue())
    assert ei.value.key == "emoji_zip_dup_file"


# ---------------- 安装 / 卸载 ----------------

def test_install_uninstall(market_db, tmp_path, monkeypatch):
    zip_bytes = _valid_zip_bytes()
    sha = hashlib.sha256(zip_bytes).hexdigest()
    monkeypatch.setattr(m, "MARKET_PACK_ROOT", tmp_path)
    monkeypatch.setattr(m, "_is_allowed_host", lambda url: True)
    monkeypatch.setattr(m, "_resolve_market_url", lambda rel: f"https://raw.githubusercontent.com/x/{rel}")
    monkeypatch.setattr(m, "_fetch_bytes", lambda url, t, mb: zip_bytes)

    async def _idx():
        return [{
            "id": "cute", "name": "可爱", "description": "d", "version": "1.0.0",
            "icon": "icon.png", "file": "cute.zip", "sha256": sha,
            "size": len(zip_bytes), "emoji_count": 2,
        }]

    monkeypatch.setattr(m, "get_market_index", _idx)

    pack = asyncio.run(m.download_market_pack(1, "cute", "zh"))
    assert pack["id"] == "market:cute"
    assert (tmp_path / "cute" / "manifest.json").is_file()
    assert (tmp_path / "cute" / "a.png").is_file()

    async def _count():
        async with market_db() as db:
            rows = (await db.execute(select(UserEmojiPack).where(
                UserEmojiPack.user_id == 1, UserEmojiPack.pack_id == "market:cute"
            ))).scalars().all()
        return len(rows)

    assert asyncio.run(_count()) == 1

    res = asyncio.run(m.uninstall_market_pack(1, "cute", "zh"))
    assert res["ok"] is True
    assert not (tmp_path / "cute").exists()
    assert asyncio.run(_count()) == 0


def test_download_sha校验失败(market_db, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "MARKET_PACK_ROOT", tmp_path)
    monkeypatch.setattr(m, "_is_allowed_host", lambda url: True)
    monkeypatch.setattr(m, "_resolve_market_url", lambda rel: "https://raw.githubusercontent.com/x/" + rel)
    monkeypatch.setattr(m, "_fetch_bytes", lambda url, t, mb: _valid_zip_bytes())

    async def _idx():
        return [{"id": "cute", "name": "可爱", "file": "cute.zip", "sha256": "deadbeef", "icon": "icon.png"}]

    monkeypatch.setattr(m, "get_market_index", _idx)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(m.download_market_pack(1, "cute", "zh"))
    assert ei.value.status_code == 400


def test_download_域名白名单拒绝(market_db, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "MARKET_PACK_ROOT", tmp_path)
    monkeypatch.setattr(m, "_is_allowed_host", lambda url: False)
    monkeypatch.setattr(m, "_resolve_market_url", lambda rel: "https://evil.com/" + rel)

    async def _idx():
        return [{"id": "cute", "name": "可爱", "file": "cute.zip", "sha256": "", "icon": "icon.png"}]

    monkeypatch.setattr(m, "get_market_index", _idx)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(m.download_market_pack(1, "cute", "zh"))
    assert ei.value.status_code == 403


def test_uninstall_未安装返回404(market_db, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "MARKET_PACK_ROOT", tmp_path)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(m.uninstall_market_pack(1, "missing", "zh"))
    assert ei.value.status_code == 404


# ---------------- packs 列表合并 ----------------

def test_list_installed_market_packs_合并(market_db, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "MARKET_PACK_ROOT", tmp_path)
    pack_dir = tmp_path / "cute"
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(json.dumps(_manifest(), ensure_ascii=False), encoding="utf-8")

    # 写入用户下载记录
    async def _seed():
        async with market_db() as db:
            db.add(UserEmojiPack(user_id=1, pack_id="market:cute", pack_name="可爱"))
            await db.commit()

    asyncio.run(_seed())
    packs = asyncio.run(m.list_installed_market_packs(1))
    assert len(packs) == 1
    assert packs[0]["id"] == "market:cute"
    assert packs[0]["type"] == "market"
    assert packs[0]["downloaded"] is True
    assert packs[0]["emojis"][0]["url"] == "/uploads/emojis/market/cute/a.png"
    assert packs[0]["emojis"][0]["name"] == "开心"
    # 未下载用户看不到
    assert asyncio.run(m.list_installed_market_packs(2)) == []


def test_download写入后packs列表合并出现(market_db, tmp_path, monkeypatch):
    zip_bytes = _valid_zip_bytes()
    sha = hashlib.sha256(zip_bytes).hexdigest()
    monkeypatch.setattr(m, "MARKET_PACK_ROOT", tmp_path)
    monkeypatch.setattr(m, "_is_allowed_host", lambda url: True)
    monkeypatch.setattr(m, "_resolve_market_url", lambda rel: "https://raw.githubusercontent.com/x/" + rel)
    monkeypatch.setattr(m, "_fetch_bytes", lambda url, t, mb: zip_bytes)

    async def _idx():
        return [{
            "id": "cute", "name": "可爱", "description": "d", "version": "1.0.0",
            "icon": "icon.png", "file": "cute.zip", "sha256": sha, "size": len(zip_bytes), "emoji_count": 2,
        }]

    monkeypatch.setattr(m, "get_market_index", _idx)
    asyncio.run(m.download_market_pack(1, "cute", "zh"))
    packs = asyncio.run(m.list_market_packs(1))
    assert packs[0]["installed"] is True
    # 未安装用户
    packs2 = asyncio.run(m.list_market_packs(2))
    assert packs2[0]["installed"] is False
