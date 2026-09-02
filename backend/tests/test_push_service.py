# -*- coding: utf-8 -*-
"""push_service 单元测试（2026-08-28）。

覆盖：频控 / WS 在线优先 / FCM fallback / 无 token offline / 无效 token 清理 / FCM 未配置。
mock 掉 fcm_provider，禁止真发 FCM。
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.application.push_service as push_service
from app.application.push.fcm_provider import FcmSendResult


@pytest.fixture()
def push_db(monkeypatch):
    """临时 SQLite 文件库：patch push_service 绑定的 async_session_factory。"""
    tmp = tempfile.mkdtemp(prefix='push_test_')
    db_path = os.path.join(tmp, 't.db')
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}', poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(push_service, 'async_session_factory', factory)
    yield factory
    engine.sync_engine.dispose()


async def _add_token(factory, user_id, device_id, token):
    async with factory() as db:
        from app.models.device import UserDeviceToken
        db.add(UserDeviceToken(
            user_id=user_id, device_id=device_id, platform='android',
            push_provider='fcm', push_token=token,
        ))
        await db.commit()


async def _count_tokens(factory):
    async with factory() as db:
        from sqlalchemy import select
        from app.models.device import UserDeviceToken
        rows = (await db.execute(select(UserDeviceToken))).scalars().all()
        return len(rows)


def _run(coro):
    return asyncio.run(coro)


# ── 频控 ──

def test_rate_limit_normal(push_db):
    push_service._rate_buckets.clear()
    # 用 _consume_rate_slot 填充配额（模拟已发送的 FCM），替代原来 _check_rate_limit 计数
    for _ in range(5):
        push_service._consume_rate_slot(999)
    assert not push_service._check_rate_limit(999, "normal"), "第 6 次应被频控"
    assert push_service._check_rate_limit(999, "high"), "高优先级应豁免"
    assert push_service._check_rate_limit(888, "normal"), "不同用户独立桶"


def test_rate_limited_notify_no_fcm(push_db, monkeypatch):
    """频控命中：notify_user 直接返回 rate_limited，不触发 WS/FCM。"""
    push_service._rate_buckets.clear()
    ws_called = []

    async def _ws(u, p):
        ws_called.append(u)
        return False

    monkeypatch.setattr('app.ws.notify_manager.push_to_user', _ws)
    # 用 _consume_rate_slot 填充配额，替代原来 _check_rate_limit 计数
    for _ in range(5):
        push_service._consume_rate_slot(7)
    r = _run(push_service.notify_user(7, "t", "b"))
    assert r.rate_limited is True
    assert r.delivered is False
    assert ws_called == []


# ── WS 在线优先 ──

def test_ws_delivered_skips_fcm(push_db, monkeypatch):
    """WS 在线送达：不查 token、不发 FCM。"""
    push_service._rate_buckets.clear()

    async def _ws(u, p):
        return True

    monkeypatch.setattr('app.ws.notify_manager.push_to_user', _ws)

    async def _fail(*a, **k):
        raise AssertionError("不应走到 FCM")

    monkeypatch.setattr('app.application.push.fcm_provider.send', _fail)
    r = _run(push_service.notify_user(1, "t", "b"))
    assert r.delivered_ws is True
    assert r.delivered is True
    assert r.offline is False


# ── FCM fallback ──

def test_fcm_fallback_when_ws_offline(push_db, monkeypatch):
    push_service._rate_buckets.clear()

    async def _ws(u, p):
        return False

    monkeypatch.setattr('app.ws.notify_manager.push_to_user', _ws)
    _run(_add_token(push_db, 1, "dev1", "tokA"))

    seen = {}

    async def _send(token, title, body, data, *, channel_id="ai_companion_chat"):
        seen['data'] = data
        seen['channel_id'] = channel_id
        return FcmSendResult(success=True, message_id="mid")

    monkeypatch.setattr('app.application.push.fcm_provider.send', _send)
    r = _run(push_service.notify_user(1, "t", "b", {"route": "chat"}))
    assert r.delivered_fcm == 1
    assert r.delivered is True
    assert r.offline is False
    assert seen['channel_id'] == "ai_companion_chat"
    assert seen['data'].get('channel') == 'chat'


# ── 无 token → offline ──

def test_no_token_offline(push_db, monkeypatch):
    push_service._rate_buckets.clear()

    async def _ws(u, p):
        return False

    monkeypatch.setattr('app.ws.notify_manager.push_to_user', _ws)
    r = _run(push_service.notify_user(1, "t", "b"))
    assert r.offline is True
    assert r.delivered is False


# ── 无效 token 清理 ──

def test_invalid_token_removed(push_db, monkeypatch):
    """FCM 返回 invalid_token → 该 token 记录被删除。"""
    push_service._rate_buckets.clear()

    async def _ws(u, p):
        return False

    monkeypatch.setattr('app.ws.notify_manager.push_to_user', _ws)
    _run(_add_token(push_db, 1, "dev1", "bad1"))

    async def _send(token, title, body, data, *, channel_id="ai_companion_chat"):
        return FcmSendResult(success=False, invalid_token=True, error="404")

    monkeypatch.setattr('app.application.push.fcm_provider.send', _send)
    r = _run(push_service.notify_user(1, "t", "b"))
    assert r.invalid_tokens == 1
    assert r.delivered is False
    assert _run(_count_tokens(push_db)) == 0


# ── FCM 未配置 → error + offline ──

def test_fcm_not_configured(push_db, monkeypatch):
    push_service._rate_buckets.clear()

    async def _ws(u, p):
        return False

    monkeypatch.setattr('app.ws.notify_manager.push_to_user', _ws)
    _run(_add_token(push_db, 1, "dev1", "tokA"))

    async def _send(token, title, body, data, *, channel_id="ai_companion_chat"):
        return FcmSendResult(success=False, error="fcm_not_configured")

    monkeypatch.setattr('app.application.push.fcm_provider.send', _send)
    r = _run(push_service.notify_user(1, "t", "b"))
    assert r.offline is True
    assert any("fcm_not_configured" in e for e in r.errors)


# ── alert 渠道 ──

def test_fcm_alert_channel_and_high_priority(push_db, monkeypatch):
    push_service._rate_buckets.clear()

    async def _ws(u, p):
        return False

    monkeypatch.setattr('app.ws.notify_manager.push_to_user', _ws)
    _run(_add_token(push_db, 1, "dev1", "tokA"))

    seen = {}

    async def _send(token, title, body, data, *, channel_id="ai_companion_chat"):
        seen['data'] = data
        seen['channel_id'] = channel_id
        return FcmSendResult(success=True, message_id="mid")

    monkeypatch.setattr('app.application.push.fcm_provider.send', _send)
    r = _run(push_service.notify_user(
        1, "t", "b", {"route": "chat"}, priority="high", channel="alert"))
    assert r.delivered_fcm == 1
    assert seen['channel_id'] == "ai_companion_alert"
    assert seen['data'].get('channel') == 'alert'


# ── 频控：WS 在线/高优先级不消耗配额 ──

def test_ws_delivered_does_not_consume_rate_limit(push_db, monkeypatch):
    """WS 在线送达 5 次后，第 6 次 normal 级 notify_user 仍不被频控（WS 不消耗配额）。"""
    push_service._rate_buckets.clear()

    async def _ws(u, p):
        return True

    monkeypatch.setattr('app.ws.notify_manager.push_to_user', _ws)

    async def _fail(*a, **k):
        raise AssertionError("WS 送达不应走到 FCM")

    monkeypatch.setattr('app.application.push.fcm_provider.send', _fail)
    for _ in range(5):
        r = _run(push_service.notify_user(1, "t", "b"))
        assert r.delivered_ws is True
        assert r.rate_limited is False
    # 第 6 次仍应允许：WS 在线投递不消耗频控配额
    r = _run(push_service.notify_user(1, "t", "b"))
    assert r.rate_limited is False
    assert r.delivered_ws is True


def test_high_priority_fcm_does_not_consume_rate_limit(push_db, monkeypatch):
    """高优先级 FCM 发送不消耗频控配额。"""
    push_service._rate_buckets.clear()

    async def _ws(u, p):
        return False

    monkeypatch.setattr('app.ws.notify_manager.push_to_user', _ws)
    _run(_add_token(push_db, 1, "dev1", "tokA"))

    async def _send(token, title, body, data, *, channel_id="ai_companion_chat"):
        return FcmSendResult(success=True, message_id="mid")

    monkeypatch.setattr('app.application.push.fcm_provider.send', _send)
    # 连续发送 5 条高优先级，均不消耗配额
    for _ in range(5):
        r = _run(push_service.notify_user(1, "t", "b", priority="high"))
        assert r.delivered_fcm == 1
    # 此后 normal 级仍应允许：高优先级发送不消耗配额
    assert push_service._check_rate_limit(1, "normal") is True
