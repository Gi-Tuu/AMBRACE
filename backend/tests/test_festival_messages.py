# -*- coding: utf-8 -*-
# 七夕死循环修复测试（2026-08-20）：
# - generate_birthday/holiday/anniversary_message 对 _gen_with_reasoning 的 tuple 返回值兼容（挡位 2）
# - str 返回值（挡位 0/1）行为不变
# - arbiter festival 分支生成失败时标记当日已处理（防每 30 秒无限重试）
import asyncio
import os
import tempfile

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.scheduler import message_generator as mg


@pytest.fixture()
def msg_db(monkeypatch):
    '''临时库：patch async_session_factory（不触碰 backend/data）'''
    tmp = tempfile.mkdtemp(prefix='festival_test_')
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
    # arbiter 模块级绑定（from app.db.database import async_session_factory）需单独 patch
    import app.scheduler.arbiter as arbiter_mod
    monkeypatch.setattr(arbiter_mod, 'async_session_factory', factory)
    yield factory
    engine.sync_engine.dispose()


def _patch_gen(monkeypatch, result):
    async def _fake_gen(messages, character_id, user_id, temperature=0.8, max_tokens=400):
        return result
    monkeypatch.setattr(mg, '_gen_with_reasoning', _fake_gen)


def test_birthday_tuple_compat(monkeypatch):
    '''挡位 2：_gen_with_reasoning 返回 (content, reasoning) tuple，应取 content'''
    _patch_gen(monkeypatch, ('生日快乐！', '思考过程'))
    out = asyncio.run(mg.generate_birthday_message('小阳', '活泼', '用户', character_id=11, user_id=1))
    assert out == '生日快乐！'


def test_holiday_tuple_compat(monkeypatch):
    _patch_gen(monkeypatch, ('七夕快乐！', '想了一下'))
    out = asyncio.run(mg.generate_holiday_message('小阳', '活泼', '用户', '七夕节', character_id=11, user_id=1))
    assert out == '七夕快乐！'


def test_anniversary_tuple_compat(monkeypatch):
    _patch_gen(monkeypatch, ('认识你 100 天啦！', '感慨'))
    out = asyncio.run(mg.generate_anniversary_message('小阳', '活泼', '用户', 100, character_id=11, user_id=1))
    assert out == '认识你 100 天啦！'


def test_str_return_unchanged(monkeypatch):
    '''挡位 0/1：返回 str 行为不变'''
    _patch_gen(monkeypatch, '早安！')
    out = asyncio.run(mg.generate_holiday_message('小阳', '活泼', '用户', '七夕节', character_id=11, user_id=1))
    assert out == '早安！'


def test_tuple_empty_content_fallback(monkeypatch):
    '''tuple content 为空时回退空串，不抛异常'''
    _patch_gen(monkeypatch, (None, '想'))
    out = asyncio.run(mg.generate_holiday_message('小阳', '活泼', '用户', '七夕节', character_id=11, user_id=1))
    assert out == ''


def test_arbiter_festival_failure_marks_log(msg_db, monkeypatch):
    '''arbiter 节日分支生成异常时：返回 False 且写 ProactiveMessageLog（当天不再重试）'''
    async def _boom(*a, **kw):
        raise RuntimeError('boom')
    monkeypatch.setattr(mg, 'generate_holiday_message', _boom)
    from app.scheduler import arbiter
    from app.models.proactive_settings import ProactiveMessageLog
    cand = dict(character_id=11, user_id=1, session_id=1, character_name='小阳',
                character_personality='活泼', nickname='用户',
                holiday_name='七夕节')
    item = {'type': 'holiday', 'candidate': cand}
    ok = asyncio.run(arbiter._execute(item))
    assert ok is False
    async def _check():
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            rows = (await db.execute(select(ProactiveMessageLog))).scalars().all()
            return rows
    rows = asyncio.run(_check())
    assert len(rows) == 1
    assert rows[0].message_type == 'holiday'
    assert rows[0].content.startswith('[send_failed]')

