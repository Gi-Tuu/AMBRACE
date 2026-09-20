# -*- coding: utf-8 -*-
"""A8 · LLM 额度按账号（2026-09-20）：服务层 + 控制台端点 + 迁移自检。

覆盖派单 §3：
- 服务层 llm_quota：resolve_limit 优先级（覆盖 > 全局 > unset）、set_user_limit（写/覆盖/None 清除）、
  set_global_limit（upsert id=1）；
- 端点：GET /server/accounts 三个新字段；PUT /server/accounts/{id}/llm-limit（成功 / 400 / 404 /
  清除覆盖）；GET·PUT /server/llm-limit；非 server_admin → 403、未登录 → 401；每次写落一条审计；
- 迁移：b5c6d7e8f9a0 单头 + 临时库 upgrade/downgrade/再 upgrade + 幂等；
  migrate 哨兵缺表判「落后」（防老库被 stamp 到 head 却永久缺表）。

口径：临时库一律 pytest tmp_path 私有 SQLite 文件（不碰 backend/data），跑完随 tmp 清理；
每个用例前后清 server_admin 进程内缓存（conftest 全局护栏 + 本文件 fixture）。
"""
import asyncio
import importlib.util
from pathlib import Path

import bcrypt
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import admin as admin_api
from app.application import llm_quota
from app.auth.config import create_token
from app.auth.router import router as auth_router
from app.models.agent import LlmUsageLimit, UserLlmLimit

pytestmark = pytest.mark.slow

ROOT_UID, SUB_UID, OTHER_UID = 1, 2, 3  # 1=server_admin 主账号；2=子账号；3=主账号但非 server_admin
MISSING_UID = 999  # 不存在的账号（404 路径）


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _run(coro):
    """同步跑一次协程（服务层函数是 async，本文件不引 pytest-asyncio）。"""
    return asyncio.run(coro)


def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库工厂接到所有 app.* 模块的 ``async_session_factory`` 名上（含早绑定引用）。

    与 test_admin_console_p2._patch_session_factories 同法：app.db.database 是接缝，但
    api.admin / admin_audit_service / llm_quota 等在 import 期就早绑定了该名字，需逐个换掉。
    """
    import sys

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
def quota_db(monkeypatch, tmp_path):
    """私有临时 SQLite：root(server_admin) / sub(子账号) / other(主账号但非 server_admin)。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/quota.db", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        from app.models.user import User

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([
                User(id=ROOT_UID, username="root", nickname="根", is_admin=True,
                     server_admin=True, password_hash=_hash("rootpass123")),
                User(id=SUB_UID, username="sub", nickname="子", is_admin=False,
                     parent_id=ROOT_UID, password_hash=_hash("subpass123")),
                User(id=OTHER_UID, username="other", nickname="别的根", is_admin=True,
                     server_admin=False, password_hash=_hash("rootpass123")),
            ])
            await db.commit()

    asyncio.run(_init())
    _patch_session_factories(monkeypatch, factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_caches():
    from app.application import permission_service as perm

    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    perm._account_state_cache.clear()
    yield
    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    perm._account_state_cache.clear()


def _client() -> TestClient:
    from app.api import system as system_api

    app = FastAPI()
    app.include_router(admin_api.router)
    app.include_router(system_api.router)  # 与控制台同源（system 侧 llm-usage-limit 语义不变）
    app.include_router(auth_router)
    return TestClient(app, raise_server_exceptions=False)


def _auth(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _audit(c: TestClient) -> list:
    r = c.get("/api/v1/admin/server/audit", headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    return r.json()["entries"]


# ═══════════════════════════════════════════════════════════════════════════════
# 服务层：口径（覆盖 > 全局 > unset）
# ═══════════════════════════════════════════════════════════════════════════════

def test_resolve_limit_priority(quota_db):
    """无覆盖+全局 0 → unset；全局 >0 → global；有覆盖 → user（且覆盖 0 也算 user）。"""
    # 1) 全空 → unset（0 = 未设置）
    assert _run(llm_quota.resolve_limit(SUB_UID)) == {
        "total_limit": 0, "source": "unset", "own": None}
    assert _run(llm_quota.get_global_limit()) == 0

    # 2) 设全局 → global（无覆盖的账号回落到它）
    assert _run(llm_quota.set_global_limit(1000, ROOT_UID))["total_limit"] == 1000
    assert _run(llm_quota.resolve_limit(SUB_UID)) == {
        "total_limit": 1000, "source": "global", "own": None}

    # 3) 设账号覆盖 → user 优先于全局
    assert _run(llm_quota.set_user_limit(SUB_UID, 50, ROOT_UID)) == {
        "user_id": SUB_UID, "total_limit": 50, "source": "user", "own": 50}
    assert _run(llm_quota.resolve_limit(SUB_UID))["source"] == "user"
    # 同库其它账号不受影响
    assert _run(llm_quota.resolve_limit(ROOT_UID)) == {
        "total_limit": 1000, "source": "global", "own": None}

    # 4) 覆盖值 0 ≠ 无覆盖：仍判 user（「有无行」区分，不看值）
    assert _run(llm_quota.set_user_limit(SUB_UID, 0, ROOT_UID)) == {
        "user_id": SUB_UID, "total_limit": 0, "source": "user", "own": 0}

    # 5) 全局清 0 + 无覆盖 → unset
    _run(llm_quota.set_user_limit(SUB_UID, None, ROOT_UID))
    _run(llm_quota.set_global_limit(0, ROOT_UID))
    assert _run(llm_quota.resolve_limit(SUB_UID)) == {
        "total_limit": 0, "source": "unset", "own": None}


def test_set_user_limit_write_override_clear(quota_db):
    """set_user_limit：写入 → 覆盖 → None 清除（回落全局）；批量读不串号。"""
    _run(llm_quota.set_global_limit(999, ROOT_UID))
    _run(llm_quota.set_user_limit(SUB_UID, 10, ROOT_UID))
    assert _run(llm_quota.get_user_overrides([SUB_UID])) == {SUB_UID: 10}
    # 覆盖（同一账号二次写）
    _run(llm_quota.set_user_limit(SUB_UID, 20, ROOT_UID))
    assert _run(llm_quota.get_user_overrides([SUB_UID])) == {SUB_UID: 20}
    # 批量读：只返回有覆盖的账号
    assert _run(llm_quota.get_user_overrides([ROOT_UID, SUB_UID, OTHER_UID])) == {SUB_UID: 20}
    # None = 清除覆盖 → 回落全局
    assert _run(llm_quota.set_user_limit(SUB_UID, None, ROOT_UID)) == {
        "user_id": SUB_UID, "total_limit": 999, "source": "global", "own": None}
    assert _run(llm_quota.get_user_overrides([SUB_UID])) == {}


def test_set_global_limit_upserts_single_row(quota_db):
    """set_global_limit：无行则插 id=1，有行则改；库里始终只有一行。"""

    async def _rows():
        async with quota_db() as db:
            return (await db.execute(select(LlmUsageLimit))).scalars().all()

    assert _run(_rows()) == []
    _run(llm_quota.set_global_limit(500, ROOT_UID))
    rows = _run(_rows())
    assert len(rows) == 1 and rows[0].id == 1 and rows[0].total_limit == 500
    _run(llm_quota.set_global_limit(600, ROOT_UID))
    rows = _run(_rows())
    assert len(rows) == 1 and rows[0].total_limit == 600 and rows[0].updated_by == ROOT_UID
    assert _run(llm_quota.get_global_limit()) == 600


def test_set_user_limit_persists_updated_by(quota_db):
    """账号覆盖行落 updated_by（审计可追溯「谁改的」）。"""

    async def _row():
        async with quota_db() as db:
            return (await db.execute(
                select(UserLlmLimit).where(UserLlmLimit.user_id == SUB_UID))).scalar_one_or_none()

    _run(llm_quota.set_user_limit(SUB_UID, 7, ROOT_UID))
    row = _run(_row())
    assert row is not None and row.total_limit == 7 and row.updated_by == ROOT_UID


# ═══════════════════════════════════════════════════════════════════════════════
# 端点：控制台账号额度
# ═══════════════════════════════════════════════════════════════════════════════

def test_accounts_payload_carries_llm_limit_fields(quota_db):
    """GET /server/accounts：既有字段一个不少 + 三个新额度字段；无覆盖时 own=null。"""
    c = _client()
    r = c.get("/api/v1/admin/server/accounts", headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    accs = {a["id"]: a for a in r.json()["accounts"]}
    assert set(accs) == {ROOT_UID, SUB_UID, OTHER_UID}
    for a in accs.values():
        # 既有字段保持（改名/缺失会让控制台与既有测试炸）
        for key in ("username", "nickname", "is_admin", "server_admin", "parent_id",
                    "is_self", "disabled_at", "llm_mode", "last_login_at"):
            assert key in a, key
        # 新字段：未设任何额度 → 生效 0 / own null / unset
        assert a["llm_total_limit"] == 0
        assert a["llm_total_limit_own"] is None
        assert a["llm_total_limit_source"] == "unset"

    # 设全局 → 所有账号 source=global
    assert c.put("/api/v1/admin/server/llm-limit", headers=_auth(ROOT_UID),
                 json={"total_limit": 999957}).status_code == 200
    accs = {a["id"]: a for a in
            c.get("/api/v1/admin/server/accounts", headers=_auth(ROOT_UID)).json()["accounts"]}
    assert accs[SUB_UID]["llm_total_limit"] == 999957
    assert accs[SUB_UID]["llm_total_limit_source"] == "global"
    assert accs[SUB_UID]["llm_total_limit_own"] is None

    # 设账号覆盖 → 只有该账号变 user
    assert c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/llm-limit",
                 headers=_auth(ROOT_UID), json={"total_limit": 50000}).status_code == 200
    accs = {a["id"]: a for a in
            c.get("/api/v1/admin/server/accounts", headers=_auth(ROOT_UID)).json()["accounts"]}
    assert accs[SUB_UID] == dict(accs[SUB_UID],
                                 llm_total_limit=50000, llm_total_limit_own=50000,
                                 llm_total_limit_source="user")
    assert accs[ROOT_UID]["llm_total_limit_source"] == "global"
    assert accs[ROOT_UID]["llm_total_limit_own"] is None


def test_put_user_llm_limit_returns_resolved_and_audits(quota_db):
    """PUT 账号额度：返回 resolve 结果 + 落一条 server.llm_limit.update（before/after 含 scope）。"""
    c = _client()
    r = c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/llm-limit",
              headers=_auth(ROOT_UID), json={"total_limit": 12345})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["user_id"] == SUB_UID
    assert body["total_limit"] == 12345 and body["source"] == "user" and body["own"] == 12345

    entries = _audit(c)
    assert entries and entries[0]["action"] == "server.llm_limit.update"
    assert entries[0]["target"] == f"user:{SUB_UID}"
    assert entries[0]["before"]["scope"] == "user"
    assert entries[0]["after"]["scope"] == "user"
    assert entries[0]["before"]["total_limit"] == 0 and entries[0]["before"]["source"] == "unset"
    assert entries[0]["after"]["total_limit"] == 12345 and entries[0]["after"]["source"] == "user"


def test_put_user_llm_limit_rejects_bad_values(quota_db):
    """负数 / 非整数 / bool / 缺字段 → 400；账号不存在 → 404（且不落审计）。"""
    c = _client()
    for bad in (-1, 1.5, "abc", True, {"a": 1}, [1]):
        assert c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/llm-limit",
                     headers=_auth(ROOT_UID), json={"total_limit": bad}).status_code == 400, bad
    assert c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/llm-limit",
                 headers=_auth(ROOT_UID), json={}).status_code == 400
    assert c.put(f"/api/v1/admin/server/accounts/{MISSING_UID}/llm-limit",
                 headers=_auth(ROOT_UID), json={"total_limit": 10}).status_code == 404
    assert _audit(c) == []  # 失败路径不留痕
    # 合法值仍可写（400/404 未污染状态）
    assert c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/llm-limit",
                 headers=_auth(ROOT_UID), json={"total_limit": 0}).status_code == 200


def test_put_user_llm_limit_null_clears_override(quota_db):
    """null = 清除覆盖 → 回落全局（有全局则 global，无则 unset）。"""
    c = _client()
    assert c.put("/api/v1/admin/server/llm-limit", headers=_auth(ROOT_UID),
                 json={"total_limit": 800}).status_code == 200
    assert c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/llm-limit",
                 headers=_auth(ROOT_UID), json={"total_limit": 30}).status_code == 200
    r = c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/llm-limit",
              headers=_auth(ROOT_UID), json={"total_limit": None})
    assert r.status_code == 200, r.text
    assert r.json()["own"] is None and r.json()["source"] == "global"
    assert r.json()["total_limit"] == 800
    accs = {a["id"]: a for a in
            c.get("/api/v1/admin/server/accounts", headers=_auth(ROOT_UID)).json()["accounts"]}
    assert accs[SUB_UID]["llm_total_limit_own"] is None
    # 审计：清除也留一条（after.source 变回 global）
    entries = _audit(c)
    assert entries[0]["action"] == "server.llm_limit.update"
    assert entries[0]["before"]["source"] == "user" and entries[0]["after"]["source"] == "global"


def test_global_llm_limit_get_put_and_audit(quota_db):
    """GET/PUT /server/llm-limit：0=未设置（unset），写入后 global；落审计。"""
    c = _client()
    r = c.get("/api/v1/admin/server/llm-limit", headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    assert r.json() == {"scope": "global", "total_limit": 0, "source": "unset"}

    r = c.put("/api/v1/admin/server/llm-limit", headers=_auth(ROOT_UID),
              json={"total_limit": 999957})
    assert r.status_code == 200, r.text
    assert r.json()["total_limit"] == 999957 and r.json()["source"] == "global"
    assert c.get("/api/v1/admin/server/llm-limit",
                 headers=_auth(ROOT_UID)).json()["total_limit"] == 999957

    for bad in (-5, 1.5, "x", True):
        assert c.put("/api/v1/admin/server/llm-limit", headers=_auth(ROOT_UID),
                     json={"total_limit": bad}).status_code == 400, bad
    assert c.put("/api/v1/admin/server/llm-limit", headers=_auth(ROOT_UID),
                 json={}).status_code == 400

    entries = _audit(c)
    assert entries[0]["action"] == "server.llm_limit.update"
    assert entries[0]["target"] == "global"
    assert entries[0]["before"]["scope"] == "global"
    assert entries[0]["after"]["total_limit"] == 999957


def test_llm_limit_endpoints_require_server_admin(quota_db):
    """非 server_admin（含子账号）→ 403；未登录 → 401。"""
    c = _client()
    gets = ["/api/v1/admin/server/llm-limit"]
    puts = [("/api/v1/admin/server/llm-limit", {"total_limit": 10}),
            (f"/api/v1/admin/server/accounts/{SUB_UID}/llm-limit", {"total_limit": 10})]
    for path in gets:
        assert c.get(path, headers=_auth(OTHER_UID)).status_code == 403, path
        assert c.get(path, headers=_auth(SUB_UID)).status_code == 403, path
        assert c.get(path).status_code == 401, path
    for path, body in puts:
        assert c.put(path, headers=_auth(OTHER_UID), json=body).status_code == 403, path
        assert c.put(path, headers=_auth(SUB_UID), json=body).status_code == 403, path
        assert c.put(path, json=body).status_code == 401, path
    assert _audit(c) == []  # 403/401 不该写库


# ═══════════════════════════════════════════════════════════════════════════════
# 迁移：单头 / 幂等 / 可逆 / 老库缺表判落后
# ═══════════════════════════════════════════════════════════════════════════════

def _load_a8_migration():
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / "b5c6d7e8f9a0_add_user_llm_limits.py")
    spec = importlib.util.spec_from_file_location("mig_user_llm_limits", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_user_llm_limits_migration_idempotent_and_reversible(tmp_path):
    """upgrade（幂等）/ downgrade / 再 upgrade 全通过；不动既有 llm_usage_limits。"""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_a8_migration()
    assert mig.down_revision == "a5b6c7d8e9f0"

    engine = sa.create_engine(f"sqlite:///{tmp_path}/quota_mig.db")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE llm_usage_limits (id INTEGER PRIMARY KEY, total_limit INTEGER)"))
        conn.execute(sa.text("INSERT INTO llm_usage_limits (id, total_limit) VALUES (1, 42)"))

        def _tables():
            return {r[0] for r in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='table'"))}

        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
            mig.upgrade()  # 幂等：重复执行不报错
        assert "user_llm_limits" in _tables()
        cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(user_llm_limits)"))}
        assert {"user_id", "total_limit", "updated_by", "updated_at"} <= cols
        # 既有全局表不受影响（生产实测只有一行 (1, 999957, 1)）
        assert conn.execute(sa.text("SELECT COUNT(*) FROM llm_usage_limits")).scalar() == 1

        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
        assert "user_llm_limits" not in _tables()

        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()  # 再 upgrade 通过
        assert "user_llm_limits" in _tables()
    engine.dispose()


def test_alembic_single_head_with_a8_migration():
    """迁移链保持单头，且 A8（b5c6d7e8f9a0）在 head 的祖先链上。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, heads  # 单头：新增迁移不得分叉
    revs = [r.revision for r in script.walk_revisions(base="base", head=heads[0])]
    assert "b5c6d7e8f9a0" in revs


def test_sentinel_marks_missing_user_llm_limits_as_behind(monkeypatch, tmp_path):
    """哨兵在列：缺表 → 判「落后」（走 upgrade 补齐），建表后 → 判「当前」（stamp）。"""
    from app.db import migrate as mig_mod

    assert ("user_llm_limits", "user_id") in mig_mod._CURRENT_SCHEMA_SENTINELS
    # 只留本哨兵，隔离其它表/列影响，直接验语义
    monkeypatch.setattr(mig_mod, "_CURRENT_SCHEMA_SENTINELS", [("user_llm_limits", "user_id")])

    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        assert mig_mod._schema_is_current(url) is False  # 缺表 → 判落后
        with engine.begin() as conn:
            conn.execute(sa.text(
                "CREATE TABLE user_llm_limits (user_id INTEGER PRIMARY KEY, total_limit INTEGER)"))
        assert mig_mod._schema_is_current(url) is True  # 有表 → 判当前
    finally:
        engine.dispose()
