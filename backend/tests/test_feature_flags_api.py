# -*- coding: utf-8 -*-
# 运行时 Feature Flag 开关测试（2026-08-18）：
# - flag_service：临时库 roundtrip（set 写库 + 热更新 AGENT_FLAGS / load 恢复覆盖 / source 标记 / 未知 key False）
# - system API：GET /feature-flags 主账号返回、非主账号 403；PUT 切换成功、缺 enabled 400、未知 key 404
import asyncio
import os

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import system as system_api
from app.auth.deps import get_current_user_id

pytestmark = pytest.mark.slow

ADMIN = 1
OTHER = 200


@pytest.fixture(autouse=True)
def _server_admin_as_user1(monkeypatch):
    '''账号独立 P2（契约 §3）：写类服务器级端点（PUT /feature-flags/{key}）收紧为 require_server_admin。

    本文件聚焦「端点行为」（200/400/404 语义），统一把 1 号账号视为 server_admin，使断言不再依赖
    「会话共享测试库里 id=1 是否存在、server_admin 是否为 1」。真实判定链（DB 权威 + 30s 缓存 +
    env 兜底 + 非 server_admin 一律 403）由 tests/test_admin_console_p2.py 与
    test_tenant_isolation_matrix.py 覆盖。
    '''
    from app.application import permission_service as perm

    async def _is_server_admin(uid: int) -> bool:
        return int(uid) == ADMIN

    monkeypatch.setattr(perm, 'is_server_admin', _is_server_admin)


@pytest.fixture()
def flag_db(monkeypatch, tmp_path):
    '''临时 SQLite 文件库：patch async_session_factory（不触碰 backend/data）'''
    tmp = str(tmp_path)
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
    from app.application import flag_service
    saved = AGENT_FLAGS.get('agent_loop_chat')
    try:
        assert asyncio.run(flag_service.set_runtime_flag('agent_loop_chat', False)) is True
        assert AGENT_FLAGS.get('agent_loop_chat') is False
        # 重置内存为默认后 load 应恢复 DB 覆盖
        AGENT_FLAGS['agent_loop_chat'] = True
        n = asyncio.run(flag_service.load_runtime_flags())
        assert n >= 1
        assert AGENT_FLAGS.get('agent_loop_chat') is False
        flags = asyncio.run(flag_service.get_all_flags())
        item = next(f for f in flags if f['key'] == 'agent_loop_chat')
        assert item['source'] == 'db'
        # 未知 key 返回 False
        assert asyncio.run(flag_service.set_runtime_flag('not_a_flag', True)) is False
    finally:
        AGENT_FLAGS['agent_loop_chat'] = saved


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def test_api_get_flags_admin(monkeypatch):
    async def _fake_flags():
        return [{'key': 'agent_loop_chat', 'enabled': True, 'source': 'default'}]
    monkeypatch.setattr('app.application.flag_service.get_all_flags', _fake_flags)
    r = _make_client(ADMIN).get('/api/v1/system/feature-flags')
    assert r.status_code == 200
    assert r.json()['flags'][0]['key'] == 'agent_loop_chat'


def test_api_get_flags_forbidden():
    r = _make_client(OTHER).get('/api/v1/system/feature-flags')
    assert r.status_code == 403


def test_api_put_flag_ok(monkeypatch):
    calls = []
    async def _fake_set(key, enabled):
        calls.append((key, enabled))
        return True
    monkeypatch.setattr('app.application.flag_service.set_runtime_flag', _fake_set)
    r = _make_client(ADMIN).put('/api/v1/system/feature-flags/agent_loop_chat', json={'enabled': False})
    assert r.status_code == 200
    assert calls == [('agent_loop_chat', False)]


def test_api_put_flag_missing_enabled(monkeypatch):
    async def _fake_set(key, enabled):
        return True
    monkeypatch.setattr('app.application.flag_service.set_runtime_flag', _fake_set)
    r = _make_client(ADMIN).put('/api/v1/system/feature-flags/agent_loop_chat', json={})
    assert r.status_code == 400


def test_api_put_flag_unknown_key(monkeypatch):
    async def _fake_set(key, enabled):
        return False
    monkeypatch.setattr('app.application.flag_service.set_runtime_flag', _fake_set)
    r = _make_client(ADMIN).put('/api/v1/system/feature-flags/nope', json={'enabled': True})
    assert r.status_code == 404


def test_set_runtime_flag_reject_non_bool_key(flag_db):
    from app.agent.loop import AGENT_FLAGS
    from app.application import flag_service
    from sqlalchemy import select
    from app.models.config import RuntimeFlag
    # domain_event_retention_days 是仍存在的数字型键（默认 0）：热切应被类型防护拒绝
    saved = AGENT_FLAGS["domain_event_retention_days"]
    assert isinstance(saved, int)
    assert asyncio.run(flag_service.set_runtime_flag("domain_event_retention_days", True)) is False
    assert AGENT_FLAGS["domain_event_retention_days"] == saved  # 内存不被改动
    # 已固化为常量的原 flag 也不再可热切（已移出 AGENT_FLAGS）
    assert asyncio.run(flag_service.set_runtime_flag("memory_recall_hop_limit", True)) is False
    # 库不新增任何行
    async def _count():
        async with flag_db() as db:
            return len((await db.execute(select(RuntimeFlag))).scalars().all())
    assert asyncio.run(_count()) == 0


def test_load_runtime_flags_skips_non_bool_key(flag_db):
    from app.agent.loop import AGENT_FLAGS
    from app.application import flag_service
    from app.models.config import RuntimeFlag
    retention_saved = AGENT_FLAGS["domain_event_retention_days"]
    chat_saved = AGENT_FLAGS["agent_loop_chat"]
    try:
        async def _seed():
            async with flag_db() as db:
                db.add(RuntimeFlag(key="domain_event_retention_days", enabled=True))  # 历史脏行：True→1 天
                db.add(RuntimeFlag(key="agent_loop_chat", enabled=False))            # 正常 bool 覆盖
                await db.commit()
        asyncio.run(_seed())
        n = asyncio.run(flag_service.load_runtime_flags())
        # 数字型键被类型防护跳过：保留原 int 默认，不被 True 覆盖成 1 天
        assert AGENT_FLAGS["domain_event_retention_days"] == retention_saved
        assert isinstance(AGENT_FLAGS["domain_event_retention_days"], int)
        # 正常 bool 键仍按 DB 覆盖
        assert AGENT_FLAGS["agent_loop_chat"] is False
        assert n == 1
    finally:
        AGENT_FLAGS["agent_loop_chat"] = chat_saved

