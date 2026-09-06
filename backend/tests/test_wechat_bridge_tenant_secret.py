# -*- coding: utf-8 -*-
"""包 C（2026-09-06 待排期清理）：per-tenant bridge secret 测试。

- 多租户隔离：A 密钥不认 B 的请求（tenant 头指定 B、带 A 密钥 → 401）；
- 查无该租户密钥 → 回落全局 env（存量请求零变化）；
- 不带 tenant 头 → 全局校验（现网单通道零改动）；
- 写后立即可用（PUT 后 relay 立即认新密钥）；
- 密文存储：plugin_stores.value_json 不含明文；GET 只回脱敏；子账号写 403。
"""
import asyncio
import pathlib

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.plugins import registry
from app.providers import registry as prov_reg

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"
_GLOBAL_SECRET = "global-env-secret-000000"
_SECRET_A = "tenant-a-secret-123456"
_SECRET_B = "tenant-b-secret-654321"
RELAY_URL = "/api/v1/plugins/bridge/wechat-relay"


@pytest.fixture()
def wc_plugin():
    if not registry.load_plugin_dir(_PLUGIN_DIR):
        raise RuntimeError("wechat_ilink plugin failed to load")
    yield
    prov_reg.unregister_providers_for_source("wechat_ilink")
    registry._loaded.pop("wechat_ilink", None)
    registry._db_config.pop("wechat_ilink", None)
    registry._enabled.pop("wechat_ilink", None)


@pytest.fixture()
def wc_db(wc_plugin, tmp_path, monkeypatch):
    monkeypatch.setenv("AMBRACE_SECRET_KEY", "wechat-ilink-test-secret-000000000000000000000001")
    monkeypatch.setenv("WECHAT_ILINK_BRIDGE_SECRET", _GLOBAL_SECRET)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    from app.application import permission_service as perm

    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(perm, "async_session_factory", factory)

    async def _seed():
        from app.models.user import User
        from app.models.character import AICharacter
        from app.models.channel import ChannelBinding

        async with factory() as db:
            db.add(User(id=1, username="A", nickname="A", is_admin=True))
            db.add(User(id=2, username="B", nickname="B", is_admin=True))
            db.add(User(id=3, username="A_sub", nickname="sub", parent_id=1, is_admin=False))
            db.add(AICharacter(id=101, user_id=1, name="小慧"))
            db.add(AICharacter(id=102, user_id=2, name="小蓝"))
            db.add(ChannelBinding(channel="wechat", tenant_id=1, owner_user_id=1,
                                  bot_account_id="default", character_id=101))
            await db.commit()

    asyncio.run(_seed())
    yield factory
    asyncio.run(engine.dispose())


def _client() -> TestClient:
    from app.api import plugins as plugins_api

    app = FastAPI()
    app.include_router(plugins_api.router)
    return TestClient(app)


def _set_tenant_secret(factory, tenant_id: int, secret: str) -> None:
    """测试辅助：直接写 per-tenant 密钥（经插件 bridge_secret，验证 Fernet 落库）。"""
    import sys

    if _PLUGIN_DIR not in sys.path:
        sys.path.insert(0, str(_PLUGIN_DIR))
    import bridge_secret

    async def _run():
        async with factory() as db:
            await bridge_secret.set_bridge_secret(db, tenant_id, secret)
            await db.commit()

    asyncio.run(_run())


def test_relay_without_tenant_header_uses_global(wc_db):
    """存量全局请求零变化：无 tenant 头 → 全局 env 校验。"""
    c = _client()
    r = c.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi", "msg_id": "m1"},
               headers={"X-AMBRACE-Bridge-Secret": _GLOBAL_SECRET})
    assert r.status_code == 200 and r.json()["code"] == "no_binding"  # 密钥通过（无绑定行是另一语义）
    assert c.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi"},
                  headers={"X-AMBRACE-Bridge-Secret": "wrong"}).status_code == 401


def test_tenant_secret_isolated(wc_db):
    """A 密钥不认 B 请求；A 自己的请求通过（查无绑定行=过了鉴权层）。"""
    _set_tenant_secret(wc_db, 1, _SECRET_A)
    c = _client()
    # tenant=1 + A 密钥 → 鉴权通过（no_binding 是绑定层语义）
    r = c.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi"},
               headers={"X-AMBRACE-Bridge-Secret": _SECRET_A, "x-ambrace-tenant-id": "1"})
    assert r.status_code == 200 and r.json()["code"] == "no_binding"
    # tenant=2（B 未配置）+ A 密钥 → 回落全局 → A 密钥不是全局 → 401
    r2 = c.post(RELAY_URL, json={"ilink_user_id": "wx2", "text": "hi"},
                headers={"X-AMBRACE-Bridge-Secret": _SECRET_A, "x-ambrace-tenant-id": "2"})
    assert r2.status_code == 401
    # tenant=1 但带全局密钥 → per-tenant 已配置，全局密钥不再被接受（fail-closed 隔离）
    r3 = c.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi"},
                headers={"X-AMBRACE-Bridge-Secret": _GLOBAL_SECRET, "x-ambrace-tenant-id": "1"})
    assert r3.status_code == 401


def test_tenant_missing_falls_back_to_global(wc_db):
    """查无该租户密钥 → 回落全局 env（兼容现网单通道零改动）。"""
    c = _client()
    r = c.post(RELAY_URL, json={"ilink_user_id": "wx9", "text": "hi"},
               headers={"X-AMBRACE-Bridge-Secret": _GLOBAL_SECRET, "x-ambrace-tenant-id": "7"})
    assert r.status_code == 200 and r.json()["code"] == "no_binding"


def test_tenant_secret_write_then_usable(wc_db):
    """写后立即可用：set → relay 认新密钥；轮换后旧 per-tenant 密钥失效。"""
    _set_tenant_secret(wc_db, 1, _SECRET_A)
    c = _client()
    r = c.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi"},
               headers={"X-AMBRACE-Bridge-Secret": _SECRET_A, "x-ambrace-tenant-id": "1"})
    assert r.status_code == 200
    _set_tenant_secret(wc_db, 1, _SECRET_B)
    r2 = c.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi"},
                headers={"X-AMBRACE-Bridge-Secret": _SECRET_A, "x-ambrace-tenant-id": "1"})
    assert r2.status_code == 401
    r3 = c.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi"},
                headers={"X-AMBRACE-Bridge-Secret": _SECRET_B, "x-ambrace-tenant-id": "1"})
    assert r3.status_code == 200


def test_tenant_secret_encrypted_at_rest_and_config_api(wc_db):
    """密文存储（value_json 不含明文）+ 配置 API（脱敏返回/子账号 403/删除回落全局）。"""
    _set_tenant_secret(wc_db, 1, _SECRET_A)

    async def _raw():
        from app.models.plugin import PluginStore

        async with wc_db() as db:
            row = (await db.execute(select(PluginStore).where(
                PluginStore.plugin_name == "wechat_ilink", PluginStore.user_id == 1,
                PluginStore.key == "bridge_secret"))).scalar_one_or_none()
            return row.value_json if row else None

    raw = asyncio.run(_raw())
    assert raw is not None and _SECRET_A not in raw and "secret_enc" in raw

    # 配置 API：PUT（root）→ GET 脱敏 → 子账号 403 → DELETE 回落全局
    from app.api import plugins as plugins_api
    from app.auth.deps import get_current_user_id
    app = FastAPI()
    app.include_router(plugins_api.router)
    # 插件 http_router 在装载期由 sdk.router() 创建并登记于 registry（main.py: routes.mount(sdk.router())）
    router_obj = registry._loaded["wechat_ilink"].get("router")
    assert router_obj is not None
    app.include_router(router_obj)
    current = {"uid": 1}
    app.dependency_overrides[get_current_user_id] = lambda: current["uid"]
    c2 = TestClient(app)
    r_put = c2.put("/api/v1/plugins/wechat_ilink/bridge-secret",
                   json={"secret": "rotated-secret-987654321"})
    assert r_put.status_code == 200 and "rotated" not in r_put.text
    r_get = c2.get("/api/v1/plugins/wechat_ilink/bridge-secret")
    assert r_get.status_code == 200 and r_get.json()["has_secret"] is True
    assert "rotated-secret-987654321" not in r_get.text  # 不回明文
    current["uid"] = 3  # 子账号（parent_id=1）→ assert_standalone_owner 403
    r_sub = c2.put("/api/v1/plugins/wechat_ilink/bridge-secret",
                   json={"secret": "sub-should-fail-123456"})
    assert r_sub.status_code == 403, r_sub.text
    current["uid"] = 1
    r_del = c2.delete("/api/v1/plugins/wechat_ilink/bridge-secret")
    assert r_del.status_code == 200 and r_del.json()["deleted"] is True
    # 删除后 tenant=1 回落全局 → 全局密钥重新可用
    c = _client()
    r4 = c.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi"},
                headers={"X-AMBRACE-Bridge-Secret": _GLOBAL_SECRET, "x-ambrace-tenant-id": "1"})
    assert r4.status_code == 200
