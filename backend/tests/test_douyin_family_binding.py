# -*- coding: utf-8 -*-
"""#68 P5 抖音组级唯一绑定测试：
- 子账号配置抖音 → 403（update_plugin / platform_profiles PUT /douyin 两处）
- 跨家庭角色绑定 → 403
- 多角色绑定（allowed_character_ids >1）→ 400
- 家庭内单选绑定成功；同家庭再绑其他角色 → 400（全组唯一）
- 解绑（空数组）成功
- platform_profiles PUT /douyin：主账号 200
"""
import asyncio
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import plugins as plugins_api
from app.api import platform_profiles as pp_api
from app.auth.deps import get_current_user_id
from app.plugins import registry
from app.services import permission_service as perm


@pytest.fixture()
def dv_db(monkeypatch):
    """临时 SQLite 文件库：patch 各模块绑定的 async_session_factory（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix='douyin_bind_test_')
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
    monkeypatch.setattr(perm, 'async_session_factory', factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_admin_cache():
    """清理 permission_service._admin_cache，避免跨用例污染/缓存命中干扰。"""
    perm._admin_cache.clear()
    yield
    perm._admin_cache.clear()


@pytest.fixture(autouse=True)
def _seed_registry():
    """内存注册表注入 douyin_mcp（不依赖磁盘扫描），并清空配置缓存。"""
    registry._loaded['douyin_mcp'] = {
        'info': {
            'name': 'douyin_mcp', 'version': '0.1.0', 'description': '抖音 MCP',
            'author': 'AICompanion', 'category': 'mcp', 'type': 'mcp',
            'config': {'allowed_character_ids': ''},
        },
        'module': None, 'hooks': {}, 'actions': {}, 'router': None,
    }
    registry._db_config['douyin_mcp'] = {'allowed_character_ids': ''}
    registry._enabled['douyin_mcp'] = True
    yield
    registry._loaded.pop('douyin_mcp', None)
    registry._db_config.pop('douyin_mcp', None)
    registry._enabled.pop('douyin_mcp', None)


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(plugins_api.router)
    app.include_router(pp_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _add_user(factory, uid: int, username: str, parent_id: int | None = None, is_admin: bool = False):
    from app.models.user import User
    async with factory() as db:
        db.add(User(id=uid, username=username, nickname=f'n{uid}', parent_id=parent_id, is_admin=is_admin))
        await db.commit()


async def _add_char(factory, cid: int, owner_id: int, name: str = '角色'):
    from app.models.character import AICharacter
    async with factory() as db:
        db.add(AICharacter(id=cid, user_id=owner_id, name=name))
        await db.commit()


async def _bind_config(factory) -> str:
    """读取 registry 内存中 douyin_mcp 的 allowed_character_ids 绑定串。"""
    return registry._db_config.get('douyin_mcp', {}).get('allowed_character_ids', '')


def test_sub_account_cannot_bind_403(dv_db):
    asyncio.run(_add_user(dv_db, 1, 'main', is_admin=True))
    asyncio.run(_add_user(dv_db, 2, 'sub', parent_id=1, is_admin=False))
    client = _make_client(2)
    r = client.put('/api/v1/plugins/douyin_mcp', json={'config': {'allowed_character_ids': [101]}})
    assert r.status_code == 403, r.text


def test_cross_family_bind_403(dv_db):
    asyncio.run(_add_user(dv_db, 1, 'main', is_admin=True))
    asyncio.run(_add_user(dv_db, 3, 'other_main', is_admin=True))
    asyncio.run(_add_char(dv_db, 201, 3, '他人角色'))
    client = _make_client(1)
    r = client.put('/api/v1/plugins/douyin_mcp', json={'config': {'allowed_character_ids': [201]}})
    assert r.status_code == 403, r.text


def test_multi_bind_400(dv_db):
    asyncio.run(_add_user(dv_db, 1, 'main', is_admin=True))
    client = _make_client(1)
    r = client.put('/api/v1/plugins/douyin_mcp', json={'config': {'allowed_character_ids': [101, 102]}})
    assert r.status_code == 400, r.text


def test_family_single_bind_success(dv_db):
    asyncio.run(_add_user(dv_db, 1, 'main', is_admin=True))
    asyncio.run(_add_char(dv_db, 101, 1, '我的角色'))
    client = _make_client(1)
    r = client.put('/api/v1/plugins/douyin_mcp', json={'config': {'allowed_character_ids': [101]}})
    assert r.status_code == 200, r.text
    assert asyncio.run(_bind_config(dv_db)) == '101'


def test_same_family_rebind_other_400(dv_db):
    asyncio.run(_add_user(dv_db, 1, 'main', is_admin=True))
    asyncio.run(_add_char(dv_db, 101, 1, '角色A'))
    asyncio.run(_add_char(dv_db, 102, 1, '角色B'))
    client = _make_client(1)
    # 先绑定 101
    r = client.put('/api/v1/plugins/douyin_mcp', json={'config': {'allowed_character_ids': [101]}})
    assert r.status_code == 200, r.text
    # 同家庭再绑 102（已被 101 占用）→ 400
    r2 = client.put('/api/v1/plugins/douyin_mcp', json={'config': {'allowed_character_ids': [102]}})
    assert r2.status_code == 400, r2.text


def test_unbind_empty_success(dv_db):
    asyncio.run(_add_user(dv_db, 1, 'main', is_admin=True))
    asyncio.run(_add_char(dv_db, 101, 1, '角色A'))
    client = _make_client(1)
    r = client.put('/api/v1/plugins/douyin_mcp', json={'config': {'allowed_character_ids': [101]}})
    assert r.status_code == 200, r.text
    assert asyncio.run(_bind_config(dv_db)) == '101'
    # 解绑：空数组
    r2 = client.put('/api/v1/plugins/douyin_mcp', json={'config': {'allowed_character_ids': []}})
    assert r2.status_code == 200, r2.text
    assert asyncio.run(_bind_config(dv_db)) == ''


def test_platform_profile_sub_403_main_200(dv_db):
    asyncio.run(_add_user(dv_db, 1, 'main', is_admin=True))
    asyncio.run(_add_user(dv_db, 2, 'sub', parent_id=1, is_admin=False))
    asyncio.run(_add_user(dv_db, 4, 'other_main', is_admin=True))
    sub_client = _make_client(2)
    r_sub = sub_client.put('/api/v1/platform-profile/douyin', json={'memory_restrict': 'off'})
    assert r_sub.status_code == 403, r_sub.text
    main_client = _make_client(1)
    r_main = main_client.put('/api/v1/platform-profile/douyin', json={'memory_restrict': 'off'})
    assert r_main.status_code == 200, r_main.text
    # 任意独立主账号（非 user_id=1）也可配置
    other_client = _make_client(4)
    r_other = other_client.put('/api/v1/platform-profile/douyin', json={'memory_restrict': 'relationship'})
    assert r_other.status_code == 200, r_other.text
