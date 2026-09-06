# -*- coding: utf-8 -*-
"""微信 bot 解绑联动同步修复（AMBRACE_微信解绑联动同步修复_交接_Codex致Zcode_20260906，2026-09-06）测试。

问题（真机实证）：App 渠道卡解绑走 DELETE /api/v1/channels/{channel}/bindings/{bot}（v2 flag 开）只删
channel_bindings 行，不联动微信插件表 wechat_ilink_bindings → 该 bot 仍被 relay 路由（插件行
enabled=1）、且不再出现在 available-bots 列表（误以为需重扫）。openclaw 网关登录态仍在，实际无需扫码。

覆盖（本批）：
1. DELETE wechat bindings/{bot}（v2）后：
   - channel_bindings 行删；
   - 插件行 (tenant, bot) enabled=0、token 清空、行保留（对齐 _clear_binding 语义）；
   - relay no_binding（不再路由该 bot）；
   - available-bots 重新可见该 bot（重绑可经 App/bind-available 直接做，无需重扫）；
2. PUT 换绑/保存：已停用 bot 属本租户 → 直接重绑（复用既有行，重启用，无需扫码）；
   PUT 手输任意未登录/他租户 bot → 404（幂等口径与 bind-available 统一，且不残留半绑定行）；
3. 其它渠道（douyin）解绑不受联动影响（无插件联动面=原行为）。
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
from app.models.channel import ChannelBinding
from app.models.character import AICharacter
from app.models.user import User

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"
_PLUGIN_DIR_STR = str(_PLUGIN_DIR)
_SECRET = "unbind-sync-test-secret-000"
_BOT = "botA-im-bot"
_CB_WS_URL = "/api/v1/channels/wechat/bindings/{bot}"
_CB_DY_URL = "/api/v1/channels/douyin/bindings/{bot}"
_RELAY_URL = "/api/v1/plugins/bridge/wechat-relay"
_AVAILABLE_URL = "/api/v1/plugins/wechat_ilink/available-bots"


@pytest.fixture()
def ws_plugin():
    import sys as _sys

    if _PLUGIN_DIR_STR not in _sys.path:
        _sys.path.insert(0, _PLUGIN_DIR_STR)
    from app.plugins import registry

    if not registry.load_plugin_dir(_PLUGIN_DIR):
        raise RuntimeError("wechat_ilink plugin failed to load")
    # 确保 hook 注册进渠道条目（main.py 加载期 register_channel_binding_hooks 已跑）
    yield
    from app.plugins import registry as _reg
    from app.providers import registry as prov_reg

    prov_reg.unregister_providers_for_source("wechat_ilink")
    _reg._loaded.pop("wechat_ilink", None)
    _reg._db_config.pop("wechat_ilink", None)
    _reg._enabled.pop("wechat_ilink", None)


@pytest.fixture()
def ws_db(ws_plugin, tmp_path, monkeypatch):
    """独立临时 SQLite + patch async_session_factory（不写生产库）。"""
    monkeypatch.setenv("AMBRACE_SECRET_KEY", "wechat-ilink-test-secret-000000000000000000000001")
    monkeypatch.setenv("WECHAT_ILINK_BRIDGE_SECRET", _SECRET)
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
        async with factory() as db:
            db.add(User(id=1, username="main", nickname="m", is_admin=True))
            db.add(User(id=2, username="main2", nickname="m2", is_admin=True))
            db.add(AICharacter(id=101, user_id=1, name="小慧"))
            db.add(AICharacter(id=103, user_id=1, name="小橙"))
            await db.commit()

    asyncio.run(_seed())
    yield factory
    asyncio.run(engine.dispose())


def _flag_on(monkeypatch):
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", True)


def _plugin_models():
    from app.plugins import registry

    return registry._loaded["wechat_ilink"]["module"].models


def _fake_accounts_dir(tmp_path, entries):
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


def _make_app():
    from app.api import plugins as plugins_api
    from app.auth.deps import get_current_user_id
    from app.plugins import registry

    app = FastAPI()
    app.include_router(cb_api.router)
    app.include_router(plugins_api.router)
    router_obj = registry._loaded["wechat_ilink"].get("router")
    assert router_obj is not None
    app.include_router(router_obj)
    current = {"uid": 1}
    app.dependency_overrides[get_current_user_id] = lambda: current["uid"]
    return TestClient(app), current


async def _seed_bound_wechat(factory, *, enabled=True, token="enc-token"):
    """种子：tenant1 绑定 botA（插件行 + channel_bindings 行）。"""
    M = _plugin_models()
    async with factory() as db:
        db.add(M.WeChatILinkBinding(
            user_id=1, tenant_id=1, bot_account_id=_BOT, character_id=101,
            ilink_user_id="wx1", ilink_bot_id=_BOT, bot_token_enc=token,
            baseurl="https://ilinkai.weixin.qq.com", enabled=enabled))
        if enabled:
            db.add(ChannelBinding(channel="wechat", tenant_id=1, owner_user_id=1,
                                  bot_account_id=_BOT, character_id=101, enabled=True))
        await db.commit()


async def _plugin_row(factory):
    M = _plugin_models()
    async with factory() as db:
        return (await db.execute(select(M.WeChatILinkBinding).where(
            M.WeChatILinkBinding.bot_account_id == _BOT))).scalars().first()


async def _channel_binding_row(factory, channel="wechat", bot=_BOT, tenant=None):
    async with factory() as db:
        q = (select(ChannelBinding).where(
            ChannelBinding.channel == channel,
            ChannelBinding.bot_account_id == bot,
        ))
        if tenant is not None:
            q = q.where(ChannelBinding.tenant_id == int(tenant))
        return (await db.execute(q)).scalars().first()


# ------------------------------------------------------------------ 1. DELETE 解绑联动

def test_delete_wechat_binding_syncs_plugin_row(ws_db, monkeypatch):
    """DELETE wechat bindings/{bot}（v2）→ channel_bindings 行删 + 插件行 enabled=0/token 清空/行保留。"""
    _flag_on(monkeypatch)
    asyncio.run(_seed_bound_wechat(ws_db, enabled=True, token="enc-token"))
    c, _ = _make_app()

    r = c.delete(_CB_WS_URL.format(bot=_BOT))
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True

    row = asyncio.run(_plugin_row(ws_db))
    assert row is not None, "插件行应保留（不物理删）"
    assert row.enabled is False
    assert row.bot_token_enc == ""
    assert row.character_id == 101  # 历史保留
    assert asyncio.run(_channel_binding_row(ws_db)) is None, "channel_bindings 行应删除"


def test_delete_wechat_binding_relay_no_binding_and_available_reappears(ws_db, tmp_path, monkeypatch):
    """解绑后 relay no_binding + available-bots 重新可见该 bot（重绑无需重扫）。"""
    _flag_on(monkeypatch)
    state = _fake_accounts_dir(tmp_path, [{"account_id": _BOT, "user_id": "wx1", "saved_at": "2026-09-06"}])
    monkeypatch.setenv("MULTIBOT_WX_DIR", state)
    asyncio.run(_seed_bound_wechat(ws_db, enabled=True, token="enc-token"))
    c, _ = _make_app()

    # 未解绑前：relay 命中、available 不可见
    pre_relay = c.post(_RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi", "msg_id": "m-pre",
                                         "bot_account_id": _BOT},
                       headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert pre_relay.status_code == 200 and pre_relay.json()["ok"] is True
    pre_avail = c.get(_AVAILABLE_URL).json()["items"]
    assert _BOT not in [i["bot_account_id"] for i in pre_avail]

    r = c.delete(_CB_WS_URL.format(bot=_BOT))
    assert r.status_code == 200, r.text

    # relay：无 enabled 绑定 → no_binding
    post_relay = c.post(_RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi", "msg_id": "m-post",
                                          "bot_account_id": _BOT},
                        headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert post_relay.status_code == 200
    assert post_relay.json()["code"] == "no_binding"
    assert post_relay.json()["ok"] is False

    # available：插件行停用后 bot 回到可添加列表
    post_avail = c.get(_AVAILABLE_URL).json()["items"]
    assert _BOT in [i["bot_account_id"] for i in post_avail]


def test_delete_wechat_then_bind_available_rebind_no_scan(ws_db, tmp_path, monkeypatch):
    """解绑后可经 bind-available 直接重绑（无需重扫）：复用既有行，重启用。"""
    _flag_on(monkeypatch)
    state = _fake_accounts_dir(tmp_path, [{"account_id": _BOT, "user_id": "wx1", "saved_at": "2026-09-06"}])
    monkeypatch.setenv("MULTIBOT_WX_DIR", state)
    asyncio.run(_seed_bound_wechat(ws_db, enabled=True, token="enc-token"))
    c, _ = _make_app()

    assert c.delete(_CB_WS_URL.format(bot=_BOT)).status_code == 200
    r = c.post("/api/v1/plugins/wechat_ilink/bind-available",
               json={"bot_account_id": _BOT, "character_id": 101})
    assert r.status_code == 200, r.text

    row = asyncio.run(_plugin_row(ws_db))
    assert row.enabled is True
    assert row.character_id == 101
    cb = asyncio.run(_channel_binding_row(ws_db))
    assert cb is not None and cb.character_id == 101 and cb.enabled is True


# ------------------------------------------------------------------ 2. PUT 换绑/保存幂等口径

def test_put_reactivates_disabled_bot_rebind_no_scan(ws_db, tmp_path, monkeypatch):
    """PUT 已停用 bot 到本租户=可重绑（复用既有行，无需扫码 / 不依赖 available 列表）。"""
    _flag_on(monkeypatch)
    # openclaw 目录为空（该 bot 不 in available）——证明 PUT 复用既有行，不依赖 available 列表
    monkeypatch.setenv("MULTIBOT_WX_DIR", _fake_accounts_dir(tmp_path, []))
    asyncio.run(_seed_bound_wechat(ws_db, enabled=False, token=""))  # 已停用（模拟解绑后）+ 无 channel_bindings 行
    c, _ = _make_app()

    r = c.put(_CB_WS_URL.format(bot=_BOT), json={"character_id": 101})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    row = asyncio.run(_plugin_row(ws_db))
    assert row.enabled is True, "停用 bot 重绑后应重新启用"
    assert row.character_id == 101
    assert row.id is not None
    cb = asyncio.run(_channel_binding_row(ws_db))
    assert cb is not None and cb.character_id == 101 and cb.enabled is True


def test_put_disabled_bot_switch_role_preserves_identity(ws_db, tmp_path, monkeypatch):
    """PUT 换绑停用 bot 到另一角色：复用行，enabled=True、角色切换、微信身份保留。"""
    _flag_on(monkeypatch)
    monkeypatch.setenv("MULTIBOT_WX_DIR", _fake_accounts_dir(tmp_path, []))
    asyncio.run(_seed_bound_wechat(ws_db, enabled=False, token=""))
    c, _ = _make_app()

    r = c.put(_CB_WS_URL.format(bot=_BOT), json={"character_id": 103})
    assert r.status_code == 200, r.text
    row = asyncio.run(_plugin_row(ws_db))
    assert row.enabled is True
    assert row.character_id == 103
    assert row.ilink_user_id == "wx1"  # 微信身份保留（区别于解绑）
    cb = asyncio.run(_channel_binding_row(ws_db))
    assert cb is not None and cb.character_id == 103


def test_put_unavailable_bot_404_and_no_residual_row(ws_db, tmp_path, monkeypatch):
    """PUT 手输任意/未登录/他租户 bot → 404；且不残留半绑定 channel_bindings 行（整事务回滚）。"""
    _flag_on(monkeypatch)
    monkeypatch.setenv("MULTIBOT_WX_DIR", _fake_accounts_dir(tmp_path, []))  # 无任何登录 bot
    asyncio.run(_seed_bound_wechat(ws_db, enabled=False, token=""))  # botA 停用（属本租户）

    async def _seed_other():
        # 另一租户（user2）绑定 botB，tenant2
        M = _plugin_models()
        async with ws_db() as db:
            db.add(M.WeChatILinkBinding(
                user_id=2, tenant_id=2, bot_account_id="botB-im-bot", character_id=101,
                ilink_user_id="wx2", bot_token_enc="x", baseurl="https://ilinkai.weixin.qq.com", enabled=True))
            db.add(ChannelBinding(channel="wechat", tenant_id=2, owner_user_id=2,
                                  bot_account_id="botB-im-bot", character_id=101, enabled=True))
            await db.commit()

    asyncio.run(_seed_other())
    c, _ = _make_app()

    # 手输任意 id（无行、不在 available）→ 404
    r = c.put(_CB_WS_URL.format(bot="random-id"), json={"character_id": 101})
    assert r.status_code == 404, r.text

    # 他租户 botB（属 tenant2）→ 404（不越权绑他人 bot）
    r2 = c.put(_CB_WS_URL.format(bot="botB-im-bot"), json={"character_id": 101})
    assert r2.status_code == 404, r2.text

    # 不残留半绑定 channel_bindings 行（本租户 tenant1 不新增；tenant2 存量行保持不动）
    assert asyncio.run(_channel_binding_row(ws_db, bot="random-id")) is None
    assert asyncio.run(_channel_binding_row(ws_db, bot="botB-im-bot", tenant=1)) is None
    assert asyncio.run(_channel_binding_row(ws_db, bot="botB-im-bot", tenant=2)) is not None


def test_put_wechat_does_not_overwrite_other_bot(ws_db, tmp_path, monkeypatch):
    """PUT 换绑只影响目标 bot：另一 bot 行不受影响。"""
    _flag_on(monkeypatch)
    monkeypatch.setenv("MULTIBOT_WX_DIR", _fake_accounts_dir(tmp_path, []))

    async def _seed():
        M = _plugin_models()
        async with ws_db() as db:
            db.add(M.WeChatILinkBinding(
                user_id=1, tenant_id=1, bot_account_id=_BOT, character_id=101,
                ilink_user_id="wx1", bot_token_enc="enc-a", baseurl="https://ilinkai.weixin.qq.com", enabled=True))
            db.add(M.WeChatILinkBinding(
                user_id=1, tenant_id=1, bot_account_id="botB-im-bot", character_id=103,
                ilink_user_id="wx1", bot_token_enc="enc-b", baseurl="https://ilinkai.weixin.qq.com", enabled=True))
            db.add(ChannelBinding(channel="wechat", tenant_id=1, owner_user_id=1,
                                  bot_account_id=_BOT, character_id=101, enabled=True))
            await db.commit()

    asyncio.run(_seed())
    c, _ = _make_app()

    r = c.put(_CB_WS_URL.format(bot=_BOT), json={"character_id": 103})
    assert r.status_code == 200, r.text

    async def _rows():
        M = _plugin_models()
        async with ws_db() as db:
            return {b.bot_account_id: (b.character_id, b.enabled) for b in
                    (await db.execute(select(M.WeChatILinkBinding))).scalars().all()}

    rows = asyncio.run(_rows())
    assert rows[_BOT] == (103, True)
    assert rows["botB-im-bot"] == (103, True)  # 另一 bot 未被触达（角色保持原值、仍启用）


# ------------------------------------------------------------------ 3. 其它渠道不受联动影响

def test_delete_douyin_binding_not_affected(ws_db, monkeypatch):
    """douyin 解绑：仅删 channel_bindings 行，无插件联动面（原行为）。"""
    _flag_on(monkeypatch)

    async def _seed():
        async with ws_db() as db:
            db.add(ChannelBinding(channel="douyin", tenant_id=1, owner_user_id=1,
                                  bot_account_id="default", character_id=101, enabled=True))
            await db.commit()

    asyncio.run(_seed())
    c, _ = _make_app()

    r = c.delete(_CB_DY_URL.format(bot="default"))
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert asyncio.run(_channel_binding_row(ws_db, channel="douyin", bot="default")) is None
