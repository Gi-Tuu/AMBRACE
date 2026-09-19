# -*- coding: utf-8 -*-
"""账号独立 P2 · 控制台管理面测试（2026-09-19）。

覆盖契约：
- §1 端点：/api/v1/admin/server/*（accounts 扩展 / disabled / llm-mode / modalities 读写 /
  flags 读写 / registration / audit / overview）——含非 server_admin 一律 403；
- §2 数据层：users.disabled_at / users.llm_mode / admin_audit_log / flag_settings / server_settings
  的 Alembic 迁移幂等 + downgrade 可逆 + 单链头；
- §3 行为收紧：写类服务器级端点 require_server_admin，**读类保持 is_admin**；
- §4 账号门禁：禁用 → 登录 403 + 请求阶段 403；llm_mode 在 resolve_modality_config 唯一出口生效；
- §6 验收 1（改服务器默认模型 → 无自有配置的账号可直接用）、3（server_locked → 用户侧写 403）、
  4（每个写动作在 GET /audit 可见）、5（非 server_admin 访问控制台端点 403）。

口径：临时库一律 pytest tmp_path 私有 SQLite 文件（不碰 backend/data），跑完随 tmp 清理；
每个用例前后清 user/server_admin/账号门禁三个进程内缓存（conftest 全局护栏 + 本文件 fixture）。
"""
import asyncio
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import pytest
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import admin as admin_api
from app.api import system as system_api
from app.auth.config import create_token
from app.auth.router import router as auth_router

# 快测档（2026-09-12 约定）：每例起一次私有临时库（约 2-3s/例），与 test_admin_accounts 同档。
pytestmark = pytest.mark.slow

ROOT_UID, SUB_UID, OTHER_UID = 1, 2, 3  # 1=server_admin 家庭主账号；2=其子账号；3=非 server_admin 主账号
ROOT_PW, SUB_PW = "rootpass123", "subpass123"


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库工厂接到所有 app.* 模块的 ``async_session_factory`` 名上（含早绑定引用）。

    与 test_tenant_isolation_matrix._patch_session_factories 同法：``app.db.database`` 是接缝，
    但 permission_service / api.admin / admin_audit_service 等模块在 import 期就
    ``from app.db.database import async_session_factory``（早绑定），需逐个换掉。
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
def p2_db(monkeypatch, tmp_path):
    """私有临时 SQLite：root(server_admin) / sub(子账号) / other(主账号但非 server_admin)。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/p2.db", poolclass=NullPool)
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
                     server_admin=True, password_hash=_hash(ROOT_PW)),
                User(id=SUB_UID, username="sub", nickname="子", is_admin=False,
                     parent_id=ROOT_UID, password_hash=_hash(SUB_PW)),
                User(id=OTHER_UID, username="other", nickname="别的根", is_admin=True,
                     server_admin=False, password_hash=_hash(ROOT_PW)),
            ])
            await db.commit()

    asyncio.run(_init())
    _patch_session_factories(monkeypatch, factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_p2_caches():
    """清三个进程内缓存（禁用状态/llm_mode 缓存跨用例残留会凭空造 403）。"""
    from app.application import permission_service as perm

    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    perm._account_state_cache.clear()
    yield
    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    perm._account_state_cache.clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(admin_api.router)
    app.include_router(system_api.router)
    app.include_router(auth_router)
    return TestClient(app, raise_server_exceptions=False)


def _auth(uid: int) -> dict:
    """真实 JWT（conftest 固定 AUTH_SECRET_KEY）——走真实鉴权路径（含 P2 禁用门禁）。"""
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _audit(c: TestClient, limit: int | None = None) -> list:
    url = "/api/v1/admin/server/audit" + (f"?limit={limit}" if limit is not None else "")
    r = c.get(url, headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    return r.json()["entries"]


# ═══════════════════════════════════════════════════════════════════════════════
# §1 / §6-5：控制台端点一律 require_server_admin
# ═══════════════════════════════════════════════════════════════════════════════

CONSOLE_GETS = [
    "/api/v1/admin/server/accounts",
    "/api/v1/admin/server/modalities",
    "/api/v1/admin/server/flags",
    "/api/v1/admin/server/registration",
    "/api/v1/admin/server/audit",
    "/api/v1/admin/server/overview",
]
CONSOLE_PUTS = [
    ("/api/v1/admin/server/accounts/2/disabled", {"disabled": True}),
    ("/api/v1/admin/server/accounts/2/llm-mode", {"llm_mode": "own"}),
    ("/api/v1/admin/server/accounts/2/server-admin", {"enabled": True}),
    ("/api/v1/admin/server/modalities/llm", {"enabled": True}),
    ("/api/v1/admin/server/flags/agent_loop_chat", {"enabled": False}),
    ("/api/v1/admin/server/registration", {"mode": "open"}),
]


def test_console_endpoints_require_server_admin(p2_db):
    """§6-5：非 server_admin（含子账号）访问任一控制台端点 → 403；未登录 → 401。"""
    c = _client()
    for path in CONSOLE_GETS:
        assert c.get(path, headers=_auth(OTHER_UID)).status_code == 403, path
        assert c.get(path, headers=_auth(SUB_UID)).status_code == 403, path
        assert c.get(path).status_code == 401, path
    for path, body in CONSOLE_PUTS:
        assert c.put(path, headers=_auth(OTHER_UID), json=body).status_code == 403, path
        assert c.put(path, headers=_auth(SUB_UID), json=body).status_code == 403, path
        assert c.put(path, json=body).status_code == 401, path
    # server_admin 全部放行（GET 侧）
    for path in CONSOLE_GETS:
        assert c.get(path, headers=_auth(ROOT_UID)).status_code == 200, path


def test_accounts_payload_p2_fields(p2_db):
    """§1.1：accounts 扩展 disabled_at / llm_mode / last_login_at，且不泄漏敏感字段。"""
    r = _client().get("/api/v1/admin/server/accounts", headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    accs = {a["id"]: a for a in r.json()["accounts"]}
    assert set(accs) == {ROOT_UID, SUB_UID, OTHER_UID}
    a = accs[SUB_UID]
    for key in ("disabled_at", "llm_mode", "last_login_at", "server_admin", "is_self",
                "parent_id", "avatar_url"):
        assert key in a, key
    assert a["disabled_at"] is None
    assert a["llm_mode"] == "default_allowed"
    assert a["last_login_at"] is None  # 契约 §2 未加登录时间列 → 恒 null（见报告）
    assert a["is_self"] is False and accs[ROOT_UID]["is_self"] is True
    assert "password_hash" not in a and "password" not in a


# ═══════════════════════════════════════════════════════════════════════════════
# §4 / §6-2：禁用账号 → 登录 403 + 请求阶段 403
# ═══════════════════════════════════════════════════════════════════════════════

def test_disable_blocks_login_and_requests_then_enable_restores(p2_db):
    c = _client()
    # 基线：可登录、可调需登录端点
    assert c.post("/api/v1/auth/login", json={"username": "sub", "password": SUB_PW}).status_code == 200
    assert c.get("/api/v1/system/status/detail", headers=_auth(SUB_UID)).status_code == 200

    r = c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/disabled",
              headers=_auth(ROOT_UID), json={"disabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["disabled"] is True and r.json()["disabled_at"]

    # 登录 403 + 可读提示
    r = c.post("/api/v1/auth/login", json={"username": "sub", "password": SUB_PW})
    assert r.status_code == 403, r.text
    assert "禁用" in r.json()["detail"]
    # 既有 token 的后续请求在鉴权阶段即 403（30s 短缓存）
    r = c.get("/api/v1/system/status/detail", headers=_auth(SUB_UID))
    assert r.status_code == 403
    assert "禁用" in r.json()["detail"]
    # 审计可见（§6-4）
    entries = _audit(c)
    hit = [e for e in entries if e["action"] == "account.disable"]
    assert hit and hit[0]["target"] == f"user:{SUB_UID}" and hit[0]["actor_username"] == "root"

    # 启用 → disabled_at 置 NULL，登录与请求恢复
    r = c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/disabled",
              headers=_auth(ROOT_UID), json={"disabled": False})
    assert r.status_code == 200 and r.json()["disabled_at"] is None
    assert c.post("/api/v1/auth/login", json={"username": "sub", "password": SUB_PW}).status_code == 200
    assert c.get("/api/v1/system/status/detail", headers=_auth(SUB_UID)).status_code == 200
    assert any(e["action"] == "account.enable" for e in _audit(c))


def test_disable_self_and_bad_requests_rejected(p2_db):
    """护栏：不可禁用自己；缺字段 400；目标不存在 404。"""
    c = _client()
    r = c.put(f"/api/v1/admin/server/accounts/{ROOT_UID}/disabled",
              headers=_auth(ROOT_UID), json={"disabled": True})
    assert r.status_code == 400 and "自己" in r.json()["detail"]
    assert c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/disabled",
                 headers=_auth(ROOT_UID), json={}).status_code == 400
    assert c.put("/api/v1/admin/server/accounts/999/disabled",
                 headers=_auth(ROOT_UID), json={"disabled": True}).status_code == 404


def test_llm_mode_endpoint_validation_and_audit(p2_db):
    """§1.1：llm_mode 三值可写、非法值 400、目标不存在 404、写动作留痕。"""
    c = _client()
    for mode in ("own", "blocked", "default_allowed"):
        r = c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/llm-mode",
                  headers=_auth(ROOT_UID), json={"llm_mode": mode})
        assert r.status_code == 200, r.text
        assert r.json()["llm_mode"] == mode
        accs = {a["id"]: a for a in c.get("/api/v1/admin/server/accounts",
                                         headers=_auth(ROOT_UID)).json()["accounts"]}
        assert accs[SUB_UID]["llm_mode"] == mode
    assert c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/llm-mode",
                 headers=_auth(ROOT_UID), json={"llm_mode": "whatever"}).status_code == 400
    assert c.put("/api/v1/admin/server/accounts/999/llm-mode",
                 headers=_auth(ROOT_UID), json={"llm_mode": "own"}).status_code == 404
    assert any(e["action"] == "account.llm_mode" for e in _audit(c))


# ═══════════════════════════════════════════════════════════════════════════════
# §1.2 / §6-1：默认模型读写 + 无自有配置账号可直接用服务器默认
# ═══════════════════════════════════════════════════════════════════════════════

def test_modalities_read_write_and_key_masking(p2_db):
    c = _client()
    r = c.get("/api/v1/admin/server/modalities", headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    mods = {m["key"]: m for m in r.json()["modalities"]}
    assert set(mods) == {"llm", "image", "vlm", "speech", "multimodal"}
    assert all("api_key" not in m for m in r.json()["modalities"])  # 只回 has_api_key
    assert mods["llm"]["enabled"] is False and mods["llm"]["has_api_key"] is False

    r = c.put("/api/v1/admin/server/modalities/llm", headers=_auth(ROOT_UID), json={
        "enabled": True, "base_url": "http://llm.example/v1", "api_key": "sk-secret-1234",
        "model": "m1", "provider": "openai",
    })
    assert r.status_code == 200, r.text
    assert r.json()["has_api_key"] is True and r.json()["base_url"] == "http://llm.example/v1"
    mods = {m["key"]: m for m in c.get("/api/v1/admin/server/modalities",
                                      headers=_auth(ROOT_UID)).json()["modalities"]}
    assert mods["llm"]["enabled"] is True and mods["llm"]["model"] == "m1"
    assert mods["llm"]["provider"] == "openai" and mods["llm"]["has_api_key"] is True

    # 空串 = 清空该字段（与既有 system.py 写接口一致）
    r = c.put("/api/v1/admin/server/modalities/llm", headers=_auth(ROOT_UID), json={"api_key": ""})
    assert r.json()["has_api_key"] is False
    # 生图 daily_limit
    r = c.put("/api/v1/admin/server/modalities/image", headers=_auth(ROOT_UID),
              json={"enabled": True, "daily_limit": 7})
    assert r.status_code == 200 and r.json()["daily_limit"] == 7
    # 非法入参
    assert c.put("/api/v1/admin/server/modalities/nope", headers=_auth(ROOT_UID),
                 json={"enabled": True}).status_code == 400
    assert c.put("/api/v1/admin/server/modalities/llm", headers=_auth(ROOT_UID), json={}).status_code == 400
    assert c.put("/api/v1/admin/server/modalities/image", headers=_auth(ROOT_UID),
                 json={"daily_limit": "abc"}).status_code == 400
    # 别名 image_gen 归一
    assert c.put("/api/v1/admin/server/modalities/image_gen", headers=_auth(ROOT_UID),
                 json={"model": "img-m"}).json()["key"] == "image"

    # 审计不落 api_key 明文
    raw = json.dumps(_audit(c), ensure_ascii=False)
    assert "sk-secret-1234" not in raw and "***" in raw
    assert any(e["action"] == "server.modality.update" for e in _audit(c))


def test_account_without_own_config_uses_server_default(p2_db):
    """§6-1：控制台改服务器默认模型 → 无自有配置的账号（子账号/独立账号）直接可用。"""
    c = _client()
    assert c.put("/api/v1/admin/server/modalities/llm", headers=_auth(ROOT_UID), json={
        "enabled": True, "base_url": "http://srv.example/v1", "api_key": "sk-srv", "model": "srv-m",
    }).status_code == 200
    assert c.put("/api/v1/admin/server/modalities/vlm", headers=_auth(ROOT_UID), json={
        "enabled": True, "base_url": "http://srv.example/v1", "api_key": "sk-srv", "model": "vlm-m",
    }).status_code == 200

    from app.application.llm_config_service import resolve_modality_config

    async def _run():
        async with p2_db() as db:
            return (await resolve_modality_config("llm", SUB_UID, None, db),
                    await resolve_modality_config("vlm", OTHER_UID, None, db),
                    await resolve_modality_config("llm", SUB_UID, None, db, required=True))

    llm, vlm, llm_required = asyncio.run(_run())
    assert llm["scope"] == "server" and llm["base_url"] == "http://srv.example/v1"
    assert vlm["scope"] == "server" and vlm["model"] == "vlm-m"
    assert llm_required["scope"] == "server"


# ═══════════════════════════════════════════════════════════════════════════════
# §1.3 / §6-3：开关读写 + server_locked / self_service 强校验
# ═══════════════════════════════════════════════════════════════════════════════

def test_flags_list_defaults_and_types(p2_db):
    """§1.3：值取 AGENT_FLAGS 生效值；缺 flag_settings 行 = 自助开、未锁定。"""
    c = _client()
    r = c.get("/api/v1/admin/server/flags", headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    flags = {f["key"]: f for f in r.json()["flags"]}
    assert "agent_loop_chat" in flags
    f = flags["agent_loop_chat"]
    for key in ("key", "enabled", "title", "desc", "self_service", "server_locked"):
        assert key in f, key
    assert f["self_service"] is True and f["server_locked"] is False and f["title"] is None
    # 非 bool 键：类型标注出来（契约字段外附加，UI 可只读展示）
    assert flags["domain_event_retention_days"]["type"] == "int"


def test_flag_lock_blocks_user_side_write(p2_db):
    """§6-3：控制台置 server_locked → 用户侧（App）写该开关 403；控制台仍可改。"""
    from app.agent.loop import AGENT_FLAGS

    c = _client()
    saved = AGENT_FLAGS["agent_loop_chat"]
    try:
        r = c.put("/api/v1/admin/server/flags/agent_loop_chat", headers=_auth(ROOT_UID),
                  json={"enabled": False, "self_service": False, "server_locked": True})
        assert r.status_code == 200, r.text
        assert r.json()["enabled"] is False and r.json()["server_locked"] is True
        assert AGENT_FLAGS["agent_loop_chat"] is False  # 热更新立即生效
        assert c.get("/api/v1/admin/server/flags", headers=_auth(ROOT_UID)).status_code == 200

        # 用户侧写接口（App → PUT /api/v1/system/feature-flags/{key}）：锁定 → 403 可读
        r = c.put("/api/v1/system/feature-flags/agent_loop_chat",
                  headers=_auth(ROOT_UID), json={"enabled": True})
        assert r.status_code == 403, r.text
        assert "锁定" in r.json()["detail"]

        # 锁定只拦用户侧：控制台路径仍可改
        r = c.put("/api/v1/admin/server/flags/agent_loop_chat", headers=_auth(ROOT_UID),
                  json={"enabled": True})
        assert r.status_code == 200 and AGENT_FLAGS["agent_loop_chat"] is True

        # self_service=false（未锁定）同样不可自助改
        assert c.put("/api/v1/admin/server/flags/agent_loop_chat", headers=_auth(ROOT_UID),
                     json={"server_locked": False, "self_service": False}).status_code == 200
        r = c.put("/api/v1/system/feature-flags/agent_loop_chat",
                  headers=_auth(ROOT_UID), json={"enabled": False})
        assert r.status_code == 403 and "自助" in r.json()["detail"]
    finally:
        AGENT_FLAGS["agent_loop_chat"] = saved
        c.put("/api/v1/admin/server/flags/agent_loop_chat", headers=_auth(ROOT_UID),
              json={"server_locked": False, "self_service": True})

    # 非 bool 键带 enabled → 400（沿用 flag_service 类型防护口径）；未知 key → 404
    assert c.put("/api/v1/admin/server/flags/domain_event_retention_days",
                 headers=_auth(ROOT_UID), json={"enabled": True}).status_code == 400
    assert c.put("/api/v1/admin/server/flags/nope",
                 headers=_auth(ROOT_UID), json={"enabled": True}).status_code == 404
    assert c.put("/api/v1/admin/server/flags/agent_loop_chat",
                 headers=_auth(ROOT_UID), json={"nonsense": 1}).status_code == 400
    assert any(e["action"] == "server.flag.update" for e in _audit(c))


# ═══════════════════════════════════════════════════════════════════════════════
# §1.4：注册策略
# ═══════════════════════════════════════════════════════════════════════════════

def test_registration_policy_default_open_and_invalid(p2_db):
    c = _client()
    assert c.get("/api/v1/admin/server/registration", headers=_auth(ROOT_UID)).json()["mode"] == "open"
    assert c.put("/api/v1/admin/server/registration", headers=_auth(ROOT_UID),
                 json={"mode": "nope"}).status_code == 400
    assert c.put("/api/v1/admin/server/registration", headers=_auth(ROOT_UID),
                 json={"mode": "open"}).status_code == 200
    # 缺 server_settings 行 = open（与现状一致）
    r = c.post("/api/v1/auth/register", json={"username": "fresh", "password": "abcd1234"})
    assert r.status_code == 201, r.text
    assert any(e["action"] == "server.registration.update" for e in _audit(c))


def test_registration_closed_and_invite_only(p2_db):
    c = _client()
    assert c.put("/api/v1/admin/server/registration", headers=_auth(ROOT_UID),
                 json={"mode": "closed"}).status_code == 200
    r = c.post("/api/v1/auth/register", json={"username": "blocked1", "password": "abcd1234"})
    assert r.status_code == 403 and "注册" in r.json()["detail"]

    assert c.put("/api/v1/admin/server/registration", headers=_auth(ROOT_UID),
                 json={"mode": "invite_only"}).status_code == 200
    # 无受邀码 → 403
    r = c.post("/api/v1/auth/register", json={"username": "blocked2", "password": "abcd1234"})
    assert r.status_code == 403 and "邀请" in r.json()["detail"]
    # 无效码 → 403
    assert c.post("/api/v1/auth/register", json={
        "username": "blocked3", "password": "abcd1234", "invite_code": "DEADBEEF",
    }).status_code == 403

    # 有效受邀码（主账号发出）→ 注册成功，新账号挂到发码主账号下并一次性消费
    async def _seed_invite():
        from app.models.user import AccountInvite

        async with p2_db() as db:
            db.add(AccountInvite(
                code="ABCD1234", creator_id=ROOT_UID,
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5),
            ))
            await db.commit()

    asyncio.run(_seed_invite())
    r = c.post("/api/v1/auth/register", json={
        "username": "invited", "password": "abcd1234", "invite_code": "abcd1234",
    })
    assert r.status_code == 201, r.text
    new_uid = r.json()["user_id"]

    async def _read():
        from app.models.user import AccountInvite, User

        async with p2_db() as db:
            u = (await db.execute(select(User).where(User.username == "invited"))).scalar_one()
            inv = (await db.execute(
                select(AccountInvite).where(AccountInvite.code == "ABCD1234")
            )).scalar_one()
            return u.parent_id, bool(u.is_admin), inv.used_by

    assert asyncio.run(_read()) == (ROOT_UID, False, new_uid)
    # 一次性：同码再用 → 403
    assert c.post("/api/v1/auth/register", json={
        "username": "invited2", "password": "abcd1234", "invite_code": "ABCD1234",
    }).status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# §4：llm_mode 在四模态唯一出口生效
# ═══════════════════════════════════════════════════════════════════════════════

def test_llm_mode_gate_in_resolve_modality_config(p2_db):
    """§4：blocked → 403；own → 不回落服务器默认；default_allowed → 现状（可回落）。"""
    from app.application.llm_config_service import resolve_modality_config

    c = _client()
    assert c.put("/api/v1/admin/server/modalities/llm", headers=_auth(ROOT_UID), json={
        "enabled": True, "base_url": "http://srv.example/v1", "api_key": "sk-srv", "model": "srv-m",
    }).status_code == 200

    def _set_mode(mode: str) -> None:
        r = c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/llm-mode",
                  headers=_auth(ROOT_UID), json={"llm_mode": mode})
        assert r.status_code == 200, r.text

    async def _resolve(uid: int):
        async with p2_db() as db:
            return await resolve_modality_config("llm", uid, None, db)

    # default_allowed（默认）：回落服务器默认
    assert asyncio.run(_resolve(SUB_UID))["scope"] == "server"

    # own：不回落服务器默认 → 无配置返回 None；required=True 给可读 400
    _set_mode("own")
    assert asyncio.run(_resolve(SUB_UID)) is None

    async def _required():
        async with p2_db() as db:
            return await resolve_modality_config("llm", SUB_UID, None, db, required=True)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(_required())
    assert ei.value.status_code == 400 and "未配置" in str(ei.value.detail)

    # blocked：403 可读
    _set_mode("blocked")
    with pytest.raises(HTTPException) as ei2:
        asyncio.run(_resolve(SUB_UID))
    assert ei2.value.status_code == 403 and "禁用" in str(ei2.value.detail)

    # 禁用账号 → 403（账号门禁与 llm_mode 同一行、同一缓存）
    _set_mode("default_allowed")
    assert c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/disabled",
                 headers=_auth(ROOT_UID), json={"disabled": True}).status_code == 200
    with pytest.raises(HTTPException) as ei3:
        asyncio.run(_resolve(SUB_UID))
    assert ei3.value.status_code == 403 and "禁用" in str(ei3.value.detail)


# ═══════════════════════════════════════════════════════════════════════════════
# §3：写类收紧 require_server_admin，读类保持 is_admin
# ═══════════════════════════════════════════════════════════════════════════════

def test_section3_write_tightened_reads_unchanged(p2_db):
    c = _client()
    # 读类保持 is_admin：other(3) 是家庭主账号（非 server_admin）→ 200
    for path in ("/api/v1/system/api-config/server", "/api/v1/system/feature-flags",
                 "/api/v1/system/llm-usage", "/api/v1/system/vlm-config/server",
                 "/api/v1/system/image-gen-config/server", "/api/v1/system/speech-config/server"):
        assert c.get(path, headers=_auth(OTHER_UID)).status_code == 200, path

    # 写类收紧：非 server_admin（含家庭主账号）→ 403。
    # 注：**开关写不在此列**（Codex 09-19 修订契约 §1.3）——本产品里每个独立账号都用自己的
    # App 开关页，若把「用户侧写开关」一并收紧，会让所有非管理员账号一写开关就 403（功能回归）；
    # 开关的服务器控制改由逐键策略承担，断言见本函数下方「逐键锁定」段。
    writes = [
        ("/api/v1/system/api-config/server", {"enabled": True}),
        ("/api/v1/system/vlm-config/server", {"enabled": True}),
        ("/api/v1/system/image-gen-config/server", {"enabled": True}),
        ("/api/v1/system/speech-config/server", {"enabled": True}),
        ("/api/v1/system/api-config/task/server/chat", {"enabled": True}),
        ("/api/v1/system/llm-usage/limit", {"total_limit": 10}),
    ]
    for path, body in writes:
        assert c.put(path, headers=_auth(OTHER_UID), json=body).status_code == 403, path
    # 备份触发/下载（不在用例里真触发备份：只验证非 server_admin 被闸门拦下）
    assert c.post("/api/v1/system/backup", headers=_auth(OTHER_UID)).status_code == 403
    assert c.get("/api/v1/system/backup/download", headers=_auth(OTHER_UID)).status_code == 403

    # 开关写（Codex 09-19 修订）：非 server_admin 且**未被锁定**时仍可自助改（保持既有可用性）；
    # 控制台逐键锁定后，同一路径立刻 403（后端强校验，不靠前端禁用）。
    from app.agent.loop import AGENT_FLAGS as _AGF
    _saved_chat = _AGF["agent_loop_chat"]
    try:
        assert c.put("/api/v1/system/feature-flags/agent_loop_chat", headers=_auth(OTHER_UID),
                     json={"enabled": _saved_chat}).status_code == 200
        assert c.put("/api/v1/admin/server/flags/agent_loop_chat", headers=_auth(ROOT_UID),
                     json={"server_locked": True}).status_code == 200
        assert c.put("/api/v1/system/feature-flags/agent_loop_chat", headers=_auth(OTHER_UID),
                     json={"enabled": _saved_chat}).status_code == 403
        assert c.put("/api/v1/admin/server/flags/agent_loop_chat", headers=_auth(ROOT_UID),
                     json={"server_locked": False}).status_code == 200
    finally:
        _AGF["agent_loop_chat"] = _saved_chat

    # server_admin 放行（写类），且落审计（App 侧服务器配置写也留痕）
    from app.agent.loop import AGENT_FLAGS

    saved = AGENT_FLAGS["agent_loop_chat"]
    try:
        assert c.put("/api/v1/system/api-config/server", headers=_auth(ROOT_UID), json={
            "enabled": True, "base_url": "http://srv2.example/v1", "api_key": "sk-x", "model": "m2",
        }).status_code == 200
        assert c.put("/api/v1/system/vlm-config/server", headers=_auth(ROOT_UID), json={
            "enabled": True, "base_url": "http://srv2.example/v1",
        }).status_code == 200
        assert c.put("/api/v1/system/llm-usage/limit", headers=_auth(ROOT_UID),
                     json={"total_limit": 10}).status_code == 200
        assert c.put("/api/v1/system/feature-flags/agent_loop_chat", headers=_auth(ROOT_UID),
                     json={"enabled": saved}).status_code == 200
    finally:
        AGENT_FLAGS["agent_loop_chat"] = saved

    actions = {e["action"] for e in _audit(c)}
    assert {"server.api_config.update", "server.vlm_config.update",
            "server.llm_usage_limit.update", "server.feature_flag.update"} <= actions


def test_feature_flag_write_unknown_key_still_404(p2_db):
    """既有口径不变：App 侧写未知 key → 404（不因收紧/加锁改变返回码语义）。"""
    c = _client()
    assert c.put("/api/v1/system/feature-flags/not_a_flag",
                 headers=_auth(ROOT_UID), json={"enabled": True}).status_code == 404
    assert c.put("/api/v1/system/feature-flags/agent_loop_chat",
                 headers=_auth(ROOT_UID), json={}).status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# §1.5 / §1.6：审计与概览
# ═══════════════════════════════════════════════════════════════════════════════

def test_audit_shape_order_and_limit(p2_db):
    c = _client()
    assert c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/llm-mode",
                 headers=_auth(ROOT_UID), json={"llm_mode": "own"}).status_code == 200
    assert c.put("/api/v1/admin/server/registration", headers=_auth(ROOT_UID),
                 json={"mode": "open"}).status_code == 200
    entries = _audit(c)
    assert len(entries) >= 2
    for key in ("id", "actor_user_id", "actor_username", "action", "target", "before", "after",
                "created_at"):
        assert key in entries[0], key
    assert entries[0]["actor_user_id"] == ROOT_UID and entries[0]["actor_username"] == "root"
    # 最新在前（created_at DESC + id DESC 兜底）
    assert entries[0]["action"] == "server.registration.update"
    assert entries[0]["before"] == {"mode": "open"} and entries[0]["after"] == {"mode": "open"}
    assert entries[1]["action"] == "account.llm_mode"
    assert entries[1]["before"] == {"llm_mode": "default_allowed"}
    # limit 生效
    assert len(_audit(c, limit=1)) == 1


def test_overview_counts(p2_db):
    c = _client()
    r = c.get("/api/v1/admin/server/overview", headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["accounts"] == 3 and j["disabled"] == 0 and j["server_admins"] == 1
    assert isinstance(j["flags_on"], int) and j["flags_on"] > 0
    assert j["version"] and j["version"] != "unknown"
    # 禁用后计数变化
    assert c.put(f"/api/v1/admin/server/accounts/{SUB_UID}/disabled",
                 headers=_auth(ROOT_UID), json={"disabled": True}).status_code == 200
    assert c.get("/api/v1/admin/server/overview", headers=_auth(ROOT_UID)).json()["disabled"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# §2：Alembic 迁移（幂等 / 可逆 / 单链头）
# ═══════════════════════════════════════════════════════════════════════════════

def _load_p2_migration():
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / "c5d6e7f8a9b0_add_admin_console_p2.py")
    spec = importlib.util.spec_from_file_location("mig_console_p2", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_console_p2_migration_adds_columns_tables_idempotent(tmp_path):
    """只加列/只加表、默认值让现有行为不变、可重复执行、downgrade 可逆。"""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_p2_migration()
    assert mig.down_revision == "c4d5e6f7a8b9"

    engine = sa.create_engine(f"sqlite:///{tmp_path}/p2mig.db")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username VARCHAR(50))"
        ))
        conn.execute(sa.text("INSERT INTO users (id, username) VALUES (1,'a')"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
            mig.upgrade()  # 幂等：重复执行不报错
        cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(users)"))}
        assert {"disabled_at", "llm_mode"} <= cols
        tables = {r[0] for r in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table'")
        )}
        assert {"admin_audit_log", "flag_settings", "server_settings"} <= tables
        # 默认值：llm_mode=default_allowed、disabled_at=NULL（现有行为不变）
        row = conn.execute(sa.text("SELECT llm_mode, disabled_at FROM users WHERE id=1")).fetchone()
        assert tuple(row) == ("default_allowed", None)
        # flag_settings 缺行默认由服务层给出（self_service=1 / server_locked=0）：表内无数据
        assert conn.execute(sa.text("SELECT COUNT(*) FROM flag_settings")).scalar() == 0

        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
        cols_after = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(users)"))}
        assert not ({"disabled_at", "llm_mode"} & cols_after)
        tables_after = {r[0] for r in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table'")
        )}
        assert not ({"admin_audit_log", "flag_settings", "server_settings"} & tables_after)
    engine.dispose()


def test_alembic_single_head_with_p2_migration():
    """迁移链保持单头（本迁移是链头）——新增迁移不得分叉。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == ["c5d6e7f8a9b0"]
