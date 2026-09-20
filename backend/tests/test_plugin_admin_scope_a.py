# -*- coding: utf-8 -*-
"""A2 插件归户 · 批次 A（M0-4 + M1 + M2）测试（2026-09-20）。

覆盖（与派单 §四 一一对应）：

- **M2 权限矩阵**：``is_admin=1 且 server_admin=0`` 的账号调插件/市场管理端点 → 403；
  ``server_admin=1`` → 200（安装 / 启停改配置 PUT / 卸载 / 市场配置 / 市场安装 / refresh）；
- **M2 me 响应**：``GET /api/v1/auth/profile`` 追加 ``server_admin``，既有字段一个不动；
- **M2 渠道绑定**：目标渠道被别的家庭根占用 → 409（复用既有 key，不静默覆盖）；
  本家庭内占用 → 400（原语义不变）；
- **M1 迁移**：临时库 upgrade 加两列 / 幂等 / downgrade 可逆 / 再 upgrade；
  哨兵登记（``_CURRENT_SCHEMA_SENTINELS``）；alembic 单头且新修订在链上；
- **M1 写入**：本地 zip 安装 / 远程市场安装 / 内置市场安装落 ``owner_user_id`` +
  ``owner_tenant_id``；``sync_plugins_db`` 的 builtin 同步路径保持 NULL；
  重复安装/重复同步不覆盖已有 owner；
- **M0-4**：``after_generate`` 与 ``memory_search`` 的 hook ctx 含 ``user_id``（假 hook 捕获断言）。

隔离：全程私有临时库（pytest ``tmp_path``）+ 把 app.* 各模块的 ``async_session_factory``
换成私有工厂（同 ``test_admin_console_p2._patch_session_factories`` 口径）；``registry.USER_DIR``
指向 ``tmp_path``，市场下载/插件加载打桩 —— **不连生产库、不写 ``backend/data``**。
"""
import asyncio
import importlib.util
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import marketplace as market_api
from app.api import plugins as plugins_api
from app.auth.config import create_token
from app.auth.router import router as auth_router
from app.plugins import registry

# 账号：1=server_admin 家庭根；2=is_admin=1 但 server_admin=0（本批会失去插件管理入口）；
# 3=1 的子账号（is_admin=0）；4=另一个家庭根（is_admin=1，非 server_admin，用于跨家庭占用）；
# 5=第二个 server_admin（用于验证「重复安装不覆盖已有 owner」）。
ROOT_UID, HOME_UID, SUB_UID, OTHER_UID, ADMIN2_UID = 1, 2, 3, 4, 5

# 角色：11/12 属账号 1 的家庭；21 属账号 2；41 属账号 4（别的家庭根）
CHAR_ROOT_A, CHAR_ROOT_B, CHAR_HOME, CHAR_OTHER = 11, 12, 21, 41


# ---------------------------------------------------------------- 私有临时库

def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库工厂接到所有 app.* 模块的 ``async_session_factory`` 名上（含早绑定引用）。

    与 ``test_admin_console_p2._patch_session_factories`` 同法：``app.db.database`` 是接缝，
    但 permission_service / auth.router / registry 等模块在 import 期或函数内取该名，
    需逐个换掉，保证被调代码一律落在私有库。
    """
    import app.db.database as db_mod
    import app.db.session as session_mod

    original = db_mod.async_session_factory
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(session_mod, "async_session_factory", factory, raising=False)
    for name, mod in list(sys.modules.items()):
        if not (name == "app" or name.startswith("app.")):
            continue
        try:
            if getattr(mod, "async_session_factory", None) is original:
                monkeypatch.setattr(mod, "async_session_factory", factory)
        except Exception:
            continue


@pytest.fixture()
def a_db(monkeypatch, tmp_path):
    """私有临时 SQLite：server_admin / 非 server_admin 家庭主账号 / 子账号 / 另一家庭。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'a2.db'}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册主 metadata
        from app.models.base import Base
        from app.models.character import AICharacter
        from app.models.user import User

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([
                User(id=ROOT_UID, username="root", nickname="服务器管理员",
                     is_admin=True, server_admin=True),
                User(id=HOME_UID, username="home", nickname="家庭主账号",
                     is_admin=True, server_admin=False),
                User(id=SUB_UID, username="sub", nickname="子账号",
                     is_admin=False, parent_id=ROOT_UID, server_admin=False),
                User(id=OTHER_UID, username="other", nickname="别的家庭根",
                     is_admin=True, server_admin=False),
                User(id=ADMIN2_UID, username="root2", nickname="第二个服务器管理员",
                     is_admin=True, server_admin=True),
            ])
            db.add_all([
                AICharacter(id=CHAR_ROOT_A, user_id=ROOT_UID, name="R-A"),
                AICharacter(id=CHAR_ROOT_B, user_id=ROOT_UID, name="R-B"),
                AICharacter(id=CHAR_HOME, user_id=HOME_UID, name="H-1"),
                AICharacter(id=CHAR_OTHER, user_id=OTHER_UID, name="O-1"),
            ])
            await db.commit()

    asyncio.run(_init())
    _patch_session_factories(monkeypatch, factory)
    yield factory
    asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _clear_registry_and_market_caches():
    """清插件注册缓存与远程市场内存索引（跨用例残留会让归属/启用判定串味）。"""
    caches = (registry._loaded, registry._enabled, registry._db_config, registry._db_prov)
    for d in caches:
        d.clear()
    market_api.clear_remote_index_cache()
    yield
    for d in caches:
        d.clear()
    market_api.clear_remote_index_cache()


@pytest.fixture()
def plugin_env(monkeypatch, tmp_path):
    """插件安装/管理隔离环境：USER_DIR → tmp_path；DB 同步与插件加载打桩、绝不写 backend/data。"""
    user_dir = tmp_path / "userplugins"
    user_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(registry, "USER_DIR", user_dir)

    async def _sync_noop():
        return None

    monkeypatch.setattr(registry, "sync_plugins_db", _sync_noop)
    # 管理端点用到的「已加载插件」：只回一个假条目（不 import 任何真实插件）
    monkeypatch.setattr(
        registry, "get_plugin",
        lambda name: {"name": name, "type": "http", "enabled": False, "config": {}},
    )

    async def _set_state(name, enabled=None, config=None):
        return {"name": name, "enabled": bool(enabled), "config": config or {}}

    monkeypatch.setattr(registry, "set_plugin_state", _set_state)

    def _resolve(name):
        p = user_dir / str(name)
        p.mkdir(parents=True, exist_ok=True)
        return p

    monkeypatch.setattr(registry, "resolve_plugin_dir", _resolve)
    yield user_dir


REMOTE_PLUGIN_NAME = "remote_demo"

REMOTE_ITEM = {
    "name": REMOTE_PLUGIN_NAME,
    "version": "1.0.0",
    "description": "远程测试插件",
    "author": "t",
    "category": "plugin",
    "type": "http",
    "icon": "",
    "page": "",
    "has_page": False,
    "hooks": [],
    "permissions": [],
    "config": {},
    "usage": "",
    "download_url": "https://market.example/remote_demo.zip",
    "size": 0,
    "sha256": None,
    "tags": [],
    "updated_at": "",
    "min_api_version": "",
    "source": "remote",
}


@pytest.fixture()
def market_env(monkeypatch, plugin_env):
    """市场端点隔离环境：远程索引命中内存缓存 + 下载/插件加载打桩（不触网、不写 backend/data）。"""
    monkeypatch.setattr(market_api.settings, "plugin_allow_remote_install", True)
    monkeypatch.setattr(market_api, "_load_remote_items", lambda: [])
    monkeypatch.setattr(
        market_api, "_remote_index_cache",
        {"items": [dict(REMOTE_ITEM)], "fetched_at": time.time()},
    )
    zip_bytes = _make_zip(REMOTE_PLUGIN_NAME)
    monkeypatch.setattr(market_api, "_fetch_bytes", lambda url, timeout, max_bytes: zip_bytes)
    monkeypatch.setattr(market_api, "_is_url_allowed", lambda url, cfg: True)

    async def _cfg():
        return {
            "enabled": True,
            "urls": ["https://market.example/index.json"],
            "refresh_interval_hours": 24,
            "allowed_hosts": [],
            "max_zip_mb": 10,
        }

    monkeypatch.setattr(market_api, "_load_config", _cfg)

    async def _refresh_one(url, cfg):
        return {"url": url, "market": "测试市场", "items": 0}

    monkeypatch.setattr(market_api, "_refresh_one", _refresh_one)
    # 安装后「加载校验」打桩：只回名字（真实 import 插件会带来跨用例副作用）
    monkeypatch.setattr(registry, "load_plugin_dir", lambda p: {"name": Path(p).name})
    yield plugin_env


# ---------------------------------------------------------------- helpers

def _client() -> TestClient:
    app = FastAPI()
    app.include_router(plugins_api.router)
    app.include_router(market_api.router)
    app.include_router(auth_router)
    return TestClient(app, raise_server_exceptions=False)


def _auth(uid: int) -> dict:
    """真实 JWT（conftest 固定 AUTH_SECRET_KEY）——走真实鉴权 + 真实 server_admin 判定。"""
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _make_zip(name: str, manifest_extra: dict | None = None) -> bytes:
    manifest = {"name": name, "version": "1.0.0", "description": "a2 测试包", "type": "http"}
    manifest.update(manifest_extra or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # zip 根目录直接放 manifest.json（validate_zip_bytes 要求顶层 manifest）
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("main.py", "x = 1\n")
    return buf.getvalue()


def _plugin_row(factory, name: str):
    """返回 (owner_user_id, owner_tenant_id, source)；无行 → None。"""
    from app.models.plugin import Plugin

    async def _go():
        async with factory() as db:
            row = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
            if row is None:
                return None
            return (row.owner_user_id, row.owner_tenant_id, row.source)

    return asyncio.run(_go())


def _seed_plugin_config(factory, name: str, config_json: str) -> None:
    from app.models.plugin import Plugin

    async def _go():
        async with factory() as db:
            row = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
            if row is None:
                db.add(Plugin(name=name, version="1.0.0", description="", config_json=config_json))
            else:
                row.config_json = config_json
            await db.commit()

    asyncio.run(_go())


# ═══════════════════════════════════════════════════════════════════════════════
# M2：权限矩阵（is_admin=1 且 server_admin=0 → 403；server_admin=1 → 200）
# ═══════════════════════════════════════════════════════════════════════════════

def test_插件管理端点_权限矩阵(a_db, plugin_env):
    """安装 / 启停改配置 PUT / 卸载：非 server_admin（家庭主账号 + 子账号）一律 403。"""
    c = _client()
    zip_bytes = _make_zip("a2_matrix")

    for uid in (HOME_UID, SUB_UID):
        h = _auth(uid)
        r = c.post("/api/v1/plugins/install", headers=h,
                   files={"file": ("a2.zip", zip_bytes, "application/zip")})
        assert r.status_code == 403, (uid, r.text)
        assert c.put("/api/v1/plugins/http_echo", headers=h, json={"enabled": True}).status_code == 403
        assert c.delete("/api/v1/plugins/a2_matrix", headers=h).status_code == 403

    h = _auth(ROOT_UID)
    assert c.put("/api/v1/plugins/http_echo", headers=h, json={"enabled": True}).status_code == 200
    assert c.delete("/api/v1/plugins/a2_matrix", headers=h).status_code == 200
    r = c.post("/api/v1/plugins/install", headers=h,
               files={"file": ("a2.zip", zip_bytes, "application/zip")})
    assert r.status_code == 200, r.text


def test_市场管理端点_权限矩阵(a_db, plugin_env, market_env):
    """市场配置读/写、refresh、市场安装（内置 + 远程）：非 server_admin 403，server_admin 200。"""
    c = _client()
    for uid in (HOME_UID, SUB_UID):
        h = _auth(uid)
        assert c.get("/api/v1/marketplace/config", headers=h).status_code == 403
        assert c.put("/api/v1/marketplace/config", headers=h, json={"max_zip_mb": 5}).status_code == 403
        assert c.post("/api/v1/marketplace/refresh?force=true", headers=h).status_code == 403
        assert c.post("/api/v1/marketplace/http_echo/install", headers=h, json={}).status_code == 403
        assert c.post(f"/api/v1/marketplace/{REMOTE_PLUGIN_NAME}/install",
                      headers=h, json={}).status_code == 403

    h = _auth(ROOT_UID)
    assert c.get("/api/v1/marketplace/config", headers=h).status_code == 200
    assert c.put("/api/v1/marketplace/config", headers=h, json={"max_zip_mb": 5}).status_code == 200
    assert c.post("/api/v1/marketplace/refresh?force=true", headers=h).status_code == 200
    r = c.post("/api/v1/marketplace/http_echo/install", headers=h, json={})
    assert r.status_code == 200, r.text
    r = c.post(f"/api/v1/marketplace/{REMOTE_PLUGIN_NAME}/install", headers=h, json={})
    assert r.status_code == 200, r.text


def test_me响应含server_admin且既有字段不动(a_db):
    """me（GET /auth/profile）只追加 server_admin；is_admin/parent_id/is_sub 原样。"""
    c = _client()
    body = c.get("/api/v1/auth/profile", headers=_auth(HOME_UID)).json()
    assert body["server_admin"] is False
    assert body["is_admin"] is True and body["parent_id"] is None and body["is_sub"] is False

    body_root = c.get("/api/v1/auth/profile", headers=_auth(ROOT_UID)).json()
    assert body_root["server_admin"] is True and body_root["is_admin"] is True

    body_sub = c.get("/api/v1/auth/profile", headers=_auth(SUB_UID)).json()
    assert body_sub["server_admin"] is False
    assert body_sub["is_admin"] is False and body_sub["parent_id"] == ROOT_UID and body_sub["is_sub"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# M2：渠道绑定跨家庭占用
# ═══════════════════════════════════════════════════════════════════════════════

def test_渠道绑定_跨家庭占用409_本家庭占用400(a_db):
    from app.api.plugins import _validate_channel_binding

    # 别的家庭根（4）已绑定渠道 → 本家庭不得静默覆盖 → 409（复用既有 channel_bind_physical_taken）
    _seed_plugin_config(a_db, "chan_demo", json.dumps({"allowed_character_ids": str(CHAR_OTHER)}))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(_validate_channel_binding(
            ROOT_UID, "chan_demo", {"allowed_character_ids": [CHAR_ROOT_A]}, "zh"))
    assert ei.value.status_code == 409

    # 本家庭内其它角色占用 → 400（原语义不变）
    _seed_plugin_config(a_db, "chan_demo", json.dumps({"allowed_character_ids": str(CHAR_ROOT_B)}))
    with pytest.raises(HTTPException) as ej:
        asyncio.run(_validate_channel_binding(
            ROOT_UID, "chan_demo", {"allowed_character_ids": [CHAR_ROOT_A]}, "zh"))
    assert ej.value.status_code == 400

    # 未绑定 → 放行并归一化为逗号串（原语义不变）
    _seed_plugin_config(a_db, "chan_demo", "{}")
    cfg = {"allowed_character_ids": [CHAR_ROOT_A]}
    asyncio.run(_validate_channel_binding(ROOT_UID, "chan_demo", cfg, "zh"))
    assert cfg["allowed_character_ids"] == str(CHAR_ROOT_A)

    # 所选角色本身属别的家庭 → 403（既有行为不变）
    with pytest.raises(HTTPException) as ek:
        asyncio.run(_validate_channel_binding(
            ROOT_UID, "chan_demo", {"allowed_character_ids": [CHAR_OTHER]}, "zh"))
    assert ek.value.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# M1：迁移 / 哨兵 / 单头
# ═══════════════════════════════════════════════════════════════════════════════

def _load_a2_migration():
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / "a8b9c0d1e2f3_add_plugins_owner_columns.py")
    spec = importlib.util.spec_from_file_location("mig_a2_owner", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_加两列_幂等_downgrade可逆(tmp_path):
    """迁移在临时库建出 owner_user_id / owner_tenant_id；重复 upgrade 幂等；downgrade 可逆。"""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_a2_migration()
    assert mig.down_revision == "b5c6d7e8f9a0"  # A2 M1 必须挂在当前 head 上，保持单链

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'a2_mig.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE plugins (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(100))"
        ))
        # 存量行：升级后 owner 必须为 NULL（存量=服务级）
        conn.execute(sa.text("INSERT INTO plugins (id, name) VALUES (1, 'builtin_x')"))

        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
            mig.upgrade()  # 幂等：列已存在 → 跳过
        cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(plugins)"))}
        assert {"owner_user_id", "owner_tenant_id"} <= cols
        row = conn.execute(sa.text(
            "SELECT owner_user_id, owner_tenant_id FROM plugins WHERE id=1")).fetchone()
        assert tuple(row) == (None, None)

        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
            mig.downgrade()  # 幂等：列已删 → 跳过
        cols_after = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(plugins)"))}
        assert not ({"owner_user_id", "owner_tenant_id"} & cols_after)
    engine.dispose()


def test_migration_alembic_upgrade_downgrade_再upgrade(tmp_path, monkeypatch):
    """真实 alembic 命令链自检：stamp 到前一 head → upgrade head（跑本迁移）→ downgrade → 再 upgrade。"""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.config import settings

    backend = Path(__file__).resolve().parents[1]
    db_file = tmp_path / "a2_chain.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_file.as_posix()}")

    # 造一个「有 plugins 表、无 owner 列」的库，并把版本记到前一 head
    engine = sa.create_engine(f"sqlite:///{db_file.as_posix()}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE plugins (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(100))"
        ))
    engine.dispose()

    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    # A2 M6（2026-09-20）新增迁移后 head 前移：不硬编码，跟随当前单头（同
    # test_fk_ondelete_active_parents 口径），避免每次新增迁移都要改本用例。
    _head = ScriptDirectory.from_config(cfg).get_current_head()
    command.stamp(cfg, "b5c6d7e8f9a0")
    command.upgrade(cfg, "head")

    engine = sa.create_engine(f"sqlite:///{db_file.as_posix()}")
    try:
        with engine.connect() as conn:
            cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(plugins)"))}
            ver = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        assert {"owner_user_id", "owner_tenant_id"} <= cols
        assert ver == _head

        command.downgrade(cfg, "b5c6d7e8f9a0")
        with engine.connect() as conn:
            cols2 = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(plugins)"))}
        assert not ({"owner_user_id", "owner_tenant_id"} & cols2)

        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            cols3 = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(plugins)"))}
            ver3 = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        assert {"owner_user_id", "owner_tenant_id"} <= cols3
        assert ver3 == _head
    finally:
        engine.dispose()


def test_migration_哨兵登记与单链头():
    """老库缺列必须判「落后」：哨兵含 (plugins, owner_user_id)；迁移链保持单头且在链上。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.db.migrate import _CURRENT_SCHEMA_SENTINELS

    assert ("plugins", "owner_user_id") in _CURRENT_SCHEMA_SENTINELS

    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, heads  # 单头：新增迁移不得分叉
    revs = [r.revision for r in script.walk_revisions(base="base", head=heads[0])]
    assert "a8b9c0d1e2f3" in revs


# ═══════════════════════════════════════════════════════════════════════════════
# M1：归属写入（安装路径 / builtin 同步 / 幂等）
# ═══════════════════════════════════════════════════════════════════════════════

def test_本地zip安装落owner_重复安装不覆盖(a_db, plugin_env):
    """本地 zip 安装：owner_user_id=调用者、owner_tenant_id=其家庭根；重复安装不覆盖已有 owner。"""
    c = _client()
    name = "a2_owner_local"
    zip_bytes = _make_zip(name)

    r = c.post("/api/v1/plugins/install", headers=_auth(ROOT_UID),
               files={"file": ("a2.zip", zip_bytes, "application/zip")})
    assert r.status_code == 200, r.text
    assert _plugin_row(a_db, name) == (ROOT_UID, ROOT_UID, "local")

    # 同账号重复安装 → 值不变（幂等）
    r = c.post("/api/v1/plugins/install", headers=_auth(ROOT_UID),
               files={"file": ("a2.zip", zip_bytes, "application/zip")})
    assert r.status_code == 200, r.text
    assert _plugin_row(a_db, name) == (ROOT_UID, ROOT_UID, "local")

    # 另一个 server_admin 安装同一插件 → 已有 owner 不被覆盖（也不得被抹成 NULL）
    r = c.post("/api/v1/plugins/install", headers=_auth(ADMIN2_UID),
               files={"file": ("a2.zip", zip_bytes, "application/zip")})
    assert r.status_code == 200, r.text
    assert _plugin_row(a_db, name) == (ROOT_UID, ROOT_UID, "local")


def test_市场安装落owner_内置与远程(a_db, plugin_env, market_env):
    """市场安装（内置示例 + 远程 zip）：两条路径都把调用者落成 owner。"""
    c = _client()
    h = _auth(HOME_UID)  # 先确认非 server_admin 装不了（市场安装同一闸）
    assert c.post("/api/v1/marketplace/http_echo/install", headers=h, json={}).status_code == 403

    hr = _auth(ROOT_UID)
    r = c.post("/api/v1/marketplace/http_echo/install", headers=hr, json={})
    assert r.status_code == 200, r.text
    assert _plugin_row(a_db, "http_echo") == (ROOT_UID, ROOT_UID, "builtin")

    r = c.post(f"/api/v1/marketplace/{REMOTE_PLUGIN_NAME}/install", headers=hr, json={})
    assert r.status_code == 200, r.text
    assert _plugin_row(a_db, REMOTE_PLUGIN_NAME) == (ROOT_UID, ROOT_UID, "remote")


def test_builtin同步保持NULL_重复同步不覆盖已有owner(a_db, monkeypatch, tmp_path):
    """sync_plugins_db：新建 builtin 行 owner=NULL（内置=服务级）；已有 owner 不许被同步抹掉。"""
    examples = tmp_path / "examples"
    plug = examples / "builtin_sync_demo"
    plug.mkdir(parents=True)
    (plug / "manifest.json").write_text(json.dumps({
        "name": "builtin_sync_demo", "version": "1.0.0", "description": "配置型示例",
        "type": "prompt",
        "config": {"prompt": {"trigger": ["hi"], "systemPrompt": "你是测试插件"}},
    }, ensure_ascii=False), encoding="utf-8")
    user_dir = tmp_path / "userplugins"
    user_dir.mkdir()
    monkeypatch.setattr(registry, "EXAMPLE_DIR", examples)
    monkeypatch.setattr(registry, "USER_DIR", user_dir)

    asyncio.run(registry.sync_plugins_db())
    assert _plugin_row(a_db, "builtin_sync_demo") == (None, None, "builtin")

    # 某个账号装了它 → 落 owner；再同步一次不得覆盖成 NULL
    asyncio.run(registry.record_install_provenance(
        "builtin_sync_demo", source="builtin", owner_user_id=HOME_UID))
    assert _plugin_row(a_db, "builtin_sync_demo") == (HOME_UID, HOME_UID, "builtin")

    asyncio.run(registry.sync_plugins_db())
    assert _plugin_row(a_db, "builtin_sync_demo") == (HOME_UID, HOME_UID, "builtin")


def test_record_install_provenance_owner语义(a_db):
    """纯写入口径：只在 NULL 时落 owner；不传 owner_user_id 保持原值；解析家庭根失败留 NULL。"""
    name = "a2_prov"
    asyncio.run(registry.record_install_provenance(
        name, source="local", owner_user_id=HOME_UID))
    assert _plugin_row(a_db, name)[:2] == (HOME_UID, HOME_UID)

    # 已有 owner → 再记录不覆盖（含 owner_user_id 为 None 的同步调用）
    asyncio.run(registry.record_install_provenance(name, source="local"))
    asyncio.run(registry.record_install_provenance(name, source="local", owner_user_id=ADMIN2_UID))
    assert _plugin_row(a_db, name)[:2] == (HOME_UID, HOME_UID)


# ═══════════════════════════════════════════════════════════════════════════════
# M0-4：hook ctx 补 caller
# ═══════════════════════════════════════════════════════════════════════════════

def test_after_generate_ctx含user_id与character_id(monkeypatch):
    from app.agent import nodes

    captured: list[tuple[str, dict]] = []

    async def _fake_run_hook(hook, ctx, timeout=None, **kw):
        # A2 M4：run_hook 新增 keyword-only 调用者/调用点形参（user_id/callsite），打桩一并吞掉
        captured.append((hook, dict(ctx)))

    async def _fake_completion(**kw):
        return "正文回复"

    async def _fake_cfg(user_id):
        return None

    monkeypatch.setattr(nodes, "chat_completion", _fake_completion)
    monkeypatch.setattr("app.agent.llm_client.get_user_llm_config", _fake_cfg)
    monkeypatch.setattr(nodes, "_has_after_generate_hook", lambda: False)
    monkeypatch.setattr(registry, "run_hook", _fake_run_hook)

    state = {
        "context_messages": [{"role": "user", "content": "hi"}],
        "emotional_state": "", "temperature": 0.8, "reasoning_level": 0,
        "user_id": ROOT_UID, "character_id": CHAR_ROOT_A,
        "ai_response": "", "user_message": "hi", "session_id": 1,
        "new_memories": [], "skip_memory_save": True,
        "character_name": "R-A", "user_name": "根",
    }
    asyncio.run(nodes.generate_response(state))

    ag = [ctx for hook, ctx in captured if hook == "after_generate"]
    assert len(ag) == 1
    assert ag[0]["user_id"] == ROOT_UID
    assert ag[0]["character_id"] == CHAR_ROOT_A
    assert ag[0]["reply_text"] == "正文回复"


def test_memory_search_hook_ctx含user_id(a_db, monkeypatch):
    from app.memory import retrieve as retrieve_mod
    from app.memory import service as memory_service

    captured: list[dict] = []

    async def _fake_hook_collect(hook, ctx, timeout=None, **kw):
        # A2 M4：run_hook_collect 新增 keyword-only 调用者/调用点形参（callsite 等），
        # 打桩需一并吞掉，否则 TypeError 会被调用点异常隔离吞成空结果。
        captured.append(dict(ctx))
        return []

    async def _empty(*a, **kw):
        return []

    monkeypatch.setattr(registry, "run_hook_collect", _fake_hook_collect)
    monkeypatch.setattr(memory_service, "vector_search", _empty)
    monkeypatch.setattr(memory_service, "bm25_search", _empty)
    monkeypatch.setattr(memory_service, "text_embedding", _empty)

    out = asyncio.run(retrieve_mod.search_memories(
        character_id=CHAR_HOME, query="测试调用者透传", limit=3, user_id=HOME_UID))
    assert out == []
    assert len(captured) == 1
    assert captured[0]["user_id"] == HOME_UID
    assert captured[0]["character_id"] == CHAR_HOME

    # 既有调用（不传 user_id）不破：ctx 里该键为 None
    captured.clear()
    asyncio.run(retrieve_mod.search_memories(character_id=CHAR_HOME, query="旧调用", limit=1))
    assert captured[0]["user_id"] is None
