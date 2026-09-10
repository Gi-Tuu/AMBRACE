# -*- coding: utf-8 -*-
"""多 ClawBot 开放（C5/C10，2026-09-06）后端测试。

- 双 bot 各绑不同角色并存（uq_wechat_tenant_bot_char 允许）；同 bot 同角色换绑；
- /bind 落 confirmed 的 ilink_bot_id（归一化 @im.bot → -im-bot）为 bot_account_id；
- unbind/rebind 按 bot 生效不误伤另一 bot（缺省自动归属唯一 bot）；
- relay 带 bot_account_id 精确定位 + 兼容窗（旧网关无 bot、存量行已修正为真实 id）。
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

import os as _os

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"
_PLUGIN_DIR_STR = str(_PLUGIN_DIR)
_SECRET = "multibot-test-secret-000"
RELAY_URL = "/api/v1/plugins/bridge/wechat-relay"


@pytest.fixture()
def mb_plugin():
    import sys

    if _PLUGIN_DIR_STR not in sys.path:
        sys.path.insert(0, _PLUGIN_DIR_STR)
    if not registry.load_plugin_dir(_PLUGIN_DIR):
        raise RuntimeError("wechat_ilink plugin failed to load")
    yield
    prov_reg.unregister_providers_for_source("wechat_ilink")
    registry._loaded.pop("wechat_ilink", None)
    registry._db_config.pop("wechat_ilink", None)
    registry._enabled.pop("wechat_ilink", None)


@pytest.fixture()
def mb_db(mb_plugin, tmp_path, monkeypatch):
    monkeypatch.setenv("AMBRACE_SECRET_KEY", "wechat-ilink-test-secret-000000000000000000000001")
    monkeypatch.setenv("WECHAT_ILINK_BRIDGE_SECRET", _SECRET)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # T5（2026-09-10）：插件表已从 Base.metadata 剥离到独立 plugin_metadata，
            # 本临时库须显式补建插件表（插件已在本文件 fixture 加载，plugin_metadata 已注册对应表）。
            from app.plugins.plugin_base import plugin_metadata
            await conn.run_sync(plugin_metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    from app.application import permission_service as perm

    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(perm, "async_session_factory", factory)

    async def _seed():
        from app.models.user import User
        from app.models.character import AICharacter

        async with factory() as db:
            db.add(User(id=1, username="main", nickname="m", is_admin=True))
            db.add(AICharacter(id=101, user_id=1, name="小慧"))
            db.add(AICharacter(id=102, user_id=1, name="小橙"))
            await db.commit()

    asyncio.run(_seed())
    yield factory
    asyncio.run(engine.dispose())


def _plugin_models():
    return registry._loaded["wechat_ilink"]["module"].models


async def _seed_two_bots(factory):
    """双 bot 各绑不同角色（C10：uq_wechat_tenant_bot_char 允许不同 bot 不同角色）。"""
    M = _plugin_models()
    async with factory() as db:
        db.add(M.WeChatILinkBinding(
            user_id=1, tenant_id=1, bot_account_id="botA-im-bot", character_id=101,
            ilink_user_id="wx1", bot_token_enc="x", baseurl="https://ilinkai.weixin.qq.com", enabled=True))
        db.add(M.WeChatILinkBinding(
            user_id=1, tenant_id=1, bot_account_id="botB-im-bot", character_id=102,
            ilink_user_id="wx1", bot_token_enc="y", baseurl="https://ilinkai.weixin.qq.com", enabled=True))
        await db.commit()


def _client() -> TestClient:
    from app.api import plugins as plugins_api

    app = FastAPI()
    app.include_router(plugins_api.router)
    return TestClient(app)


def _patch_reply(monkeypatch, text="回复"):
    mod = importlib.import_module("inbound")

    async def _fake(user_id, character_id, content):
        return text

    monkeypatch.setattr(mod, "_run_companion_reply", _fake)


import importlib  # noqa: E402


def test_two_bots_bind_different_characters(mb_db):
    """双 bot 各绑不同角色并存；同 bot 同角色换绑幂等。"""
    asyncio.run(_seed_two_bots(mb_db))
    M = _plugin_models()

    async def _rows():
        async with mb_db() as db:
            return {b.bot_account_id: b.character_id for b in
                    (await db.execute(select(M.WeChatILinkBinding))).scalars().all()}

    got = asyncio.run(_rows())
    assert got == {"botA-im-bot": 101, "botB-im-bot": 102}


def test_unbind_by_bot_does_not_touch_other_bot(mb_db):
    """unbind 按 bot 生效：只清目标 bot 行凭据，另一 bot 不受影响。"""
    import routes as plugin_routes

    asyncio.run(_seed_two_bots(mb_db))

    async def _run():
        async with mb_db() as db:
            await plugin_routes._clear_binding(db, 1, 102, bot_account_id="botB-im-bot")
            await db.commit()

    asyncio.run(_run())
    M = _plugin_models()

    async def _rows():
        async with mb_db() as db:
            return {b.bot_account_id: (b.enabled, b.bot_token_enc) for b in
                    (await db.execute(select(M.WeChatILinkBinding))).scalars().all()}

    rows = asyncio.run(_rows())
    assert rows["botB-im-bot"] == (False, "")       # 目标 bot 被清
    assert rows["botA-im-bot"] == (True, "x")       # 另一 bot 不受影响


def test_rebind_by_bot_only_migrates_target(mb_db):
    """rebind 按 bot 只迁目标 bot 行（flag-on 分支的插件行迁移语义）。"""
    import routes as plugin_routes

    asyncio.run(_seed_two_bots(mb_db))

    async def _run():
        async with mb_db() as db:
            bot = await plugin_routes._resolve_bot_for_user(db, 1, "botB-im-bot")
            import models as _pm  # noqa: PLC0415 - 插件 models（sys.path 已含插件目录）
            rows = (await db.execute(
                select(_pm.WeChatILinkBinding).where(
                    _pm.WeChatILinkBinding.user_id == 1,
                    _pm.WeChatILinkBinding.bot_account_id == bot,
                    _pm.WeChatILinkBinding.enabled.is_(True),
                ))).scalars().all()
            for row in rows:
                row.character_id = 101
            await db.commit()

    asyncio.run(_run())
    M = _plugin_models()

    async def _rows():
        async with mb_db() as db:
            return {b.bot_account_id: b.character_id for b in
                    (await db.execute(select(M.WeChatILinkBinding))).scalars().all()}

    got = asyncio.run(_rows())
    assert got == {"botA-im-bot": 101, "botB-im-bot": 101}  # 只迁 botB；botA 原值保持


def test_resolve_bot_ambiguous_requires_explicit(mb_db):
    """多 bot 时缺省 bot → 400 语义（HTTPException）；唯一 bot 自动归属。"""
    import routes as plugin_routes
    from fastapi import HTTPException

    asyncio.run(_seed_two_bots(mb_db))

    async def _run():
        async with mb_db() as db:
            with pytest.raises(HTTPException) as ei:
                await plugin_routes._resolve_bot_for_user(db, 1, "")
            assert ei.value.status_code == 400
            # 删一个 bot 行后唯一 → 自动归属
            async with mb_db() as db2:
                M = _plugin_models()
                row = (await db2.execute(select(M.WeChatILinkBinding).where(
                    M.WeChatILinkBinding.bot_account_id == "botB-im-bot"))).scalar_one()
                await db2.delete(row)
                await db2.commit()
            async with mb_db() as db:
                return await plugin_routes._resolve_bot_for_user(db, 1, "")

    assert asyncio.run(_run()) == "botA-im-bot"


def test_relay_bot_precise_and_legacy_compat_window(mb_db, monkeypatch):
    """relay：真实 bot id 精确定位；旧网关（无 bot→default）走兼容窗（wxuser 唯一命中、多行不回落）。"""
    asyncio.run(_seed_two_bots(mb_db))
    _patch_reply(monkeypatch)
    client = _client()

    # 真实 bot id 精确定位
    r = client.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi", "msg_id": "m1",
                                     "bot_account_id": "botA-im-bot"},
                    headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 200 and r.json()["ok"] is True

    # 旧网关缺省 default：wx1 有两行 → 不回落（防串台）
    r2 = client.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi", "msg_id": "m2"},
                     headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r2.json()["code"] == "no_binding"

    # 旧网关缺省 default：wxuser 唯一 → 兼容窗命中（模拟存量修正后单 bot 部署）
    M = _plugin_models()

    async def _drop_b():
        async with mb_db() as db:
            row = (await db.execute(select(M.WeChatILinkBinding).where(
                M.WeChatILinkBinding.bot_account_id == "botB-im-bot"))).scalar_one()
            await db.delete(row)
            await db.commit()

    asyncio.run(_drop_b())
    r3 = client.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi", "msg_id": "m3"},
                     headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r3.status_code == 200 and r3.json()["ok"] is True  # 兼容窗命中 botA 行


def test_bind_uses_confirmed_ilink_bot_id_normalized(mb_db):
    """/bind 落库：confirmed 的 ilink_bot_id（@im.bot 形态）归一化为 -im-bot 稳定键。"""
    import routes as plugin_routes

    async def _run():
        async with mb_db() as db:
            return await plugin_routes._save_binding(
                db, user_id=1, character_id=101,
                ilink_user_id="wx-new", ilink_bot_id="94030e7e091c@im.bot",
                bot_token="tok-123456", baseurl="https://ilinkai.weixin.qq.com")

    row = asyncio.run(_run())
    assert row.bot_account_id == "94030e7e091c-im-bot"
    assert row.ilink_bot_id == "94030e7e091c@im.bot"  # 原值仍记录在记录列
