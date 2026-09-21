# -*- coding: utf-8 -*-
"""device push API 测试（2026-08-28）。

覆盖：register/heartbeat/unregister 鉴权（未登录 401）+ upsert 行为（同设备同 provider 更新 token）。
参照 tests/test_family.py 的 TestClient / _make_client 写法。
"""
import asyncio
import json

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import device as device_api
from app.auth.deps import get_current_user_id


# 快测档（2026-09-12）：本文件是重量级/集成型用例（每例起一次临时库，约 3s/例），打 slow 标记。
# 全量默认照跑；日常开发用 pytest -m "not slow" 跳过本档（见 docs/engineering-protocol.md 十八）。
pytestmark = pytest.mark.slow

@pytest.fixture()
def device_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库：patch device API 绑定的 async_session_factory。"""
    db_path = tmp_path / 't.db'
    # 父行（users）由用例内 _add_user 自建，与本文件账号口径一致（不重复补父行）。
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)
    # device.py 在模块导入时通过 `from app.db.database import async_session_factory` 绑定引用
    monkeypatch.setattr(device_api, 'async_session_factory', factory)
    yield factory
    engine.sync_engine.dispose()


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(device_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _make_public_client() -> TestClient:
    """不覆盖鉴权依赖（未带 token），用于验证未登录 401。"""
    app = FastAPI()
    app.include_router(device_api.router)
    return TestClient(app)


async def _add_user(factory, uid: int):
    async with factory() as db:
        from app.models.user import User
        db.add(User(id=uid, username=f'u{uid}', nickname=f'n{uid}'))
        await db.commit()


async def _count_tokens(factory) -> int:
    async with factory() as db:
        from app.models.device import UserDeviceToken
        rows = (await db.execute(select(UserDeviceToken))).scalars().all()
        return len(rows)


async def _get_token(factory, user_id, device_id, provider="fcm"):
    async with factory() as db:
        from app.models.device import UserDeviceToken
        stmt = select(UserDeviceToken).where(
            UserDeviceToken.user_id == user_id,
            UserDeviceToken.device_id == device_id,
            UserDeviceToken.push_provider == provider,
        )
        return (await db.execute(stmt)).scalar_one_or_none()


# ── 鉴权 ──

def test_fcm_config_public(device_db, monkeypatch):
    """/fcm-config 是公开接口，未登录也可访问；未配置时返回 enabled=false。

    2026-09-13 隔离修复：本机 .env 配了 FCM 凭据时原用例会失败——改为显式自造配置，
    不再依赖跑测机器的环境（与 test_fcm_smoke.py 同一思路）。
    """
    monkeypatch.setattr(device_api.settings, 'push_fcm_enabled', False, raising=False)
    monkeypatch.setattr(device_api.settings, 'push_fcm_client_config', '', raising=False)
    client = _make_public_client()
    r = client.get('/api/v1/device/fcm-config')
    assert r.status_code == 200, r.text
    assert r.json() == {"enabled": False}


def test_fcm_config_enabled_when_configured(device_db, monkeypatch):
    """配好凭据时返回完整客户端配置（enabled=true）——服务端侧正常态。"""
    monkeypatch.setattr(device_api.settings, 'push_fcm_enabled', True, raising=False)
    monkeypatch.setattr(
        device_api.settings, 'push_fcm_client_config',
        json.dumps({'apiKey': 'k', 'appId': 'a', 'messagingSenderId': 's', 'projectId': 'p'}),
        raising=False,
    )
    client = _make_public_client()
    r = client.get('/api/v1/device/fcm-config')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['enabled'] is True and body['projectId'] == 'p'


def test_register_requires_auth(device_db):
    client = _make_public_client()
    r = client.post('/api/v1/device/register', json={
        "device_id": "dev1", "platform": "android",
        "push_provider": "fcm", "push_token": "tok",
    })
    assert r.status_code == 401, r.text


def test_unregister_requires_auth(device_db):
    client = _make_public_client()
    r = client.delete('/api/v1/device/unregister',
                      params={"device_id": "dev1", "push_provider": "fcm"})
    assert r.status_code == 401, r.text


# ── register / upsert ──

def test_register_inserts_token(device_db):
    asyncio.run(_add_user(device_db, 1))
    client = _make_client(1)
    r = client.post('/api/v1/device/register', json={
        "device_id": "dev1", "platform": "android",
        "push_provider": "fcm", "push_token": "tokA",
        "app_version": "3.3.8",
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert asyncio.run(_count_tokens(device_db)) == 1


def test_register_upsert_updates_token(device_db):
    asyncio.run(_add_user(device_db, 1))
    client = _make_client(1)
    r1 = client.post('/api/v1/device/register', json={
        "device_id": "dev1", "platform": "android",
        "push_provider": "fcm", "push_token": "tokA",
    })
    assert r1.status_code == 200, r1.text
    # 同设备同 provider 再注册 → 更新 token，不新增行
    r2 = client.post('/api/v1/device/register', json={
        "device_id": "dev1", "platform": "android",
        "push_provider": "fcm", "push_token": "tokB",
    })
    assert r2.status_code == 200, r2.text
    assert asyncio.run(_count_tokens(device_db)) == 1
    existing = asyncio.run(_get_token(device_db, 1, "dev1"))
    assert existing is not None
    assert existing.push_token == "tokB"


def test_register_same_device_replaces_old_user_token(device_db):
    """同设备换账号：旧账号残留 token 被清掉，防止登出失败后串号。"""
    asyncio.run(_add_user(device_db, 1))
    asyncio.run(_add_user(device_db, 2))
    client1 = _make_client(1)
    client2 = _make_client(2)
    assert client1.post('/api/v1/device/register', json={
        "device_id": "dev1", "platform": "android",
        "push_provider": "fcm", "push_token": "tok1",
    }).status_code == 200
    assert client2.post('/api/v1/device/register', json={
        "device_id": "dev1", "platform": "android",
        "push_provider": "fcm", "push_token": "tok2",
    }).status_code == 200
    assert asyncio.run(_count_tokens(device_db)) == 1
    assert asyncio.run(_get_token(device_db, 1, "dev1")) is None
    assert asyncio.run(_get_token(device_db, 2, "dev1")) is not None


def test_register_different_devices_isolated(device_db):
    """不同用户/不同设备各自维护 token，互不覆盖。"""
    asyncio.run(_add_user(device_db, 1))
    asyncio.run(_add_user(device_db, 2))
    client1 = _make_client(1)
    client2 = _make_client(2)
    assert client1.post('/api/v1/device/register', json={
        "device_id": "devA", "platform": "android",
        "push_provider": "fcm", "push_token": "tok1",
    }).status_code == 200
    assert client2.post('/api/v1/device/register', json={
        "device_id": "devB", "platform": "android",
        "push_provider": "fcm", "push_token": "tok2",
    }).status_code == 200
    assert asyncio.run(_count_tokens(device_db)) == 2


# ── heartbeat ──

def test_heartbeat_updates_last_seen(device_db):
    asyncio.run(_add_user(device_db, 1))
    client = _make_client(1)
    client.post('/api/v1/device/register', json={
        "device_id": "dev1", "platform": "android",
        "push_provider": "fcm", "push_token": "tokA",
    })
    r = client.post('/api/v1/device/heartbeat', json={
        "device_id": "dev1", "push_provider": "fcm",
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    existing = asyncio.run(_get_token(device_db, 1, "dev1"))
    assert existing is not None and existing.last_seen_at is not None


def test_heartbeat_unknown_device_ok(device_db):
    """不存在的设备心跳：静默返回 ok（不报错）。"""
    asyncio.run(_add_user(device_db, 1))
    client = _make_client(1)
    r = client.post('/api/v1/device/heartbeat', json={
        "device_id": "ghost", "push_provider": "fcm",
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}


# ── unregister ──

def test_unregister_removes_token(device_db):
    asyncio.run(_add_user(device_db, 1))
    client = _make_client(1)
    client.post('/api/v1/device/register', json={
        "device_id": "dev1", "platform": "android",
        "push_provider": "fcm", "push_token": "tokA",
    })
    r = client.delete('/api/v1/device/unregister',
                      params={"device_id": "dev1", "push_provider": "fcm"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert asyncio.run(_count_tokens(device_db)) == 0


def test_unregister_unknown_ok(device_db):
    """不存在的 token 注销：静默返回 ok。"""
    asyncio.run(_add_user(device_db, 1))
    client = _make_client(1)
    r = client.delete('/api/v1/device/unregister',
                      params={"device_id": "ghost", "push_provider": "fcm"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}


def test_fcm_diagnostic_accepts_report(device_db):
    """FCM 诊断上报：公开接口，只写日志，返回 ok（2026-09-13 排查真机拿不到 token）。"""
    client = _make_client(1)
    r = client.post('/api/v1/device/fcm-diagnostic', json={
        'device_id': 'dev1', 'stage': 'token_null', 'detail': 'getToken returned null',
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
