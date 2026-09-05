# -*- coding: utf-8 -*-
"""wechat_ilink /rebind（任务 B，App 换绑管理）测试：

- rebind 把内核 config 与插件自有绑定行角色一起切到目标角色（同一主账号家庭）；
- 目标非法/越权（跨家庭）→ 内核 403 透出，绑定行**不迁移**；
- 子账号调用 → 403（仅主账号）；
- 解绑（清空绑定/清凭据停状态）**不被误触发**：rebind 保留 bot_token_enc/baseurl，enabled 保持 True。
与 test_wechat_ilink_binding.py 同构：临时 SQLite + 登录态 mock + 内核完整 PUT 路径裁决。
"""
import asyncio
import os
import pathlib
import tempfile

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
def wc_db(monkeypatch, wc_plugin):
    """临时 SQLite 文件库：patch 各模块绑定的 async_session_factory（不触碰 backend/data）。"""
    monkeypatch.setenv("AMBRACE_SECRET_KEY", _SECRET_KEY)
    tmp = tempfile.mkdtemp(prefix="wechat_rebind_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
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


@pytest.fixture(autouse=True)
def _clear_admin_cache():
    perm._admin_cache.clear()
    yield
    perm._admin_cache.clear()


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(plugins_api.router)  # 内核 PUT /api/v1/plugins/{name}
    app.include_router(registry._loaded["wechat_ilink"]["router"])  # 插件 /bind //unbind//rebind//status
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


async def _seed_binding(factory, user_id, character_id, *, token="enc-token", baseurl="https://base.weixin.qq.com"):
    M = registry._loaded["wechat_ilink"]["module"].models
    async with factory() as db:
        db.add(M.WeChatILinkBinding(
            user_id=user_id, character_id=character_id, ilink_user_id="wx_uid_rebind",
            ilink_bot_id="bot-1", bot_token_enc=token, baseurl=baseurl,
            poll_buf="", out_count_in_window=0, enabled=True,
        ))
        await db.commit()


async def _bind_config(factory) -> str:
    return registry._db_config.get("wechat_ilink", {}).get("allowed_character_ids", "")


def _get_bindings(factory):
    M = registry._loaded["wechat_ilink"]["module"].models

    async def _q():
        async with factory() as db:
            return (await db.execute(select(M.WeChatILinkBinding))).scalars().all()

    return asyncio.run(_q())


# ------------------------------------------------------------------ /rebind 换绑

def test_rebind_success_switches_config_and_binding_role(wc_db):
    """换绑：目标=同家庭另一角色 → config 与绑定行角色一起切过去，凭据保留不清。"""
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    asyncio.run(_add_char(wc_db, 101, 1, "角色A"))
    asyncio.run(_add_char(wc_db, 102, 1, "角色B"))
    client = _make_client(1)
    r = client.put("/api/v1/plugins/wechat_ilink", json={"config": {"allowed_character_ids": [101]}})
    assert r.status_code == 200, r.text
    asyncio.run(_seed_binding(wc_db, 1, 101, token="enc-keep", baseurl="https://base.weixin.qq.com"))
    assert asyncio.run(_bind_config(wc_db)) == "101"

    r2 = client.post("/api/v1/plugins/wechat_ilink/rebind", json={"character_id": 102})
    assert r2.status_code == 200, r2.text
    assert r2.json()["rebound"] is True
    assert r2.json()["character_id"] == 102
    # config 切到 102
    assert asyncio.run(_bind_config(wc_db)) == "102"
    # 绑定行角色切到 102；enabled 保持 True；bot_token_enc/baseurl 保留（区别于解绑）
    rows = _get_bindings(wc_db)
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == 1
    assert row.character_id == 102
    assert row.enabled is True
    assert row.bot_token_enc == "enc-keep"
    assert row.baseurl == "https://base.weixin.qq.com"
    assert row.ilink_user_id == "wx_uid_rebind"  # 微信身份不变


def test_rebind_illegal_target_returns_4xx_and_no_migration(wc_db):
    """跨家庭目标 → 内核 403 透出；绑定行不迁移、凭据不清空（解绑不被误触发）。"""
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    asyncio.run(_add_user(wc_db, 3, "other_main", is_admin=True))
    asyncio.run(_add_char(wc_db, 101, 1, "角色A"))
    asyncio.run(_add_char(wc_db, 201, 3, "他人角色"))
    client = _make_client(1)
    client.put("/api/v1/plugins/wechat_ilink", json={"config": {"allowed_character_ids": [101]}})
    asyncio.run(_seed_binding(wc_db, 1, 101, token="enc-keep"))

    r = client.post("/api/v1/plugins/wechat_ilink/rebind", json={"character_id": 201})
    assert r.status_code == 403, r.text
    # P2-1（2026-09-05）：非法目标失败后，内核 config 必须补偿恢复为旧角色，
    # 不允许停在“先解绑后绑定”两段式留下的空绑定中间态
    assert asyncio.run(_bind_config(wc_db)) == "101"

    rows = _get_bindings(wc_db)
    assert len(rows) == 1
    assert rows[0].character_id == 101  # 未迁移
    assert rows[0].enabled is True      # 未被误改为停用
    assert rows[0].bot_token_enc == "enc-keep"  # 凭据未被清空（区别于解绑）


def test_rebind_sub_account_403(wc_db):
    """子账号调用 rebind → 403（仅主账号，内核裁决）。"""
    asyncio.run(_add_user(wc_db, 1, "main", is_admin=True))
    asyncio.run(_add_user(wc_db, 2, "sub", parent_id=1, is_admin=False))
    asyncio.run(_add_char(wc_db, 101, 1, "角色A"))
    asyncio.run(_add_char(wc_db, 102, 1, "角色B"))
    client = _make_client(2)
    r = client.post("/api/v1/plugins/wechat_ilink/rebind", json={"character_id": 102})
    assert r.status_code == 403, r.text
