# -*- coding: utf-8 -*-
"""App 添加未绑定 ClawBot（2026-09-06）后端测试。

- available 过滤：剔除已绑（任意租户）/缺 userId；纯函数；
- GET /available-bots：root-only（子账号 403）、只列未绑定；
- POST /bind-available：落插件行（无 token、wxuser=bot userId）+ channel_bindings upsert；
  幂等；不可用 bot 404；非 root 403。
"""
import asyncio
import importlib
import json
import os
import pathlib
import sys

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

import pathlib as _pl

_PLUGIN_DIR = _pl.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"
_PLUGIN_DIR_STR = str(_PLUGIN_DIR)


@pytest.fixture()
def ab_plugin():
    if _PLUGIN_DIR_STR not in sys.path:
        sys.path.insert(0, _PLUGIN_DIR_STR)
    from app.plugins import registry

    if not registry.load_plugin_dir(_PLUGIN_DIR):
        raise RuntimeError("wechat_ilink plugin failed to load")
    yield
    from app.plugins import registry as _reg
    from app.providers import registry as prov_reg

    prov_reg.unregister_providers_for_source("wechat_ilink")
    _reg._loaded.pop("wechat_ilink", None)
    _reg._db_config.pop("wechat_ilink", None)
    _reg._enabled.pop("wechat_ilink", None)


@pytest.fixture()
def ab_db(ab_plugin, tmp_path, monkeypatch):
    monkeypatch.setenv("AMBRACE_SECRET_KEY", "wechat-ilink-test-secret-000000000000000000000001")
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

        async with factory() as db:
            db.add(User(id=1, username="main", nickname="m", is_admin=True))
            db.add(User(id=3, username="sub", nickname="s", parent_id=1, is_admin=False))
            db.add(AICharacter(id=101, user_id=1, name="小慧"))
            await db.commit()

    asyncio.run(_seed())
    yield factory
    asyncio.run(engine.dispose())


def _fake_accounts_dir(tmp_path: object, entries: list[dict]) -> str:
    """构造模拟 openclaw accounts 目录；entries=[{account_id, user_id, saved_at}]。"""
    d = pathlib.Path(str(tmp_path)) / "openclaw-weixin"
    (d / "accounts").mkdir(parents=True, exist_ok=True)
    (d / "accounts.json").write_text(json.dumps([e["account_id"] for e in entries]), encoding="utf-8")
    for e in entries:
        (d / "accounts" / f"{e['account_id']}.json").write_text(json.dumps({
            "token": f"{e['account_id'].replace('-im-bot', '')}@im.bot:x",
            "savedAt": e.get("saved_at", ""), "baseUrl": "https://ilinkai.weixin.qq.com",
            "userId": e.get("user_id", ""),
        }), encoding="utf-8")
    return str(d)


def _make_app(factory):
    from app.api import plugins as plugins_api
    from app.auth.deps import get_current_user_id
    from app.plugins import registry

    app = FastAPI()
    app.include_router(plugins_api.router)
    router_obj = registry._loaded["wechat_ilink"].get("router")
    assert router_obj is not None
    app.include_router(router_obj)
    current = {"uid": 1}
    app.dependency_overrides[get_current_user_id] = lambda: current["uid"]
    return TestClient(app), current


# ---------------- 纯函数 ----------------

def test_filter_available_pures():
    import sys

    if _PLUGIN_DIR_STR not in sys.path:
        sys.path.insert(0, _PLUGIN_DIR_STR)
    import available_bots

    accounts = [
        {"account_id": "botA-im-bot", "user_id": "u1@im.wechat", "saved_at": "s1"},
        {"account_id": "botB-im-bot", "user_id": "u2@im.wechat", "saved_at": "s2"},
        {"account_id": "botC-im-bot", "user_id": "", "saved_at": "s3"},  # 缺 userId → 剔除
    ]
    out = available_bots.filter_available(accounts, {"botB-im-bot"})  # botB 已绑
    assert [a["account_id"] for a in out] == ["botA-im-bot"]


# ---------------- 端点 ----------------

def test_available_bots_endpoint_root_only_and_filter(ab_db, tmp_path, monkeypatch):
    state = _fake_accounts_dir(tmp_path, [
        {"account_id": "newbot-im-bot", "user_id": "newuser@im.wechat", "saved_at": "2026-09-06"},
        {"account_id": "boundbot-im-bot", "user_id": "bound@im.wechat", "saved_at": "2026-09-06"},
        {"account_id": "nouser-im-bot", "user_id": "", "saved_at": "2026-09-06"},
    ])
    monkeypatch.setenv("MULTIBOT_WX_DIR", state)  # 供 available_bots 默认路径外注入

    async def _seed_bound():
        M = importlib.import_module("models")
        async with ab_db() as db:
            db.add(M.WeChatILinkBinding(
                user_id=1, tenant_id=1, bot_account_id="boundbot-im-bot", character_id=101,
                ilink_user_id="bound@im.wechat", bot_token_enc="x",
                baseurl="https://ilinkai.weixin.qq.com", enabled=True))
            await db.commit()

    asyncio.run(_seed_bound())
    c, current = _make_app(ab_db)

    # root：只列未绑定且有 userId 的
    r = c.get("/api/v1/plugins/wechat_ilink/available-bots")
    assert r.status_code == 200, r.text
    ids = [i["bot_account_id"] for i in r.json()["items"]]
    assert "newbot-im-bot" in ids
    assert "boundbot-im-bot" not in ids  # 已绑剔除
    assert "nouser-im-bot" not in ids  # 缺 userId 剔除

    # 子账号 403
    current["uid"] = 3
    r2 = c.get("/api/v1/plugins/wechat_ilink/available-bots")
    assert r2.status_code == 403


def test_bind_available_persists_binding_and_channel_row(ab_db, tmp_path, monkeypatch):
    from app.agent import loop as agent_loop

    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", True)
    state = _fake_accounts_dir(tmp_path, [
        {"account_id": "newbot-im-bot", "user_id": "newuser@im.wechat", "saved_at": "2026-09-06"},
    ])
    monkeypatch.setenv("MULTIBOT_WX_DIR", state)
    c, current = _make_app(ab_db)

    r = c.post("/api/v1/plugins/wechat_ilink/bind-available",
               json={"bot_account_id": "newbot-im-bot", "character_id": 101})
    assert r.status_code == 200, r.text
    assert r.json()["bot_account_id"] == "newbot-im-bot"

    async def _verify():
        M = importlib.import_module("models")
        from app.models.channel import ChannelBinding

        async with ab_db() as db:
            wb = (await db.execute(select(M.WeChatILinkBinding).where(
                M.WeChatILinkBinding.bot_account_id == "newbot-im-bot"))).scalar_one()
            cb = (await db.execute(select(ChannelBinding).where(
                ChannelBinding.bot_account_id == "newbot-im-bot"))).scalar_one()
            return (wb.ilink_user_id, wb.bot_token_enc, wb.tenant_id,
                    cb.character_id, cb.tenant_id, cb.enabled)

    uid, tok, tenant, cb_char, cb_tenant, cb_enabled = asyncio.run(_verify())
    assert uid == "newuser@im.wechat"  # wxuser=bot userId（relay 路由键）
    assert tok == ""  # 无 token（凭据在网关侧，relay 不依赖）
    assert tenant == 1 and cb_tenant == 1 and cb_char == 101 and cb_enabled is True

    # 幂等：重复绑定不新增行（upsert）
    r2 = c.post("/api/v1/plugins/wechat_ilink/bind-available",
                json={"bot_account_id": "newbot-im-bot", "character_id": 101})
    assert r2.status_code == 200

    async def _count():
        M = importlib.import_module("models")
        async with ab_db() as db:
            return len((await db.execute(select(M.WeChatILinkBinding))).scalars().all())

    assert asyncio.run(_count()) == 1


def test_bind_available_rejects_unavailable_and_subaccount(ab_db, tmp_path, monkeypatch):
    from app.agent import loop as agent_loop

    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", True)
    state = _fake_accounts_dir(tmp_path, [
        {"account_id": "newbot-im-bot", "user_id": "newuser@im.wechat", "saved_at": "2026-09-06"},
    ])
    monkeypatch.setenv("MULTIBOT_WX_DIR", state)
    c, current = _make_app(ab_db)

    # 手输任意 id（不在 available）→ 404
    r = c.post("/api/v1/plugins/wechat_ilink/bind-available",
               json={"bot_account_id": "random-id", "character_id": 101})
    assert r.status_code == 404

    # 子账号 → 403
    current["uid"] = 3
    r2 = c.post("/api/v1/plugins/wechat_ilink/bind-available",
                json={"bot_account_id": "newbot-im-bot", "character_id": 101})
    assert r2.status_code == 403

