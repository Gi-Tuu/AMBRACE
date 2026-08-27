# -*- coding: utf-8 -*-
"""认知循环开关（cognitive_loop_enabled）schema/API 回归（2026-08-24）。

覆盖点：
- CharacterUpdate / CharacterResponse 均含 cognitive_loop_enabled 字段。
- PUT /api/v1/characters/{id} 更新 cognitive_loop_enabled=true/false 生效并持久化。
- PUT 不传该字段时不影响其他字段（旧客户端兼容）。
- GET /api/v1/characters/{id} 响应含该字段。
"""
import asyncio
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import characters as characters_api
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.models.character import AICharacter

USER = 1


@pytest.fixture()
def char_db():
    """临时 SQLite 文件库（不触碰 backend/data），种子一个角色并返回 (factory, character_id)。"""
    tmp = tempfile.mkdtemp(prefix='char_cognitive_')
    db_path = os.path.join(tmp, 't.db')
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}', poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    async def _seed_and_get_id():
        async with factory() as db:
            char = AICharacter(user_id=USER, name='测试', cognitive_loop_enabled=False)
            db.add(char)
            await db.commit()
            await db.refresh(char)
            return char.id

    char_id = asyncio.run(_seed_and_get_id())
    yield factory, char_id
    engine.sync_engine.dispose()


def _make_client(factory, user_id=USER) -> TestClient:
    app = FastAPI()
    app.include_router(characters_api.router)

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def test_character_schema_has_cognitive_loop_enabled():
    """CharacterUpdate/CharacterResponse 均须暴露该字段（前端回写依赖）。"""
    from app.schemas.character import CharacterUpdate, CharacterResponse
    assert 'cognitive_loop_enabled' in CharacterUpdate.model_fields
    assert 'cognitive_loop_enabled' in CharacterResponse.model_fields


def test_get_character_response_contains_field(char_db):
    """GET 详情响应含 cognitive_loop_enabled，默认关闭。"""
    factory, char_id = char_db
    r = _make_client(factory).get(f'/api/v1/characters/{char_id}')
    assert r.status_code == 200
    body = r.json()
    assert 'cognitive_loop_enabled' in body
    assert body['cognitive_loop_enabled'] is False


def test_put_cognitive_loop_enabled_true(char_db):
    """PUT cognitive_loop_enabled=true 生效并持久化。"""
    factory, char_id = char_db
    client = _make_client(factory)
    r = client.put(f'/api/v1/characters/{char_id}', json={'cognitive_loop_enabled': True})
    assert r.status_code == 200
    assert r.json()['cognitive_loop_enabled'] is True
    g = client.get(f'/api/v1/characters/{char_id}')
    assert g.json()['cognitive_loop_enabled'] is True


def test_put_cognitive_loop_enabled_false(char_db):
    """PUT cognitive_loop_enabled=false 生效。"""
    factory, char_id = char_db
    r = _make_client(factory).put(f'/api/v1/characters/{char_id}', json={'cognitive_loop_enabled': False})
    assert r.status_code == 200
    assert r.json()['cognitive_loop_enabled'] is False


def test_put_without_field_keeps_other_fields_and_default(char_db):
    """旧客户端不传该字段：只更新传入字段，cognitive_loop_enabled 维持默认 False。"""
    factory, char_id = char_db
    client = _make_client(factory)
    r = client.put(f'/api/v1/characters/{char_id}', json={'name': '测试改名'})
    assert r.status_code == 200
    body = r.json()
    assert body['name'] == '测试改名'
    assert body['cognitive_loop_enabled'] is False


def test_update_character_invalidates_persona_baseline_cache(char_db):
    """P2-1：角色性格/说话风格编辑成功后，进程内人格基线缓存被清除（下次读取按新人格重算）。"""
    from app.services import character_state_service as cs
    factory, char_id = char_db
    # 模拟已预热的人格基线缓存（对应 edit 前的旧人格）
    cs._persona_baseline[char_id] = cs._derive_persona_baseline("高冷内敛", "简洁寡言")
    try:
        r = _make_client(factory).put(
            f'/api/v1/characters/{char_id}',
            json={'personality': '热情开朗', 'chat_style': '活泼俏皮'},
        )
        assert r.status_code == 200
        assert char_id not in cs._persona_baseline  # 编辑成功后缓存已失效
    finally:
        cs._persona_baseline.pop(char_id, None)
