# -*- coding: utf-8 -*-
"""P3-1 / P3-2 回归测试（2026-09-20 全量检查报告）。

- **P3-2**：``PUT /api/v1/plugins/{name}`` 在 enabled 与 config 都没给时（调用方什么都没改）
  必须 400，不再静默返回 200；且插件行的 enabled/config 逐字节不变。
- **P3-1**：``all_bound_characters`` 在 flag 开且查不到 enabled 行时，用 ``channel_taken_over``
  区分两种语义相反的「空」——渠道表有行但全部 disabled → 返回空（绝不回落旧全局串，
  杜绝解绑幽灵）；渠道表全空（无人走过 v2 写路径）→ 仍回落旧全局 config 串。

隔离：全程私有 ``tmp_path`` 临时库 + 把各模块 ``async_session_factory`` 换成私有工厂
（同 ``test_plugin_admin_scope_a._patch_session_factories`` 口径）——不连生产库、不写 ``backend/data``。
"""
import asyncio
import json
import sys

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as agent_loop
from app.application import permission_service  # noqa: F401 - 先入 sys.modules，保证接缝被替换
from app.auth.deps import get_current_user_id
from app.models.channel import ChannelBinding
from app.models.character import AICharacter
from app.models.plugin import Plugin
from app.models.user import User
from app.plugins import registry
from app.providers.channel_binding_reader import all_bound_characters

pytestmark = pytest.mark.slow

ROOT_UID = 1
DEMO_PLUGIN = "p3_scope_demo"


# ---------------------------------------------------------------- 共用夹具


def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库工厂接到所有 app.* 模块的 ``async_session_factory``（含 import 期早绑定引用）。"""
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


def _make_factory(tmp_path):
    engine = clone_engine(tmp_path / "p3.db")
    return engine, make_session_factory(engine)


async def _create_schema(engine) -> None:
    """表结构由 ``_dbclone`` 模板库页级克隆提供（含 users/characters/channel_bindings/plugins），
    原 ``Base.metadata.create_all`` 一步不再需要；保留本协程以便各调用点写法不变。"""
    return None


async def _seed_families(factory) -> None:
    """两个独立主账号家庭：user1→角色 101/103，user2→角色 102（与 test_channel_binding_v2 同法）。"""
    # 克隆库默认开 FK（生产同款 PRAGMA）：父行 users 先提交，子行 ai_characters 后提交
    async with factory() as db:
        db.add_all([
            User(id=1, username="p3m1", nickname="m1", is_admin=True),
            User(id=2, username="p3m2", nickname="m2", is_admin=True),
        ])
        await db.commit()
    async with factory() as db:
        db.add_all([
            AICharacter(id=101, user_id=1, name="小慧"),
            AICharacter(id=103, user_id=1, name="小橙"),
            AICharacter(id=102, user_id=2, name="小蓝"),
        ])
        await db.commit()


async def _seed_legacy_global_config(factory) -> None:
    """回落数据源：wechat_ilink 插件全局 config 里的旧串（含两租户角色，用来证明「有没有误回落」）。"""
    async with factory() as db:
        db.add(Plugin(name="wechat_ilink", version="1.0.0",
                      config_json=json.dumps({"allowed_character_ids": "101,102"})))
        await db.commit()


# ================================================================ P3-2：空 body → 400


@pytest.fixture()
def plugin_put_env(monkeypatch, tmp_path):
    """PUT /plugins/{name} 的最小隔离环境：私有库 + 一个已加载的假非渠道插件 + server_admin 登录态。"""
    engine, factory = _make_factory(tmp_path)
    asyncio.run(_create_schema(engine))

    async def _seed():
        async with factory() as db:
            db.add(User(id=ROOT_UID, username="p3root", nickname="root",
                         is_admin=True, server_admin=True))
            db.add(Plugin(name=DEMO_PLUGIN, version="1.0.0", description="d", author="a",
                          category="plugin", type="http", enabled=True,
                          config_json='{"keep": "me"}'))
            await db.commit()

    asyncio.run(_seed())
    _patch_session_factories(monkeypatch, factory)

    entry = {"info": {"name": DEMO_PLUGIN, "version": "1.0.0", "description": "d",
                      "author": "a", "category": "plugin", "type": "http",
                      "config": {}, "hooks": [], "permissions": []},
             "module": None}
    registry._loaded[DEMO_PLUGIN] = entry
    registry._enabled[DEMO_PLUGIN] = True
    registry._db_config[DEMO_PLUGIN] = {"keep": "me"}

    app = FastAPI()
    from app.api import plugins as plugins_api

    app.include_router(plugins_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: ROOT_UID
    yield TestClient(app), factory
    registry._loaded.pop(DEMO_PLUGIN, None)
    registry._enabled.pop(DEMO_PLUGIN, None)
    registry._db_config.pop(DEMO_PLUGIN, None)
    asyncio.run(engine.dispose())


def _row_state(factory):
    async def _read():
        async with factory() as db:
            row = (await db.execute(select(Plugin).where(Plugin.name == DEMO_PLUGIN))).scalar_one()
            return row.enabled, row.config_json

    return asyncio.run(_read())


@pytest.mark.parametrize("body", [
    {},                                  # 完全空 body
    {"enabled": None},                   # 显式 null（与不传同义）
    {"config": None},
    {"enable": True},                    # 键名拼错 → 两个字段都取不到
    {"enabled": None, "config": None},
])
def test_p3_2_空body返回400且插件状态不变(plugin_put_env, body):
    """enabled 与 config 都为空 → 400（复用既有 key config_invalid），插件行 enabled/config_json 原样。"""
    client, factory = plugin_put_env
    before = _row_state(factory)
    assert before == (True, '{"keep": "me"}')

    r = client.put(f"/api/v1/plugins/{DEMO_PLUGIN}", json=body)
    assert r.status_code == 400, (body, r.text)
    assert r.json()["detail"] == "配置参数无效"
    assert _row_state(factory) == before  # 一次写都没发生


def test_p3_2_非空body仍按原语义工作(plugin_put_env):
    """对照：只改 enabled / 只改 config 仍 200 且落库；config 非 dict 仍 400；未知插件仍 404。"""
    client, factory = plugin_put_env

    r = client.put(f"/api/v1/plugins/{DEMO_PLUGIN}", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    assert _row_state(factory) == (False, '{"keep": "me"}')

    r = client.put(f"/api/v1/plugins/{DEMO_PLUGIN}", json={"config": {"x": 1}})
    assert r.status_code == 200, r.text
    enabled, cfg = _row_state(factory)
    assert enabled is False and json.loads(cfg) == {"keep": "me", "x": 1}

    assert client.put(f"/api/v1/plugins/{DEMO_PLUGIN}",
                      json={"config": "not-a-dict"}).status_code == 400
    assert client.put("/api/v1/plugins/no_such_plugin",
                      json={"enabled": True}).status_code == 404


# ================================================================ P3-1：disabled 行不回落


def _flag(monkeypatch, on: bool):
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", on)


async def _add_binding(factory, tenant_id: int, character_id: int, enabled: bool):
    async with factory() as db:
        db.add(ChannelBinding(channel="wechat", tenant_id=tenant_id, owner_user_id=tenant_id,
                              bot_account_id="default", character_id=character_id,
                              enabled=enabled))
        await db.commit()


def test_p3_1_有行但全部disabled不得回落旧全局串(tmp_path, monkeypatch):
    """flag 开 + channel_bindings 只有 disabled 行 → all_bound_characters 返回 []（旧全局串仍在也不回落）。"""
    engine, factory = _make_factory(tmp_path)
    asyncio.run(_create_schema(engine))
    asyncio.run(_seed_families(factory))
    asyncio.run(_seed_legacy_global_config(factory))  # 旧串留着：一旦错误回落就会返回 [(1,101),(2,102)]
    _flag(monkeypatch, True)

    async def _run():
        await _add_binding(factory, 1, 101, enabled=False)
        async with factory() as db:
            assert await all_bound_characters(db, "wechat") == []

    asyncio.run(_run())
    asyncio.run(engine.dispose())


def test_p3_1_渠道表全空仍回落旧全局串并过滤归属(tmp_path, monkeypatch):
    """flag 开但渠道表一行都没有（无人走过 v2 写）→ 仍回落旧全局串，按角色归属家庭 root 解析。"""
    engine, factory = _make_factory(tmp_path)
    asyncio.run(_create_schema(engine))
    asyncio.run(_seed_families(factory))
    asyncio.run(_seed_legacy_global_config(factory))
    _flag(monkeypatch, True)

    async def _run():
        async with factory() as db:
            assert await all_bound_characters(db, "wechat") == [(1, 101), (2, 102)]
        # 别的渠道（douyin）同样全空 → 回落 wechat_ilink 无关：douyin 无插件行 → 空
        async with factory() as db:
            assert await all_bound_characters(db, "douyin") == []
        # 一旦某租户写入 enabled 行（渠道被接管）→ 立刻改读新表，不再回落
        await _add_binding(factory, 2, 102, enabled=True)
        async with factory() as db:
            assert await all_bound_characters(db, "wechat") == [(2, 102)]

    asyncio.run(_run())
    asyncio.run(engine.dispose())


def test_p3_1_disabled行重新enable后恢复可见(tmp_path, monkeypatch):
    """判据只认「有无该渠道行」而非「有无 enabled 行」：停用完再启用 → 恢复读新表，不误回落。"""
    engine, factory = _make_factory(tmp_path)
    asyncio.run(_create_schema(engine))
    asyncio.run(_seed_families(factory))
    asyncio.run(_seed_legacy_global_config(factory))
    _flag(monkeypatch, True)

    async def _run():
        await _add_binding(factory, 1, 101, enabled=False)
        async with factory() as db:
            assert await all_bound_characters(db, "wechat") == []
        async with factory() as db:
            row = (await db.execute(select(ChannelBinding).where(
                ChannelBinding.channel == "wechat"))).scalars().one()
            row.enabled = True
            await db.commit()
        async with factory() as db:
            assert await all_bound_characters(db, "wechat") == [(1, 101)]

    asyncio.run(_run())
    asyncio.run(engine.dispose())


def test_p3_1_flag关时disabled行不影响旧路径回落(tmp_path, monkeypatch):
    """flag 关 → 走旧全局串原样（渠道表是否存在行/是否 disabled 一律不参与判定）。"""
    engine, factory = _make_factory(tmp_path)
    asyncio.run(_create_schema(engine))
    asyncio.run(_seed_families(factory))
    asyncio.run(_seed_legacy_global_config(factory))
    _flag(monkeypatch, False)

    async def _run():
        await _add_binding(factory, 1, 101, enabled=False)
        async with factory() as db:
            assert await all_bound_characters(db, "wechat") == [(1, 101), (2, 102)]

    asyncio.run(_run())
    asyncio.run(engine.dispose())
