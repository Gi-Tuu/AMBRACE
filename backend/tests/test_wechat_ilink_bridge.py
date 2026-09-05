# -*- coding: utf-8 -*-
"""wechat_ilink 桥端点（/bridge/relay）测试：服务到服务鉴权 + 幂等 + 回复/配额裁决。"""
import asyncio
import importlib
import os
import pathlib
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.plugins import registry
from app.api import plugins as plugins_api
from app.providers import registry as prov_reg
from app.application import permission_service as perm

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"
_SECRET = "bridge-test-secret-123"
RELAY_URL = "/api/v1/plugins/bridge/wechat-relay"
DELIVERY_URL = "/api/v1/plugins/bridge/wechat-delivery"


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
    monkeypatch.setenv("AMBRACE_SECRET_KEY", "wechat-ilink-test-secret-000000000000000000000001")
    monkeypatch.setenv("WECHAT_ILINK_BRIDGE_SECRET", _SECRET)
    tmp = tempfile.mkdtemp(prefix="wechat_bridge_test_")
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


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(plugins_api.router)  # 内核免登录桥端点
    return TestClient(app)


def _plugin_models():
    return registry._loaded["wechat_ilink"]["module"].models


async def _seed(factory, *, wx_uid="wxuid-1", out_count=0):
    from app.models.user import User
    from app.models.character import AICharacter
    M = _plugin_models()
    async with factory() as db:
        db.add(User(id=1, username="main", nickname="main", is_admin=True))
        db.add(AICharacter(id=101, user_id=1, name="小慧"))
        db.add(M.WeChatILinkBinding(
            user_id=1, character_id=101, ilink_user_id=wx_uid, ilink_bot_id="",
            bot_token_enc="x", baseurl="https://ilinkai.weixin.qq.com",
            poll_buf="", out_count_in_window=out_count, enabled=True,
        ))
        await db.commit()


def _bindings(factory):
    M = _plugin_models()

    async def _q():
        async with factory() as db:
            return (await db.execute(select(M.WeChatILinkBinding))).scalars().all()

    return asyncio.run(_q())


def _patch_reply(monkeypatch, text="回复文本"):
    mod = importlib.import_module("inbound")

    async def _fake(user_id, character_id, content):
        return text

    monkeypatch.setattr(mod, "_run_companion_reply", _fake)


def test_401_wrong_or_missing_secret(wc_db):
    asyncio.run(_seed(wc_db))
    client = _client()
    body = {"ilink_user_id": "wxuid-1", "text": "你好", "msg_id": "m1"}
    assert client.post(RELAY_URL, json=body).status_code == 401
    assert client.post(RELAY_URL, json=body,
                       headers={"X-AMBRACE-Bridge-Secret": "wrong"}).status_code == 401


def test_503_when_secret_not_configured(wc_db, monkeypatch):
    monkeypatch.delenv("WECHAT_ILINK_BRIDGE_SECRET", raising=False)
    asyncio.run(_seed(wc_db))
    r = _client().post(RELAY_URL, json={"ilink_user_id": "wxuid-1", "text": "hi"},
                       headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 503


def test_400_missing_fields(wc_db):
    asyncio.run(_seed(wc_db))
    client = _client()
    r = client.post(RELAY_URL, json={"ilink_user_id": "wxuid-1"},
                    headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 400


def test_ok_flow_reply_quota_and_dedupe(wc_db, monkeypatch):
    asyncio.run(_seed(wc_db))
    _patch_reply(monkeypatch, "小慧回复你")
    client = _client()
    body = {"ilink_user_id": "wxuid-1", "text": "你好", "msg_id": "m-1"}
    r = client.post(RELAY_URL, json=body, headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True and data["reply"] == "小慧回复你"
    assert data["sendable"] is True and data["quota"]["charged"] is True
    assert data["quota"]["remaining"] == 9

    row = _bindings(wc_db)[0]
    assert row.out_count_in_window == 1 and row.window_started_at is not None
    # 幂等：同 msg_id 二次转发 → duplicate，不重复计费/落库
    r2 = client.post(RELAY_URL, json=body, headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    assert _bindings(wc_db)[0].out_count_in_window == 1


def test_no_binding_code(wc_db):
    client = _client()
    r = client.post(RELAY_URL, json={"ilink_user_id": "nobody", "text": "hi", "msg_id": "m-x"},
                    headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 200 and r.json()["code"] == "no_binding"


def test_empty_reply_not_sendable(wc_db, monkeypatch):
    asyncio.run(_seed(wc_db))
    _patch_reply(monkeypatch, "")
    r = _client().post(RELAY_URL, json={"ilink_user_id": "wxuid-1", "text": "hi", "msg_id": "m-2"},
                       headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    data = r.json()
    assert data["ok"] is True and data["reply"] == "" and data["sendable"] is False
    assert _bindings(wc_db)[0].out_count_in_window == 0


def test_bridge_reply_cleaned_and_sent_cleanup_reported(wc_db, monkeypatch):
    """L2 出口净文：带结构化标记的回复在 QuotaGate 前净文，返回下层发的干净文本 + sent_cleanup 审计。"""
    asyncio.run(_seed(wc_db))
    _patch_reply(monkeypatch, "【状态更新：今天很努力】好的呀")
    r = _client().post(RELAY_URL, json={"ilink_user_id": "wxuid-1", "text": "hi", "msg_id": "m-clean"},
                       headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["reply"] == "好的呀"          # 标记被净文，下发的是一段可读正文
    assert data["sendable"] is True
    assert "structured_markers" in data["sent_cleanup"]["stripped"]
    assert data["sent_cleanup"]["truncated"] is False
    # 配额已计费（out 流水已落）
    row = _bindings(wc_db)[0]
    assert row.out_count_in_window == 1


def test_bridge_reply_truncated_reported(wc_db, monkeypatch):
    """L2 出口净文：超长回复在出口截断并补 …,sent_cleanup.truncated=True。"""
    asyncio.run(_seed(wc_db))
    _patch_reply(monkeypatch, "字" * 600)
    r = _client().post(RELAY_URL, json={"ilink_user_id": "wxuid-1", "text": "hi", "msg_id": "m-trunc"},
                       headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    data = r.json()
    assert data["ok"] is True
    assert data["reply"].endswith("…") and data["sendable"] is True
    assert data["sent_cleanup"]["truncated"] is True


# ================================================================== P3-4：桥端点加固（413/429）
def test_relay_413_body_too_large(wc_db):
    """P3-4：序列化后 body >32KB → 413（进入处理前拦截）。"""
    asyncio.run(_seed(wc_db))
    body = {"ilink_user_id": "wxuid-1", "text": "x" * (33 * 1024), "msg_id": "m-big"}
    r = _client().post(RELAY_URL, json=body, headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 413


def test_relay_429_rate_limited(wc_db, monkeypatch):
    """P3-4：令牌桶突发耗尽后 → 429（带 Retry-After）。用无绑定 sender 避免落库副作用。"""
    monkeypatch.setattr(plugins_api, "_BRIDGE_RATE_BURST", 3)
    monkeypatch.setattr(plugins_api, "_BRIDGE_RATE_PER_SEC", 0.0)
    plugins_api._reset_bridge_rate()
    client = _client()
    for _ in range(3):
        r = client.post(RELAY_URL, json={"ilink_user_id": "ratelimit-no", "text": "hi", "msg_id": "r"},
                        headers={"X-AMBRACE-Bridge-Secret": _SECRET})
        assert r.status_code == 200, r.text
    r4 = client.post(RELAY_URL, json={"ilink_user_id": "ratelimit-no", "text": "hi", "msg_id": "r2"},
                     headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r4.status_code == 429
    assert "Retry-After" in r4.headers
    plugins_api._reset_bridge_rate()  # 恢复兜底令牌，避免影响后续用例


def test_relay_response_includes_out_row_id(wc_db, monkeypatch):
    """P3-1 回执通道：成功下发的桥响应带出 out 流水行 id（刚落的 out 行）。"""
    asyncio.run(_seed(wc_db))
    _patch_reply(monkeypatch, "小慧回复你")
    r = _client().post(RELAY_URL, json={"ilink_user_id": "wxuid-1", "text": "你好", "msg_id": "m-outid"},
                       headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True and data["sendable"] is True
    assert isinstance(data.get("out_row_id"), int)


# ================================================================== C 项：wechat-delivery 回执端点
def _seed_out_row(factory, *, binding_id, status="sent_by_gateway"):
    M = _plugin_models()

    async def _do():
        async with factory() as db:
            row = M.WeChatILinkMessage(
                binding_id=binding_id, character_id=101, ilink_msg_id="", context_token="",
                direction="out", content="hi", quota_charged=True, status=status,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id

    return asyncio.run(_do())


def _get_msg_row(factory, rid):
    M = _plugin_models()

    async def _q():
        async with factory() as db:
            return await db.get(M.WeChatILinkMessage, rid)

    return asyncio.run(_q())


def test_delivery_401_wrong_or_missing_secret(wc_db):
    asyncio.run(_seed(wc_db))
    body = {"out_row_id": 1, "ok": False, "error": "x"}
    assert _client().post(DELIVERY_URL, json=body).status_code == 401
    assert _client().post(DELIVERY_URL, json=body,
                          headers={"X-AMBRACE-Bridge-Secret": "wrong"}).status_code == 401


def test_delivery_503_when_secret_not_configured(wc_db, monkeypatch):
    monkeypatch.delenv("WECHAT_ILINK_BRIDGE_SECRET", raising=False)
    r = _client().post(DELIVERY_URL, json={"out_row_id": 1, "ok": False},
                       headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 503


def test_delivery_400_bad_row_id(wc_db):
    """一机多主回执稳定键（2026-09-05）：坏 out_row_id 不再 400，回落稳定键定位；
    既无有效 out_row_id 也无 (bot_account_id, in_msg_id) → 200 not_found。"""
    r = _client().post(DELIVERY_URL, json={"out_row_id": "nope", "ok": False},
                       headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 200 and r.json().get("code") == "not_found"


def test_delivery_not_found(wc_db):
    r = _client().post(DELIVERY_URL, json={"out_row_id": 99999, "ok": False},
                       headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 200 and r.json().get("code") == "not_found"


def test_delivery_marks_failed_and_idempotent(wc_db):
    """success=false 回调：sent_by_gateway 的 out 行 → failed（配额不回补）；重复回调无副作用。"""
    asyncio.run(_seed(wc_db))
    bid = _bindings(wc_db)[0].id
    rid = _seed_out_row(wc_db, binding_id=bid, status="sent_by_gateway")
    client = _client()

    r = client.post(DELIVERY_URL, json={"out_row_id": rid, "ok": False, "error": "send timeout"},
                    headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed"
    assert _get_msg_row(wc_db, rid).status == "failed"

    # 幂等：重复回调仍 failed，无副作用
    r2 = client.post(DELIVERY_URL, json={"out_row_id": rid, "ok": False},
                     headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r2.status_code == 200 and r2.json()["status"] == "failed"


def test_delivery_success_leaves_sent_by_gateway(wc_db):
    """success=true（或 ok=true）不回传：保持 sent_by_gateway。"""
    asyncio.run(_seed(wc_db))
    bid = _bindings(wc_db)[0].id
    rid = _seed_out_row(wc_db, binding_id=bid, status="sent_by_gateway")
    r = _client().post(DELIVERY_URL, json={"out_row_id": rid, "ok": True},
                       headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent_by_gateway"
    assert _get_msg_row(wc_db, rid).status == "sent_by_gateway"
