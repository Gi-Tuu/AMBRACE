# -*- coding: utf-8 -*-
"""A2 批次 B（M5 卸载数据生命周期 + M6 同意按租户）测试（2026-09-20）。

覆盖（与派单 §四 一一对应）：

- **M5**：``DELETE /api/v1/plugins/{name}`` 响应含 ``stores_cleared`` / ``accounts_affected`` /
  ``plugin_tables_kept``；KV 清理范围与改前一致（**全账号**按 plugin_name 删，不按 user_id 收窄）；
  ``plugins`` 行删除；**插件自有表不 DROP**（数据保留）；同事务落一条管理审计 ``plugin.uninstall``。
- **M6**：``consent_state`` / ``get_tenant_consented_permissions`` 按租户判定（A 租户已同意、
  B 租户未同意 → B 仍需同意）；内置插件（``owner_tenant_id IS NULL``）回落服务级同意、不要求重新同意；
  本地 zip 安装路径把同意写进 ``plugin_consents``（tenant_id = 安装者**家庭根**，非调用者自身）；
  同插件不同家庭各自同意一次；迁移建表 + downgrade 可逆 + 单头 + 哨兵命中；回填一次性幂等
  （重复跑 0 变更；``owner_tenant_id IS NULL`` 的存量行不写新表）。
- **清理脚本**：``scripts/plugins/prune_orphan_plugin_tables.py`` 默认 dry-run 只列孤儿表，
  ``--apply`` 缺 ``--yes`` 拒绝；临时库上 apply 后重跑 0 孤儿（幂等）。

隔离：全程私有临时库（pytest ``tmp_path``）+ 把 app.* 的 ``async_session_factory`` 换成私有工厂
（同 ``test_admin_console_p2._patch_session_factories`` 口径）；``registry.USER_DIR`` 指向 tmp_path，
插件加载/同步打桩 —— **不连生产库、不写 ``backend/data``**。
"""
import asyncio
import importlib.util
import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import plugins as plugins_api
from app.auth.config import create_token
from app.plugins import registry

# 账号：1/2 = 两个独立家庭根（均 server_admin）；3 = 1 的子账号（server_admin，
# 用于验证「同意租户 = 家庭根（1）≠ 调用者（3）」）。
ROOT_A, ROOT_B, SUB_A = 1, 2, 3


# ---------------------------------------------------------------- 私有临时库

def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库工厂接到所有 app.* 模块的 ``async_session_factory`` 名上（含早绑定引用）。"""
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
def m_db(monkeypatch, tmp_path):
    """私有临时 SQLite：两个家庭根 + 一个子账号。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm56.db'}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册主 metadata（含 plugin_consents）
        from app.models.base import Base
        from app.models.user import User

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([
                User(id=ROOT_A, username="root_a", nickname="家庭A", is_admin=True, server_admin=True),
                User(id=ROOT_B, username="root_b", nickname="家庭B", is_admin=True, server_admin=True),
                User(id=SUB_A, username="sub_a", nickname="A的子账号", is_admin=False,
                     parent_id=ROOT_A, server_admin=True),
            ])
            await db.commit()

    asyncio.run(_init())
    _patch_session_factories(monkeypatch, factory)

    async def _tables():
        from sqlalchemy import text as _t
        async with engine.connect() as conn:
            rows = (await conn.execute(
                _t("SELECT name FROM sqlite_master WHERE type='table'"))).all()
        return {r[0] for r in rows}

    assert "plugin_consents" in asyncio.run(_tables())  # create_all 路径建出新表
    yield factory
    asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _clear_registry_caches():
    """清插件注册缓存（跨用例残留会让同意/归属判定串味）。"""
    for d in (registry._loaded, registry._enabled, registry._db_config, registry._db_prov):
        d.clear()
    yield
    for d in (registry._loaded, registry._enabled, registry._db_config, registry._db_prov):
        d.clear()


@pytest.fixture()
def plugin_env(monkeypatch, tmp_path):
    """插件安装/卸载隔离环境：USER_DIR → tmp_path；同步/加载打桩，绝不写 backend/data。"""
    user_dir = tmp_path / "userplugins"
    user_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(registry, "USER_DIR", user_dir)

    async def _sync_noop():
        return None

    monkeypatch.setattr(registry, "sync_plugins_db", _sync_noop)
    monkeypatch.setattr(
        registry, "get_plugin",
        lambda name: {"name": name, "type": "http", "enabled": False, "config": {}},
    )

    def _resolve(name):
        p = user_dir / str(name)
        p.mkdir(parents=True, exist_ok=True)
        return p

    monkeypatch.setattr(registry, "resolve_plugin_dir", _resolve)
    yield user_dir


# ---------------------------------------------------------------- helpers

def _client() -> TestClient:
    app = FastAPI()
    app.include_router(plugins_api.router)
    return TestClient(app, raise_server_exceptions=False)


def _auth(uid: int) -> dict:
    """真实 JWT（conftest 固定 AUTH_SECRET_KEY）——走真实鉴权 + 真实 server_admin 判定。"""
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _make_zip(name: str, manifest_extra: dict | None = None) -> bytes:
    manifest = {"name": name, "version": "1.0.0", "description": "m56 测试包", "type": "http"}
    manifest.update(manifest_extra or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("main.py", "x = 1\n")
    return buf.getvalue()


def _exec(factory, sql: str) -> None:
    async def _go():
        from sqlalchemy import text as _t
        async with factory() as db:
            await db.execute(_t(sql))
            await db.commit()
    asyncio.run(_go())


def _scalar(factory, sql: str):
    async def _go():
        from sqlalchemy import text as _t
        async with factory() as db:
            return (await db.execute(_t(sql))).scalar()
    return asyncio.run(_go())


def _seed_plugin(factory, name: str, **kw) -> None:
    from app.models.plugin import Plugin

    async def _go():
        async with factory() as db:
            db.add(Plugin(name=name, version="1.0.0", description="", **kw))
            await db.commit()
    asyncio.run(_go())


def _seed_consent(factory, name: str, tenant_id: int, perms_json: str, consented_by=None) -> None:
    from app.models.plugin import PluginConsent

    async def _go():
        async with factory() as db:
            db.add(PluginConsent(plugin_name=name, tenant_id=tenant_id,
                                 permissions_json=perms_json, consented_by=consented_by))
            await db.commit()
    asyncio.run(_go())


def _plugin_row(factory, name: str):
    from app.models.plugin import Plugin

    async def _go():
        async with factory() as db:
            r = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
            if r is None:
                return None
            return {"owner_user_id": r.owner_user_id, "owner_tenant_id": r.owner_tenant_id,
                    "consented_permissions": r.consented_permissions, "enabled": r.enabled}
    return asyncio.run(_go())


def _consent_rows(factory, name: str):
    """返回 [(tenant_id, 权限列表, consented_by), ...]（按 tenant_id 排序，权限已排序）。"""
    from app.models.plugin import PluginConsent

    async def _go():
        async with factory() as db:
            rows = (await db.execute(
                select(PluginConsent).where(PluginConsent.plugin_name == name)
            )).scalars().all()
            return sorted((r.tenant_id, tuple(sorted(json.loads(r.permissions_json or "[]"))),
                           r.consented_by) for r in rows)
    return asyncio.run(_go())


def _audit_rows(factory, action: str):
    from app.models.admin import AdminAuditLog

    async def _go():
        async with factory() as db:
            rows = (await db.execute(
                select(AdminAuditLog).where(AdminAuditLog.action == action)
            )).scalars().all()
            return [(r.id, r.actor_user_id, r.target,
                     json.loads(r.before_json or "null"), json.loads(r.after_json or "null"))
                    for r in rows]
    return asyncio.run(_go())


def _issue_install(c, uid: int, name: str, *, consent: bool, permissions=None):
    zip_bytes = _make_zip(name, {"permissions": list(permissions or [])})
    data = {"consent": "true" if consent else "false",
            "permissions": json.dumps(list(permissions or []))}
    return c.post("/api/v1/plugins/install", headers=_auth(uid),
                  files={"file": ("p.zip", zip_bytes, "application/zip")}, data=data)


# ═══════════════════════════════════════════════════════════════════════════════
# M5：卸载数据生命周期
# ═══════════════════════════════════════════════════════════════════════════════

def test_m5_卸载回显影响面_全账号清KV_审计一条_自有表保留(m_db, plugin_env):
    factory = m_db
    name = "m5_plug"
    # 物理插件自有表（模拟 douyin_* 类业务表）：卸载后必须保留
    _exec(factory, "CREATE TABLE m5_plug_data (id INTEGER PRIMARY KEY, v TEXT)")
    _exec(factory, "INSERT INTO m5_plug_data (v) VALUES ('keep-me')")
    _seed_plugin(factory, name, enabled=True, owner_user_id=ROOT_A, owner_tenant_id=ROOT_A)

    async def _seed_kv():
        from app.models.plugin import PluginStore
        async with factory() as db:
            db.add_all([
                PluginStore(plugin_name=name, user_id=ROOT_A, key="a", value_json="1"),
                PluginStore(plugin_name=name, user_id=ROOT_B, key="b", value_json="1"),
                PluginStore(plugin_name=name, user_id=SUB_A, key="c", value_json="1"),
                PluginStore(plugin_name="other_plug", user_id=ROOT_A, key="d", value_json="1"),
            ])
            await db.commit()
    asyncio.run(_seed_kv())

    r = _client().delete(f"/api/v1/plugins/{name}", headers=_auth(ROOT_A))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["uninstalled"] is True and body["name"] == name
    # M5 回显字段（行为变更点只在响应体）
    assert body["stores_cleared"] == 3
    assert body["accounts_affected"] == 3  # distinct user_id：1/2/3
    assert body["plugin_tables_kept"] is True
    assert "不删" in body["plugin_tables_note"]
    # KV 全账号清空（不受 user_id 收窄）；别的插件不受影响
    assert _scalar(factory, f"SELECT COUNT(*) FROM plugin_stores WHERE plugin_name='{name}'") == 0
    assert _scalar(factory, "SELECT COUNT(*) FROM plugin_stores WHERE plugin_name='other_plug'") == 1
    # plugins 行删除（含来源/同意/归属元数据）
    assert _scalar(factory, f"SELECT COUNT(*) FROM plugins WHERE name='{name}'") == 0
    # 插件自有表不 DROP：表与数据都保留
    assert _scalar(factory, "SELECT COUNT(*) FROM m5_plug_data") == 1
    # 目录已删
    assert not (plugin_env / name).exists()
    # 审计恰一条，before/after 口径齐
    rows = _audit_rows(factory, "plugin.uninstall")
    assert len(rows) == 1
    _id, actor, target, before, after = rows[0]
    assert actor == ROOT_A and target == name
    assert before["name"] == name and before["enabled"] is True
    assert before["owner_user_id"] == ROOT_A
    assert before["stores_cleared"] == 3 and before["accounts_affected"] == 3
    assert after["stores_cleared"] == 3 and after["accounts_affected"] == 3
    assert after["owner_user_id"] is None and after["enabled"] is False


def test_m5_卸载内置插件拒绝_响应体不变(m_db, plugin_env, monkeypatch):
    """内置插件（仅 EXAMPLE_DIR）不可卸载 → 400；不产生审计/KV 变更。"""
    builtin_dir = plugin_env.parent / "example_builtin"
    builtin_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(registry, "resolve_plugin_dir", lambda name: builtin_dir)
    r = _client().delete("/api/v1/plugins/builtin_x", headers=_auth(ROOT_A))
    assert r.status_code == 400
    assert _audit_rows(m_db, "plugin.uninstall") == []


# ═══════════════════════════════════════════════════════════════════════════════
# M6：同意按租户（判定 / 回落）
# ═══════════════════════════════════════════════════════════════════════════════

def test_m6_按租户判定_A已同意B仍需同意(m_db):
    factory = m_db
    _seed_plugin(factory, "m6_plug", owner_user_id=ROOT_A, owner_tenant_id=ROOT_A,
                 consented_permissions='["a"]')
    _seed_consent(factory, "m6_plug", ROOT_A, '["a"]', consented_by=ROOT_A)

    perms_a = asyncio.run(registry.get_tenant_consented_permissions("m6_plug", ROOT_A))
    perms_b = asyncio.run(registry.get_tenant_consented_permissions("m6_plug", ROOT_B))
    assert perms_a == ["a"]
    assert perms_b == []  # 已归户插件：别的租户同意不生效，也不回落服务级
    assert registry.consent_state(["a"], perms_a) == ("auto", [])
    assert registry.consent_state(["a"], perms_b) == ("required", ["a"])


def test_m6_内置插件回落服务级_不要求重新同意(m_db):
    factory = m_db
    _seed_plugin(factory, "m6_builtin", owner_user_id=None, owner_tenant_id=None,
                 consented_permissions='["proactive:read"]')
    for tid in (ROOT_A, ROOT_B, None):
        perms = asyncio.run(registry.get_tenant_consented_permissions("m6_builtin", tid))
        assert perms == ["proactive:read"]  # 服务级回落生效
        assert registry.consent_state(["proactive:read"], perms) == ("auto", [])
    # 未知租户 / 任意租户都不再弹确认
    asyncio.run(registry.require_plugin_consent(
        "m6_builtin", ["proactive:read"], "zh", tenant_id=ROOT_B))
    # 未新增任何租户级同意行（服务级回落不改数据）
    assert _consent_rows(factory, "m6_builtin") == []


def test_m6_升级新增权限_按租户各自同意(m_db):
    """升级（同租户）：已同意集覆盖旧权限 → auto；新增权限 → required（needed 为完整清单）。"""
    factory = m_db
    _seed_plugin(factory, "m6_up", owner_user_id=ROOT_A, owner_tenant_id=ROOT_A,
                 consented_permissions='["a"]')
    _seed_consent(factory, "m6_up", ROOT_A, '["a"]')
    perms_a = asyncio.run(registry.get_tenant_consented_permissions("m6_up", ROOT_A))
    assert registry.consent_state(["a"], perms_a) == ("auto", [])
    assert registry.consent_state(["a", "b"], perms_a) == ("required", ["a", "b"])


# ═══════════════════════════════════════════════════════════════════════════════
# M6：安装路径写入新表（tenant = 家庭根）
# ═══════════════════════════════════════════════════════════════════════════════

def test_m6_安装写新表_tenant取家庭根_不同家庭各自同意(m_db, plugin_env):
    c = _client()
    name = "m6_inst"
    # 子账号 3（家庭根 1，server_admin）安装 → tenant_id 必须是家庭根 1，而非调用者 3
    r = _issue_install(c, SUB_A, name, consent=True, permissions=["write_memory"])
    assert r.status_code == 200, r.text
    assert _consent_rows(m_db, name) == [(ROOT_A, ("write_memory",), SUB_A)]
    # 兼容列仍写（旧读点）
    assert _plugin_row(m_db, name)["consented_permissions"] == '["write_memory"]'

    # 另一个家庭（2）再装同一插件（升级声明新增权限）：租户 2 未同意 → 400
    r2 = _issue_install(c, ROOT_B, name, consent=False,
                        permissions=["write_memory", "send_message"])
    assert r2.status_code == 400, r2.text
    assert _consent_rows(m_db, name) == [(ROOT_A, ("write_memory",), SUB_A)]  # 未落新行

    # 携带同意 → 200，并落 tenant=2 的独立行；家庭 1 的同意集不被家庭 2 的升级权限污染
    r3 = _issue_install(c, ROOT_B, name, consent=True,
                        permissions=["write_memory", "send_message"])
    assert r3.status_code == 200, r3.text
    assert _consent_rows(m_db, name) == [
        (ROOT_A, ("write_memory",), SUB_A),
        (ROOT_B, ("send_message", "write_memory"), ROOT_B),
    ]


def test_m6_重复安装同租户_不重复弹确认(m_db, plugin_env):
    c = _client()
    name = "m6_repeat"
    assert _issue_install(c, ROOT_A, name, consent=True,
                          permissions=["write_memory"]).status_code == 200
    # 同租户再装：已同意集覆盖 → auto（不带 consent 也放行）
    assert _issue_install(c, ROOT_A, name, consent=False,
                          permissions=["write_memory"]).status_code == 200
    assert _consent_rows(m_db, name) == [(ROOT_A, ("write_memory",), ROOT_A)]


def test_m6_record_install_provenance_按安装者家庭根落同意(m_db):
    """市场安装既有调用点（未透传调用者租户）：``record_install_provenance`` 按安装者家庭根
    补写 ``plugin_consents``，且不改写插件旧 owner（同意租户 = 安装者，而非旧 owner）。"""
    factory = m_db
    _seed_plugin(factory, "m6_market", owner_user_id=ROOT_A, owner_tenant_id=ROOT_A,
                 consented_permissions='["write_memory"]')
    _seed_consent(factory, "m6_market", ROOT_A, '["write_memory"]', consented_by=ROOT_A)

    asyncio.run(registry.record_install_provenance(
        "m6_market", source="remote", owner_user_id=ROOT_B))

    assert _consent_rows(factory, "m6_market") == [
        (ROOT_A, ("write_memory",), ROOT_A),
        (ROOT_B, ("write_memory",), ROOT_B),
    ]
    assert _plugin_row(factory, "m6_market")["owner_tenant_id"] == ROOT_A  # 旧 owner 不被覆盖


# ═══════════════════════════════════════════════════════════════════════════════
# M6：存量一致性回填（一次性、幂等）
# ═══════════════════════════════════════════════════════════════════════════════

def test_m6_回填一次性幂等_owner为NULL不写(m_db):
    factory = m_db
    _seed_plugin(factory, "bf_owned", owner_user_id=ROOT_B, owner_tenant_id=ROOT_B,
                 consented_permissions='["x"]')
    _seed_plugin(factory, "bf_builtin", owner_user_id=None, owner_tenant_id=None,
                 consented_permissions='["y"]')
    _seed_plugin(factory, "bf_empty", owner_user_id=ROOT_A, owner_tenant_id=ROOT_A,
                 consented_permissions="[]")

    assert asyncio.run(registry.backfill_plugin_consents_once()) == 1  # 只 bf_owned
    assert _consent_rows(factory, "bf_owned") == [(ROOT_B, ("x",), ROOT_B)]
    assert _consent_rows(factory, "bf_builtin") == []  # NULL 归属 = 服务级，不写新表
    assert _consent_rows(factory, "bf_empty") == []

    # 幂等：新表非空 → 再跑 0 变更（即便新插入可回填行）
    _seed_plugin(factory, "bf_owned2", owner_user_id=ROOT_A, owner_tenant_id=ROOT_A,
                 consented_permissions='["z"]')
    before = _consent_rows(factory, "bf_owned") + _consent_rows(factory, "bf_owned2")
    assert asyncio.run(registry.backfill_plugin_consents_once()) == 0
    after = _consent_rows(factory, "bf_owned") + _consent_rows(factory, "bf_owned2")
    assert before == after


# ═══════════════════════════════════════════════════════════════════════════════
# M6：迁移（建表 / 幂等 / downgrade 可逆 / 单头 / 哨兵）
# ═══════════════════════════════════════════════════════════════════════════════

def _load_consent_migration():
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / "b9c0d1e2f3a4_add_plugin_consents.py")
    spec = importlib.util.spec_from_file_location("mig_a2_consents", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_建表_幂等_downgrade可逆(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_consent_migration()
    assert mig.revision == "b9c0d1e2f3a4"
    assert mig.down_revision == "a8b9c0d1e2f3"  # 必须挂当前 head，保持单链

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm56_mig.db'}")
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
            mig.upgrade()  # 幂等：表已存在 → 跳过
        cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(plugin_consents)"))}
        assert {"plugin_name", "tenant_id", "permissions_json", "consented_at",
                "consented_by"} <= cols
        # 联合主键 (plugin_name, tenant_id)
        pk = [(r[1], r[5]) for r in conn.execute(sa.text("PRAGMA table_info(plugin_consents)"))
              if r[5] > 0]
        assert pk == [("plugin_name", 1), ("tenant_id", 2)]

        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
            mig.downgrade()  # 幂等：表已删 → 跳过
        tables = {r[0] for r in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "plugin_consents" not in tables
    engine.dispose()


def test_migration_alembic_upgrade_downgrade_再upgrade(tmp_path, monkeypatch):
    """真实 alembic 命令链：stamp 到前一 head → upgrade head（跑本迁移）→ downgrade → 再 upgrade。"""
    from alembic import command
    from alembic.config import Config

    from app.config import settings

    backend = Path(__file__).resolve().parents[1]
    db_file = tmp_path / "m56_chain.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_file.as_posix()}")

    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    command.stamp(cfg, "a8b9c0d1e2f3")
    command.upgrade(cfg, "head")

    engine = sa.create_engine(f"sqlite:///{db_file.as_posix()}")
    try:
        with engine.connect() as conn:
            tables = {r[0] for r in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='table'"))}
            ver = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        assert "plugin_consents" in tables
        # 2026-09-20：链头随新迁移前移，不再硬编码 b9c0d1e2f3a4（P3-5 的 c0d1e2f3a4b5 一加就红）
        from alembic.script import ScriptDirectory
        assert ver == ScriptDirectory.from_config(cfg).get_current_head()  # 单头且落在当前链头

        command.downgrade(cfg, "a8b9c0d1e2f3")
        with engine.connect() as conn:
            tables2 = {r[0] for r in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "plugin_consents" not in tables2

        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            tables3 = {r[0] for r in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='table'"))}
            ver3 = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        assert "plugin_consents" in tables3 and ver3 == ScriptDirectory.from_config(cfg).get_current_head()
    finally:
        engine.dispose()


def test_migration_哨兵命中与单链头():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.db.migrate import _CURRENT_SCHEMA_SENTINELS

    assert ("plugin_consents", "plugin_name") in _CURRENT_SCHEMA_SENTINELS

    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, heads  # 单头（新增迁移不得分叉；链头随新迁移前移，勿硬编码）
    revs = [r.revision for r in script.walk_revisions(base="base", head=heads[0])]
    assert "a8b9c0d1e2f3" in revs and "b9c0d1e2f3a4" in revs


# ═══════════════════════════════════════════════════════════════════════════════
# M5：清理脚本（默认 dry-run / --apply 需 --yes / 幂等）
# ═══════════════════════════════════════════════════════════════════════════════

def _load_prune_module():
    path = (Path(__file__).resolve().parents[2] / "scripts" / "plugins"
            / "prune_orphan_plugin_tables.py")
    spec = importlib.util.spec_from_file_location("prune_orphan_plugin_tables", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_prune_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE plugins (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO plugins (name) VALUES ('installed_plug')")
        conn.execute("CREATE TABLE orphan_plug_data (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO orphan_plug_data (v) VALUES ('x')")
        conn.execute("INSERT INTO orphan_plug_data (v) VALUES ('y')")
        conn.execute("CREATE TABLE installed_plug_data (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()


def _make_plugin_dir(base: Path, name: str, table: str) -> None:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    (d / "models.py").write_text(f'__tablename__ = "{table}"\n', encoding="utf-8")


def test_prune_脚本_默认dryrun列出孤儿_apply需yes_幂等(tmp_path, monkeypatch, capsys):
    mod = _load_prune_module()
    db = tmp_path / "prune.db"
    _make_prune_db(db)
    _make_plugin_dir(tmp_path, "orphan_plug", "orphan_plug_data")
    _make_plugin_dir(tmp_path, "installed_plug", "installed_plug_data")

    items = mod.collect(str(db), [str(tmp_path)])
    by_table = {it["table"]: it for it in items}
    assert by_table["orphan_plug_data"]["status"] == "orphan"
    assert by_table["orphan_plug_data"]["rows"] == 2
    assert by_table["installed_plug_data"]["status"] == "kept"

    # 默认 dry-run：只打印，不 DROP
    monkeypatch.setattr(sys, "argv", ["x", "--db", str(db), "--plugin-dir", str(tmp_path)])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "orphan_plug_data" in out and "dry-run" in out
    assert "orphan_plug_data" in _table_names(db)

    # --apply 缺 --yes → 拒绝（exit 2），表仍在
    monkeypatch.setattr(sys, "argv", ["x", "--db", str(db), "--apply"])
    assert mod.main() == 2
    assert "orphan_plug_data" in _table_names(db)

    # --apply --yes → DROP 孤儿；重跑 collect 0 孤儿（幂等）
    monkeypatch.setattr(sys, "argv",
                        ["x", "--db", str(db), "--plugin-dir", str(tmp_path), "--apply", "--yes"])
    assert mod.main() == 0
    assert "orphan_plug_data" not in _table_names(db)
    assert "installed_plug_data" in _table_names(db)  # 已安装插件的表不动
    assert [it for it in mod.collect(str(db), [str(tmp_path)]) if it["status"] == "orphan"] == []


def _table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
