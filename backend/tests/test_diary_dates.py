# -*- coding: utf-8 -*-
"""角色详情页 v2：按月查询有日记日期接口（GET /api/v1/diary/{character_id}/dates）验证。

用临时 SQLite 文件库（不触碰 backend/data），seed 一个用户角色 + 该角色若干日记，
校验 dates 接口返回 200、只返回该月日期、格式 YYYY-MM-DD、跨月隔离、参数校验。
"""
import asyncio
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import diary as diary_api
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.models.character import AICharacter
from app.models.life import AIDiary

USER = 1


@pytest.fixture()
def diary_db():
    """临时 SQLite 文件库（不触碰 backend/data），seed 一个角色 + 该角色若干日记。"""
    tmp = tempfile.mkdtemp(prefix='char_diary_dates_')
    db_path = os.path.join(tmp, 't.db')
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}', poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    async def _seed():
        async with factory() as db:
            char = AICharacter(user_id=USER, name='测试')
            db.add(char)
            await db.commit()
            await db.refresh(char)
            for d in ['2026-08-01', '2026-08-15', '2026-08-31', '2026-09-02']:
                db.add(AIDiary(character_id=char.id, diary_date=d, content=f'内容 {d}'))
            await db.commit()
            return char.id

    char_id = asyncio.run(_seed())
    yield factory, char_id
    engine.sync_engine.dispose()


def _make_client(factory, user_id=USER) -> TestClient:
    app = FastAPI()
    app.include_router(diary_api.router)

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


def test_diary_dates_returns_month_dates(diary_db):
    """GET /dates?month=2026-08 返回该月有日记的日期（升序、格式 YYYY-MM-DD）。"""
    factory, char_id = diary_db
    r = _make_client(factory).get(f'/api/v1/diary/{char_id}/dates', params={'month': '2026-08'})
    assert r.status_code == 200
    body = r.json()
    assert body['dates'] == ['2026-08-01', '2026-08-15', '2026-08-31']


def test_diary_dates_isolates_months(diary_db):
    """不同月份互不干扰；无日记月份返回空列表。"""
    factory, char_id = diary_db
    client = _make_client(factory)
    sep = client.get(f'/api/v1/diary/{char_id}/dates', params={'month': '2026-09'})
    assert sep.status_code == 200
    assert sep.json()['dates'] == ['2026-09-02']
    empty = client.get(f'/api/v1/diary/{char_id}/dates', params={'month': '2026-07'})
    assert empty.status_code == 200
    assert empty.json()['dates'] == []


def test_diary_dates_requires_valid_month_format(diary_db):
    """month 不符合 YYYY-MM 时返回 422（FastAPI Query pattern 校验）。"""
    factory, char_id = diary_db
    r = _make_client(factory).get(f'/api/v1/diary/{char_id}/dates', params={'month': '2026-8'})
    assert r.status_code == 422
