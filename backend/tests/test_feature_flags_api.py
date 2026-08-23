# -*- coding: utf-8 -*-
# 运行时 Feature Flag 开关测试（2026-08-18）：
# - flag_service：临时库 roundtrip（set 写库 + 热更新 AGENT_FLAGS / load 恢复覆盖 / source 标记 / 未知 key False）
# - system API：GET /feature-flags 主账号返回、非主账号 403；PUT 切换成功、缺 enabled 400、未知 key 404
import asyncio
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import system as system_api
from app.auth.deps import get_current_user_id

ADMIN = 1
OTHER = 200


@pytest.fixture()
def flag_db(monkeypatch):
    '''临时 SQLite 文件库：patch async_session_factory（不触碰 backend/data）'''
    tmp = tempfile.mkdtemp(prefix='flag_test_')
    db_path = os.path.join(tmp, 't.db')
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}', poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, 'async_session_factory', factory)
    yield factory
    engine.sync_engine.dispose()


def test_set_and_load_roundtrip(flag_db):
    from app.agent.loop import AGENT_FLAGS
    from app.services import flag_service
    saved = AGENT_FLAGS.get('agent_loop_search')
    try:
        assert asyncio.run(flag_service.set_runtime_flag('agent_loop_search', False)) is True
        assert AGENT_FLAGS.get('agent_loop_search') is False
        # 重置内存为默认后 load 应恢复 DB 覆盖
        AGENT_FLAGS['agent_loop_search'] = True
        n = asyncio.run(flag_service.load_runtime_flags())
        assert n >= 1
        assert AGENT_FLAGS.get('agent_loop_search') is False
        flags = asyncio.run(flag_service.get_all_flags())
        item = next(f for f in flags if f['key'] == 'agent_loop_search')
        assert item['source'] == 'db'
        # 未知 key 返回 False
        assert asyncio.run(flag_service.set_runtime_flag('not_a_flag', True)) is False
    finally:
        AGENT_FLAGS['agent_loop_search'] = saved


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def test_api_get_flags_admin(monkeypatch):
    async def _fake_flags():
        return [{'key': 'agent_loop_search', 'enabled': True, 'source': 'default'}]
    monkeypatch.setattr('app.services.flag_service.get_all_flags', _fake_flags)
    r = _make_client(ADMIN).get('/api/v1/system/feature-flags')
    assert r.status_code == 200
    assert r.json()['flags'][0]['key'] == 'agent_loop_search'


def test_api_get_flags_forbidden():
    r = _make_client(OTHER).get('/api/v1/system/feature-flags')
    assert r.status_code == 403


def test_api_put_flag_ok(monkeypatch):
    calls = []
    async def _fake_set(key, enabled):
        calls.append((key, enabled))
        return True
    monkeypatch.setattr('app.services.flag_service.set_runtime_flag', _fake_set)
    r = _make_client(ADMIN).put('/api/v1/system/feature-flags/agent_loop_search', json={'enabled': False})
    assert r.status_code == 200
    assert calls == [('agent_loop_search', False)]


def test_api_put_flag_missing_enabled(monkeypatch):
    async def _fake_set(key, enabled):
        return True
    monkeypatch.setattr('app.services.flag_service.set_runtime_flag', _fake_set)
    r = _make_client(ADMIN).put('/api/v1/system/feature-flags/agent_loop_search', json={})
    assert r.status_code == 400


def test_api_put_flag_unknown_key(monkeypatch):
    async def _fake_set(key, enabled):
        return False
    monkeypatch.setattr('app.services.flag_service.set_runtime_flag', _fake_set)
    r = _make_client(ADMIN).put('/api/v1/system/feature-flags/nope', json={'enabled': True})
    assert r.status_code == 404

