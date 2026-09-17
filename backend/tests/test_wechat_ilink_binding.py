# -*- coding: utf-8 -*-
"""wechat_ilink PR2 绑定/解绑测试：

- **双路契约（channel_binding_v2 自 2026-09-17 起默认开）**：flag 开（默认）→ /bind、/unbind
  走 channel_bindings 新表（租户化，ChannelBindingService），**不再回写旧全局
  config.allowed_character_ids**；flag 关（显式回落）→ 旧「内核完整 PUT」路径。
- 旧路径裁决语义（/api/v1/plugins/wechat_ilink 完整 PUT，与抖音同语义）：子账号 403、
  跨家庭 403、多角色 400、家庭单选成功、换绑 400、空数组解绑成功。
- 插件自有绑定表：稳定 ilink_user_id 重新扫码 → 轮换凭据**不新增行**（P1-3）；
  解绑清凭据停状态；baseurl 非白名单被拒（P3-2 SSRF）。
- 2026-09-17 批次一任务4：断言从旧全局 config 迁到新表 + 插件行（见
  test_unbind_route_clears_binding）。
"""
import asyncio
import os
import pathlib

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import plugins as plugins_api
from app.auth.deps import get_current_user_id
from app.plugins import registry
from app.providers import registry as prov_reg
from app.application import permission_service as perm

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"
_SECRET_KEY = "wechat-ilink-test-secret-000000000000000000000001"


# 快测档（2026-09-12）：本文件是重量级/集成型用例（每例起一次临时库，约 3s/例），打 slow 标记。
# 全量默认照跑；日常开发用 pytest -m "not slow" 跳过本档（见 docs/engineering-protocol.md 十八）。
pytestmark = pytest.mark.slow

def _load_plugin():
    """经内核插件加载器装配 wechat_ilink（渠道注册 + 模型进 Base.metadata + router）。"""
    if not registry.load_plugin_dir(_PLUGIN_DIR):
        raise RuntimeError("wechat_ilink plugin failed to load")


@pytest.fixture()
def wc_plugin():
    _load_plugin()
    yield
    prov_reg.unregister_providers_for_source("wechat_ilink")
    registry._loaded.pop("wechat_ilink", None)
    registry._db_config.pop("wechat_ilink", None)
    registry._enabled.pop("wechat_ilink", None)


@pytest.fixture()
def wc_db(monkeypatch, wc_plugin, tmp_path):
    """临时 SQLite 文件库：patch 各模块绑定的 async_session_factory（不触碰 backend/data）。"""
    monkeypatch.setenv("AMBRACE_SECRET_KEY", _SECRET_KEY)
    tmp = str(tmp_path)
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
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
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(perm, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_admin_cache():
    """清理 permission_service._admin_cache，避免跨用例污染/缓存命中干扰。"""
    perm._admin_cache.clear()
    yield
    perm._admin_cache.clear()


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(plugins_api.router)  # 内核 PUT /api/v1/plugins/{name}
    app.include_router(registry._loaded["wechat_ilink"]["router"])  # 插件 /bind //unbind//status
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _add_user(factory, uid, username, parent_id=None, is_admin=False):
    from app.models.user import User
    async with factory() as db:
        db.add(User(id=uid, username=username, nickname=f"n{uid}", parent_id=parent_id, is_admin=is_admin))
        await db.commit()


async def _add_char(factory, cid, owner_id, name="角色"):
    from app.models.character import AICharacter
    async with factory() as db:
        db.add(AICharacter(id=cid, user_id=owner_id, name=name))
        await db.commit()


async def _bind_config(factory) -> str:
    """读取 registry 内存中 wechat_ilink 的 allowed_character_ids 绑定串。"""
    return registry._db_config.get("wechat_ilink", {}).get("allowed_character_ids", "")


def _get_bindings(factory):
    """读取插件自有绑定表全部行（wechat_ilink_bindings）。"""
    model = registry._loaded["wechat_ilink"]["module"].models.WeChatILinkBinding

    async def _q():
        async with factory() as db:
            return (await db.execute(select(model))).scalars().all()

    return asyncio.run(_q())


# ------------------------------------------------------------------ 内核完整 PUT 路径裁决（与抖音同语义）

def test_sub_account_cannot_bind_403(wc_db):
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    asyncio.run(_add_user(wc_db, 2, "sub", parent_id=1, is_admin=False))
    client = _make_client(2)
    r = client.put("/api/v1/plugins/wechat_ilink", json={"config": {"allowed_character_ids": [101]}})
    assert r.status_code == 403, r.text


def test_cross_family_bind_403(wc_db):
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    asyncio.run(_add_user(wc_db, 3, "other_main", is_admin=True))
    asyncio.run(_add_char(wc_db, 201, 3, "他人角色"))
    client = _make_client(1)
    r = client.put("/api/v1/plugins/wechat_ilink", json={"config": {"allowed_character_ids": [201]}})
    assert r.status_code == 403, r.text


def test_multi_bind_400(wc_db):
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    client = _make_client(1)
    r = client.put("/api/v1/plugins/wechat_ilink", json={"config": {"allowed_character_ids": [101, 102]}})
    assert r.status_code == 400, r.text


def test_family_single_bind_success(wc_db):
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    asyncio.run(_add_char(wc_db, 101, 1, "我的角色"))
    client = _make_client(1)
    r = client.put("/api/v1/plugins/wechat_ilink", json={"config": {"allowed_character_ids": [101]}})
    assert r.status_code == 200, r.text
    assert asyncio.run(_bind_config(wc_db)) == "101"


def test_same_family_rebind_other_400(wc_db):
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    asyncio.run(_add_char(wc_db, 101, 1, "角色A"))
    asyncio.run(_add_char(wc_db, 102, 1, "角色B"))
    client = _make_client(1)
    r = client.put("/api/v1/plugins/wechat_ilink", json={"config": {"allowed_character_ids": [101]}})
    assert r.status_code == 200, r.text
    r2 = client.put("/api/v1/plugins/wechat_ilink", json={"config": {"allowed_character_ids": [102]}})
    assert r2.status_code == 400, r2.text


def test_unbind_empty_success(wc_db):
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    asyncio.run(_add_char(wc_db, 101, 1, "角色A"))
    client = _make_client(1)
    r = client.put("/api/v1/plugins/wechat_ilink", json={"config": {"allowed_character_ids": [101]}})
    assert r.status_code == 200, r.text
    assert asyncio.run(_bind_config(wc_db)) == "101"
    r2 = client.put("/api/v1/plugins/wechat_ilink", json={"config": {"allowed_character_ids": []}})
    assert r2.status_code == 200, r2.text
    assert asyncio.run(_bind_config(wc_db)) == ""


# ------------------------------------------------------------------ 插件自有绑定表（P1-3 轮换 / 解绑 / SSRF）

def test_bind_route_rotates_credentials_no_new_row(wc_db):
    """稳定 ilink_user_id 重新扫码 → 轮换 token/bot_id/baseurl，不新增第二条绑定（P1-3）。"""
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    asyncio.run(_add_char(wc_db, 101, 1, "角色A"))
    client = _make_client(1)

    r1 = client.post("/api/v1/plugins/wechat_ilink/bind", json={
        "character_id": 101, "bot_token": "tok-1", "baseurl": "https://base.weixin.qq.com",
        "ilink_user_id": "wx_uid_stable_001", "ilink_bot_id": "bot-1"})
    assert r1.status_code == 200, r1.text
    rows1 = _get_bindings(wc_db)
    assert len(rows1) == 1

    # 同一微信（稳定 ilink_user_id）重新扫码：真机确认（2026-09-06 多 ClawBot 前置场景 B）
    # ilink_bot_id 跨重扫不变 → bot_account_id 不变 → token/baseurl 轮换仍只有 1 行
    r2 = client.post("/api/v1/plugins/wechat_ilink/bind", json={
        "character_id": 101, "bot_token": "tok-2", "baseurl": "https://base2.weixin.qq.com",
        "ilink_user_id": "wx_uid_stable_001", "ilink_bot_id": "bot-1"})
    assert r2.status_code == 200, r2.text
    rows2 = _get_bindings(wc_db)
    assert len(rows2) == 1
    row = rows2[0]
    assert row.ilink_user_id == "wx_uid_stable_001"
    assert row.ilink_bot_id == "bot-1"  # 重扫 bot id 不变（稳定键）
    assert row.baseurl == "https://base2.weixin.qq.com"
    # 密文 ≠ 明文；旧 token 已被覆盖
    assert row.bot_token_enc != "tok-2"
    assert row.bot_token_enc != rows1[0].bot_token_enc
    assert row.enabled is True


def test_bind_route_rejects_untrusted_baseurl(wc_db):
    """P3-2 SSRF：confirmed baseurl 非微信官方域 → 拒绑（400）。"""
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    asyncio.run(_add_char(wc_db, 101, 1, "角色A"))
    client = _make_client(1)
    r = client.post("/api/v1/plugins/wechat_ilink/bind", json={
        "character_id": 101, "bot_token": "tok", "baseurl": "https://evil.example.com",
        "ilink_user_id": "wx_uid_evil", "ilink_bot_id": "bot-evil"})
    assert r.status_code == 400, r.text
    assert len(_get_bindings(wc_db)) == 0


def test_unbind_route_clears_binding(wc_db, monkeypatch):
    """解绑（channel_binding_v2 默认开）：新表落绑定 + 插件行停用清凭据。

    2026-09-17 批次一任务4（契约漂移修正）：/bind、/unbind 走 channel_bindings 新表后，
    旧断言 registry._db_config["allowed_character_ids"] 已不再被写，改为断言新表与插件行。
    """
    import app.agent.loop as _loop
    monkeypatch.setitem(_loop.AGENT_FLAGS, "channel_binding_v2", True)  # 锁死被测路径
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    asyncio.run(_add_char(wc_db, 101, 1, "角色A"))
    client = _make_client(1)

    r = client.post("/api/v1/plugins/wechat_ilink/bind", json={
        "character_id": 101, "bot_token": "tok", "baseurl": "https://base.weixin.qq.com",
        "ilink_user_id": "wx_uid_unbind", "ilink_bot_id": "bot-1"})
    assert r.status_code == 200, r.text
    # 新表：bind 后 (tenant, 'wechat', bot="bot-1") → 101 且 enabled
    tenant = _tenant(wc_db, 1)
    cb = _get_channel_bindings(wc_db)
    assert len(cb) == 1
    assert (cb[0].channel, cb[0].tenant_id, cb[0].bot_account_id) == ("wechat", tenant, "bot-1")
    assert cb[0].character_id == 101
    assert cb[0].enabled is True

    r2 = client.post("/api/v1/plugins/wechat_ilink/unbind", json={"character_id": 101})
    assert r2.status_code == 200, r2.text
    # 插件绑定行停用 + 凭据清空（token 解绑即删，P0-4）
    rows = _get_bindings(wc_db)
    assert len(rows) == 1
    assert rows[0].enabled is False
    assert rows[0].bot_token_enc == ""
    # 新表：解绑后该 bot 的绑定行被物理删除（2026-09-17 修复：v2 分支原按 DEFAULT_BOT 匹配，
    # 多 bot 归一化键（"bot-1"）的行删不掉 → 解绑实际不生效；现按解析出的目标 bot 删，
    # 并在此锁定该不变量，防回归）
    assert _get_channel_bindings(wc_db) == []


def _tenant(factory, user_id: int) -> int:
    """租户键（家庭 root user_id），与 channel_bindings 写入同源 tenant_scope。"""
    from app.application.tenant_scope import resolve_tenant

    async def _q():
        async with factory() as db:
            return await resolve_tenant(db, user_id)

    return asyncio.run(_q())


def _get_channel_bindings(factory, channel: str = "wechat"):
    """读内核 channel_bindings 新表（channel_binding_v2 契约面）。"""
    from app.models.channel import ChannelBinding

    async def _q():
        async with factory() as db:
            return (await db.execute(
                select(ChannelBinding).where(ChannelBinding.channel == channel)
            )).scalars().all()

    return asyncio.run(_q())


def test_status_route_reports_binding_with_masked_uid(wc_db):
    """/status 绑定视图：绑定后报 bound + 脱敏 uid；不泄露 token。"""
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    asyncio.run(_add_char(wc_db, 101, 1, "角色A"))
    client = _make_client(1)

    r = client.post("/api/v1/plugins/wechat_ilink/bind", json={
        "character_id": 101, "bot_token": "tok", "baseurl": "https://base.weixin.qq.com",
        "ilink_user_id": "wx_uid_status_new_001", "ilink_bot_id": "bot-1"})
    assert r.status_code == 200, r.text

    s = client.get("/api/v1/plugins/wechat_ilink/status")
    assert s.status_code == 200, s.text
    data = s.json()
    assert data["ok"] is True
    assert data["bound"] is True
    assert data["character_id"] == 101
    assert "ilink_user_id_masked" in data
    masked = data["ilink_user_id_masked"]
    assert "wx_uid_status_new_001" not in masked  # 不泄露完整稳定 uid
    assert "tok" not in str(data)  # token 绝不进前端返回
