# -*- coding: utf-8 -*-
"""AMBRACE 3.9 插件安全闸（2026-09-02）后端测试。

覆盖：远程安装默认开关 403（1）/ 安装前权限确认（同意缺失拒绝、不一致拒绝、同意成功）（2）/
升级新增权限需重新同意（2）/ 来源+sha256 持久化（3）/ 预留签名校验接口（3）。
全部用临时库 + 临时插件目录隔离，不触碰真实库与真实插件目录。
"""

import asyncio
import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import marketplace as m
from app.plugins import registry


# ---------- 纯函数：同意判定 ----------

def test_consent_state_无权限声明():
    state, needed = registry.consent_state([], [])
    assert state == "empty" and needed == []


def test_consent_state_已同意自动放行():
    state, needed = registry.consent_state(["write_memory"], ["write_memory", "send_message"])
    assert state == "auto" and needed == []


def test_consent_state_新增权限需同意():
    state, needed = registry.consent_state(["write_memory", "send_message"], ["write_memory"])
    assert state == "required" and needed == ["send_message", "write_memory"]


def test_consent_matches_一致才同意():
    assert registry.consent_matches(["write_memory"], True, ["write_memory"])
    # permissions 与 manifest 不一致 → 视为未同意
    assert not registry.consent_matches(["write_memory"], True, ["write_memory", "send_message"])
    assert not registry.consent_matches(["write_memory", "send_message"], True, ["write_memory"])
    assert not registry.consent_matches(["write_memory"], False, ["write_memory"])


def test_verify_plugin_signature_预留恒通过():
    # 预留接口：当前不强制，恒 True；接入签名后应改为真实校验
    assert registry.verify_plugin_signature({"name": "x"}, b"data") is True


# ---------- DB / 目录隔离 fixture ----------

@pytest.fixture
def sec_env(tmp_path, monkeypatch):
    """隔离环境：临时 sqlite（仅 plugins 表）+ 临时插件目录 + 清空 registry 缓存。"""
    from app.models.plugin import Plugin

    async def _create(engine):
        async with engine.begin() as conn:
            await conn.run_sync(Plugin.__table__.create)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sec.db'}", poolclass=NullPool)
    asyncio.run(_create(engine))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.db.database.async_session_factory", factory)

    user_dir = tmp_path / "plugins_user"
    ex_dir = tmp_path / "plugins_examples"
    user_dir.mkdir()
    ex_dir.mkdir()
    monkeypatch.setattr(registry, "USER_DIR", user_dir)
    monkeypatch.setattr(registry, "EXAMPLE_DIR", ex_dir)

    registry._loaded.clear()
    registry._enabled.clear()
    registry._db_config.clear()
    registry._db_prov.clear()

    yield {"engine": engine, "user_dir": user_dir, "ex_dir": ex_dir}
    asyncio.run(engine.dispose())


def _make_zip(manifest: dict, main_py: str = "x = 1\n") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("main.py", main_py)
    return buf.getvalue()


def _install_remote_async(item, body=None, lang="zh"):
    return asyncio.run(m._install_remote(item, lang, body))


# ---------- 需求 1：默认关闭远程市场安装 ----------

def test_remote_install_开关关闭_403(sec_env, monkeypatch):
    monkeypatch.setattr(m, "settings", SimpleNamespace(plugin_allow_remote_install=False))
    monkeypatch.setattr(m, "_load_config", _fake_cfg)
    item = {"name": "x", "download_url": "https://x/x.zip", "source": "remote"}
    with pytest.raises(HTTPException) as ei:
        _install_remote_async(item)
    assert ei.value.status_code == 403


def test_remote_install_开关开启_不下发402(sec_env, monkeypatch):
    # 开关开启后 403 不触发（下载/校验前的其它校验接管）——此处验证开关开启不再 403
    monkeypatch.setattr(m, "settings", SimpleNamespace(plugin_allow_remote_install=True))
    monkeypatch.setattr(m, "_load_config", _fake_cfg)
    item = {"name": "x", "download_url": "http://x/x.zip", "source": "remote"}
    # http 默认被拒（需 debug 放行）→ 400 而非 403
    with pytest.raises(HTTPException) as ei:
        _install_remote_async(item)
    assert ei.value.status_code == 400


# ---------- 需求 2：安装前权限确认 ----------

def test_remote_install_无同意拒绝(sec_env, monkeypatch):
    monkeypatch.setattr(m, "settings", SimpleNamespace(plugin_allow_remote_install=True))
    monkeypatch.setattr(m, "_load_config", _fake_cfg)
    manifest = {"name": "x", "version": "1.0.0", "description": "d",
                "type": "http", "permissions": ["write_memory"]}
    z = _make_zip(manifest)
    monkeypatch.setattr(m, "_fetch_bytes", lambda url, t, mb: z)
    item = {"name": "x", "download_url": "https://x/x.zip", "source": "remote"}
    with pytest.raises(HTTPException) as ei:
        _install_remote_async(item, body={})
    assert ei.value.status_code == 400
    assert "write_memory" in str(ei.value.detail)


def test_remote_install_权限不一致拒绝(sec_env, monkeypatch):
    monkeypatch.setattr(m, "settings", SimpleNamespace(plugin_allow_remote_install=True))
    monkeypatch.setattr(m, "_load_config", _fake_cfg)
    manifest = {"name": "x", "version": "1.0.0", "description": "d",
                "type": "http", "permissions": ["write_memory"]}
    z = _make_zip(manifest)
    monkeypatch.setattr(m, "_fetch_bytes", lambda url, t, mb: z)
    item = {"name": "x", "download_url": "https://x/x.zip", "source": "remote"}
    # 同意但 permissions 与 manifest 不一致 → 视为未同意
    with pytest.raises(HTTPException) as ei:
        _install_remote_async(item, body={"consent": True, "permissions": ["send_message"]})
    assert ei.value.status_code == 400


def test_remote_install_同意成功并记录来源(sec_env, monkeypatch):
    monkeypatch.setattr(m, "settings", SimpleNamespace(plugin_allow_remote_install=True))
    monkeypatch.setattr(m, "_load_config", _fake_cfg)
    manifest = {"name": "x", "version": "1.0.0", "description": "d",
                "type": "http", "permissions": ["write_memory"]}
    z = _make_zip(manifest)
    monkeypatch.setattr(m, "_fetch_bytes", lambda url, t, mb: z)
    item = {"name": "x", "download_url": "https://x/x.zip", "source": "remote"}
    out = _install_remote_async(item, body={"consent": True, "permissions": ["write_memory"]})
    assert out["installed"] is True
    prov = asyncio.run(registry.get_plugin_provenance("x"))
    assert prov["source"] == "remote"
    assert prov["source_url"] == "https://x/x.zip"
    assert prov["sha256"] == hashlib.sha256(z).hexdigest()
    assert prov["consented_permissions"] == ["write_memory"]
    assert prov["consented_at"] is not None


def test_remote_install_升级新增权限需重新同意(sec_env, monkeypatch):
    monkeypatch.setattr(m, "settings", SimpleNamespace(plugin_allow_remote_install=True))
    monkeypatch.setattr(m, "_load_config", _fake_cfg)
    # 首次安装：同意 ["write_memory"]
    z1 = _make_zip({"name": "x", "version": "1.0.0", "description": "d",
                    "type": "http", "permissions": ["write_memory"]})
    monkeypatch.setattr(m, "_fetch_bytes", lambda url, t, mb: z1)
    item = {"name": "x", "download_url": "https://x/x.zip", "source": "remote"}
    _install_remote_async(item, body={"consent": True, "permissions": ["write_memory"]})

    # 升级：新增 send_message，未同意 → 拒绝
    z2 = _make_zip({"name": "x", "version": "2.0.0", "description": "d",
                    "type": "http", "permissions": ["write_memory", "send_message"]})
    monkeypatch.setattr(m, "_fetch_bytes", lambda url, t, mb: z2)
    with pytest.raises(HTTPException) as ei:
        _install_remote_async(item, body={})
    assert ei.value.status_code == 400
    assert "send_message" in str(ei.value.detail)

    # 升级：重新同意 → 成功，权限并入
    out = _install_remote_async(item, body={"consent": True, "permissions": ["write_memory", "send_message"]})
    assert out["installed"] is True
    prov = asyncio.run(registry.get_plugin_provenance("x"))
    assert set(prov["consented_permissions"]) == {"write_memory", "send_message"}


def test_local_zip_不受远程开关限制_同意成功(sec_env, monkeypatch):
    """需求 1 的边界：本地 zip 导入不属于远程市场，不触发 403 开关；但权限非空仍需同意。"""
    # 开关关闭（模拟默认）——zip 导入仍可进行（同意流程接管）
    monkeypatch.setattr(m, "settings", SimpleNamespace(plugin_allow_remote_install=False))
    # 直接走 registry.require_plugin_consent + record（等价 zip 导入的同意/来源落点）
    manifest_perms = ["write_memory"]
    with pytest.raises(HTTPException):
        asyncio.run(registry.require_plugin_consent("lz", manifest_perms, "zh"))
    asyncio.run(registry.require_plugin_consent("lz", manifest_perms, "zh",
                                                consent=True, provided_permissions=["write_memory"]))
    asyncio.run(registry.record_install_provenance("lz", source="local", sha256="abc123"))
    prov = asyncio.run(registry.get_plugin_provenance("lz"))
    assert prov["source"] == "local"
    assert prov["consented_permissions"] == ["write_memory"]


# ---------- 需求 3：来源/哈希持久化 + 列表回显 ----------

def test_list_plugins_回显来源与sha256(sec_env, monkeypatch):
    monkeypatch.setattr(m, "settings", SimpleNamespace(plugin_allow_remote_install=True))
    monkeypatch.setattr(m, "_load_config", _fake_cfg)
    z = _make_zip({"name": "x", "version": "1.0.0", "description": "d",
                   "type": "http", "permissions": []})
    monkeypatch.setattr(m, "_fetch_bytes", lambda url, t, mb: z)
    item = {"name": "x", "download_url": "https://x/x.zip", "source": "remote"}
    _install_remote_async(item, body={})  # 无权限声明 → 无需同意
    plugins = registry.list_plugins()
    x = next(p for p in plugins if p["name"] == "x")
    assert x["source"] == "remote"
    assert x["sha256"] == hashlib.sha256(z).hexdigest()
    assert x["consented_permissions"] == []


# ---------- 工具 ----------

def _fake_cfg():
    async def _inner():
        return {"max_zip_mb": 10, "allowed_hosts": [], "_debug_allow_http": False, "enabled": True}
    return _inner()
