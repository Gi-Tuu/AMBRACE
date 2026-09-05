# -*- coding: utf-8 -*-
"""S3 渠道绑定 API 测试（/api/v1/channels/{channel}/bindings）。

- flag 关（channel_binding_v2 默认关）：GET 回落旧全局 config 合成行；PUT 走既有内核
  update_plugin 裁决；DELETE 空串解绑——与现 App 行为等价；
- flag 开：GET 只列本租户；PUT 双 bot 并存（bot_single）；DELETE 只删指定 bot；
  子账号 PUT/DELETE 403、GET 跟随其 root；跨租户 A/B 互不影响（后绑覆盖先绑回归）。
"""
import asyncio
import json
import pathlib

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.agent import loop as agent_loop
from app.api import channel_bindings as cb_api
from app.models.character import AICharacter
from app.models.channel import ChannelBinding
from app.models.plugin import Plugin
from app.models.user import User
from app.application import permission_service as perm

CB_URL = "/api/v1/channels/{ch}/bindings"


@pytest.fixture()
def cb_db(tmp_path, monkeypatch):
    """独立临时库 + 登录态依赖打桩（get_current_user_id → header X-Test-User）。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(perm, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture()
def client(cb_db, monkeypatch):
    """TestClient：channels 路由 + plugins 路由（flag 关 PUT/DELETE 回落 update_plugin 需要）。"""
    from app.api.plugins import router as plugins_router
    from app.auth.deps import get_current_user_id

    app = FastAPI()
    app.include_router(cb_api.router)
    app.include_router(plugins_router)
    current = {"uid": 1}

    async def _fake_user_id():
        return current["uid"]

    app.dependency_overrides[get_current_user_id] = _fake_user_id
    yield TestClient(app), current
    app.dependency_overrides.pop(get_current_user_id, None)


async def _seed_family(factory):
    """user1(char101/103, admin) / user2(char102, admin) / user3(子账号 of 1)。"""
    async with factory() as db:
        db.add(User(id=1, username="main1", nickname="m1", is_admin=True))
        db.add(User(id=2, username="main2", nickname="m2", is_admin=True))
        db.add(User(id=3, username="sub1", nickname="s1", parent_id=1, is_admin=False))
        db.add(AICharacter(id=101, user_id=1, name="小慧"))
        db.add(AICharacter(id=103, user_id=1, name="小橙"))
        db.add(AICharacter(id=102, user_id=2, name="小蓝"))
        await db.commit()


def _flag_on(monkeypatch):
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", True)


def _flag_off(monkeypatch):
    monkeypatch.delitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", raising=False)


def _switch(client_fixture, uid: int):
    c, current = client_fixture
    current["uid"] = uid
    return c


# ================================================================= flag 关（回落旧路径）


@pytest.fixture()
def plugins_loaded():
    """装载 wechat/douyin 插件（flag 关回落 update_plugin 需要 registry 内插件存在）。"""
    from app.plugins import registry

    base = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples"
    for name in ("wechat_ilink", "douyin_mcp"):
        if not registry.load_plugin_dir(base / name):
            raise RuntimeError(f"{name} plugin failed to load")
    yield
    from app.providers import registry as prov_reg

    prov_reg.unregister_providers_for_source("wechat_ilink")
    prov_reg.unregister_providers_for_source("douyin_mcp")
    for n in ("wechat_ilink", "douyin_mcp"):
        registry._loaded.pop(n, None)
        registry._db_config.pop(n, None)
        registry._enabled.pop(n, None)


def test_flag_off_get_returns_synth_row_from_global_config(client, cb_db, monkeypatch):
    _flag_off(monkeypatch)
    asyncio.run(_seed_family(cb_db))

    async def _plugin():
        async with cb_db() as db:
            db.add(Plugin(name="wechat_ilink", version="1.0.0", config_json='{"allowed_character_ids":"101"}'))
            await db.commit()

    asyncio.run(_plugin())
    c = _switch(client, 1)
    r = c.get(CB_URL.format(ch="wechat"))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["bot_account_id"] == "default"
    assert items[0]["character_id"] == 101
    assert items[0]["enabled"] is True


def test_flag_off_put_routes_through_kernel_validation(client, cb_db, monkeypatch, plugins_loaded):
    """flag 关 PUT 走既有内核 update_plugin：跨家庭 403、合法绑定写回全局 config。"""
    _flag_off(monkeypatch)
    asyncio.run(_seed_family(cb_db))
    c = _switch(client, 1)
    # 跨家庭（user2 的角色 102）→ 内核 403
    r = c.put(CB_URL.format(ch="douyin") + "/default", json={"character_id": 102})
    assert r.status_code == 403, r.text
    # 合法绑定 → 200，全局 config 更新
    r2 = c.put(CB_URL.format(ch="douyin") + "/default", json={"character_id": 101})
    assert r2.status_code == 200 and r2.json()["ok"] is True
    row = asyncio.run(_plugin_config(cb_db, "douyin_mcp"))
    assert row == "101"


async def _plugin_config(factory, name):
    async with factory() as db:
        p = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
        return (json.loads(p.config_json or "{}").get("allowed_character_ids") or "") if p else None


def test_flag_off_delete_clears_config(client, cb_db, monkeypatch, plugins_loaded):
    _flag_off(monkeypatch)
    asyncio.run(_seed_family(cb_db))

    async def _plugin():
        async with cb_db() as db:
            db.add(Plugin(name="wechat_ilink", version="1.0.0", config_json='{"allowed_character_ids":"101"}'))
            await db.commit()

    asyncio.run(_plugin())
    c = _switch(client, 1)
    r = c.delete(CB_URL.format(ch="wechat") + "/default")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert asyncio.run(_plugin_config(cb_db, "wechat_ilink")) == ""


def test_flag_off_sub_account_put_403(client, cb_db, monkeypatch, plugins_loaded):
    """flag 关：子账号写操作被内核 main_account_manage_only 挡 403。"""
    _flag_off(monkeypatch)
    asyncio.run(_seed_family(cb_db))
    c = _switch(client, 3)
    r = c.put(CB_URL.format(ch="wechat") + "/default", json={"character_id": 101})
    assert r.status_code == 403


# ================================================================= flag 开（新表路径）


def test_flag_on_get_lists_only_own_tenant(client, cb_db, monkeypatch):
    _flag_on(monkeypatch)
    asyncio.run(_seed_family(cb_db))

    async def _seed():
        async with cb_db() as db:
            db.add(ChannelBinding(channel="wechat", tenant_id=1, owner_user_id=1,
                                  bot_account_id="default", character_id=101, bot_label="我的bot"))
            db.add(ChannelBinding(channel="wechat", tenant_id=2, owner_user_id=2,
                                  bot_account_id="default", character_id=102))
            await db.commit()

    asyncio.run(_seed())
    c1 = _switch(client, 1)
    items1 = c1.get(CB_URL.format(ch="wechat")).json()["items"]
    assert [(i["bot_account_id"], i["character_id"]) for i in items1] == [("default", 101)]
    assert items1[0]["bot_label"] == "我的bot"
    # 子账号 GET 跟随其 root（tenant=1）
    c3 = _switch(client, 3)
    items3 = c3.get(CB_URL.format(ch="wechat")).json()["items"]
    assert [i["character_id"] for i in items3] == [101]


def test_flag_on_put_multi_bot_and_upsert(client, cb_db, monkeypatch):
    """flag 开：bot_single 渠道双 bot 并存；同 bot 重复 PUT=改绑。"""
    _flag_on(monkeypatch)
    asyncio.run(_seed_family(cb_db))
    c = _switch(client, 1)

    class _Port:
        pass

    from app.providers import registry as prov_reg

    prov_reg.register_provider("channel", "testch", lambda: _Port(), source="test")
    prov_reg._ENTRIES[("channel", "testch")]["meta"] = {"binding": {"mode": "bot_single"}}
    try:
        r1 = c.put(CB_URL.format(ch="testch") + "/bot_alpha", json={"character_id": 101})
        r2 = c.put(CB_URL.format(ch="testch") + "/bot_beta", json={"character_id": 103, "bot_label": "二号"})
        assert r1.status_code == 200 and r2.status_code == 200, f"{r1.text} / {r2.text}"
        r3 = c.put(CB_URL.format(ch="testch") + "/bot_alpha", json={"character_id": 103})
        assert r3.status_code == 200  # 同 bot 改绑
        items = c.get(CB_URL.format(ch="testch")).json()["items"]
        got = {i["bot_account_id"]: i["character_id"] for i in items}
        assert got == {"bot_alpha": 103, "bot_beta": 103}
    finally:
        prov_reg._ENTRIES.pop(("channel", "testch"), None)


def test_flag_on_put_errors_map_i18n(client, cb_db, monkeypatch):
    _flag_on(monkeypatch)
    asyncio.run(_seed_family(cb_db))
    c = _switch(client, 1)
    # 跨家庭 403
    r = c.put(CB_URL.format(ch="wechat") + "/default", json={"character_id": 102})
    assert r.status_code == 403
    # 子账号 403
    c3 = _switch(client, 3)
    r2 = c3.put(CB_URL.format(ch="wechat") + "/default", json={"character_id": 101})
    assert r2.status_code == 403


def test_flag_on_cross_tenant_no_overwrite(client, cb_db, monkeypatch):
    """核心回归：A 的 PUT/DELETE 不影响 B 的行（后绑覆盖先绑不再发生）。"""
    _flag_on(monkeypatch)
    asyncio.run(_seed_family(cb_db))
    c1 = _switch(client, 1)
    r1 = c1.put(CB_URL.format(ch="wechat") + "/default", json={"character_id": 101})
    assert r1.status_code == 200, r1.text
    c2 = _switch(client, 2)
    r2 = c2.put(CB_URL.format(ch="wechat") + "/default", json={"character_id": 102})
    assert r2.status_code == 200, r2.text
    # B 再 PUT 后，A 的行仍在（切回 user1 查看）
    _switch(client, 1)
    items1 = c1.get(CB_URL.format(ch="wechat")).json()["items"]
    assert [i["character_id"] for i in items1] == [101]
    # A DELETE 只删自己的行，B 的行不动
    assert c1.delete(CB_URL.format(ch="wechat") + "/default").status_code == 200
    items1 = c1.get(CB_URL.format(ch="wechat")).json()["items"]
    # A 再 GET：渠道已被 v2 接管（B 仍有行）→ 空 items（C2 判据：不合成全局行，杜绝跨租户幽灵）
    assert items1 == []
    _switch(client, 2)
    items2 = c2.get(CB_URL.format(ch="wechat")).json()["items"]
    assert [i["character_id"] for i in items2 if i["updated_at"] is not None] == [102]


def test_flag_on_delete_only_specified_bot(client, cb_db, monkeypatch):
    _flag_on(monkeypatch)
    asyncio.run(_seed_family(cb_db))
    c = _switch(client, 1)

    class _Port:
        pass

    from app.providers import registry as prov_reg

    prov_reg.register_provider("channel", "testch2", lambda: _Port(), source="test")
    prov_reg._ENTRIES[("channel", "testch2")]["meta"] = {"binding": {"mode": "bot_single"}}
    try:
        c.put(CB_URL.format(ch="testch2") + "/bot_alpha", json={"character_id": 101})
        c.put(CB_URL.format(ch="testch2") + "/bot_beta", json={"character_id": 103})
        assert c.delete(CB_URL.format(ch="testch2") + "/bot_alpha").status_code == 200
        items = c.get(CB_URL.format(ch="testch2")).json()["items"]
        assert [i["bot_account_id"] for i in items] == ["bot_beta"]
    finally:
        prov_reg._ENTRIES.pop(("channel", "testch2"), None)
