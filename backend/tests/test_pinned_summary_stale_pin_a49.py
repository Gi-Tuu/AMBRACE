# -*- coding: utf-8 -*-
'''A49 回归：被 status 过滤掉的 stale 置顶会把「印象／画像」重生成卡死在唯一索引上。

现场（2026-10-09，用户报「印象重新生成失败」）：
  - 生产库里 5 条 status=stale 的记忆仍 is_pinned=1, is_archived=0（id 5104／5557／6649／5972／7417）；
  - 代码查「已有置顶」带 status == active（current_facts_active_only 默认开）⇒ 看不见它们 ⇒ 走 INSERT 分支；
  - DB 部分唯一索引 ux_memories_pinned_active 只认 is_pinned=1 AND is_archived=0 ⇒ INSERT 撞唯一约束
    （sqlite3.IntegrityError: UNIQUE constraint failed: index ux_memories_pinned_active）⇒ 该桶永久生成失败。
修法＝插入前按**索引口径**放开同桶旧置顶（summary._release_bucket_pins），只改 is_pinned、不删行、不动内容。

本文件在临时库里**真建那条部分唯一索引**（模型层刻意没有它，见迁移 d1a2b3c4e5f6 的说明），
所以「老代码会撞、新代码不撞」是可验证的：把 _release_bucket_pins 摘掉，前两条用例当场红。
'''
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text

from _dbclone import clone_engine, make_session_factory

from app.memory import summary as _summary
from app.models.character import AICharacter
from app.models.memory import Memory
from app.models.user import User

pytestmark = pytest.mark.slow

CHAR_ID = 301
USER_ID = 1
NEW_TEXT = 'A49 重生成后的新印象'
NEW_IDENTITY = 'A49 重生成后的新画像'
STALE_SUMMARY = '这条是 stale 的旧印象（仍挂着置顶）'
STALE_IDENTITY = '这条是 stale 的旧画像（仍挂着置顶）'
MATERIAL = '用户说过这周要交实验报告'

# 与迁移 d1a2b3c4e5f6 逐字同口径的部分唯一索引（模型层没有它，所以这里手工建）
INDEX_DDL = '''CREATE UNIQUE INDEX IF NOT EXISTS ux_memories_pinned_active
ON memories (character_id, memory_type,
CASE WHEN sub_type IS NULL OR sub_type = 'summary' THEN '' ELSE sub_type END)
WHERE is_pinned = 1 AND is_archived = 0'''


@pytest.fixture()
def env(monkeypatch, tmp_path):
    engine = clone_engine(tmp_path / 'a49.db')
    factory = make_session_factory(engine)

    async def _seed_owner():
        async with factory() as db:
            db.add(User(id=USER_ID, username='a49_u1', nickname='A49 用户'))
            db.add(AICharacter(id=CHAR_ID, user_id=USER_ID, name='A49 角色'))
            await db.commit()

    asyncio.run(_seed_owner())
    monkeypatch.setattr(_summary, 'async_session_factory', factory)

    box = {'responses': []}

    async def _llm(messages=None, **_kw):
        queue = box['responses']
        return queue.pop(0) if queue else NEW_TEXT

    monkeypatch.setattr('app.agent.llm_client.chat_completion', _llm)

    async def _v2_true(*_a, **_kw):
        return True

    monkeypatch.setattr('app.memory.flags.memory_v2_enabled', _v2_true)
    yield {'engine': engine, 'factory': factory, 'box': box}
    asyncio.run(engine.dispose())


def _make_index(engine):
    async def _run():
        async with engine.begin() as conn:
            await conn.execute(text(INDEX_DDL))
    asyncio.run(_run())


def _add(factory, *, content, mtype='user_info', sub_type=None, pinned=False, status=None, why=None):
    async def _run():
        async with factory() as db:
            m = Memory(user_id=USER_ID, character_id=CHAR_ID, memory_type=mtype, sub_type=sub_type,
                       source='chat', content=content, importance=50.0, is_pinned=pinned,
                       why_it_matters=why)
            if status is not None:
                m.status = status
            db.add(m)
            await db.commit()
            return m.id
    return asyncio.run(_run())


def _all(factory):
    async def _run():
        async with factory() as db:
            return list((await db.execute(
                select(Memory).where(Memory.character_id == CHAR_ID)
            )).scalars().all())
    return asyncio.run(_run())


def _pinned_in_bucket(factory, mtype='user_info', bucket=''):
    return [r for r in _all(factory)
            if r.is_pinned and r.memory_type == mtype
            and _summary._bucket_of(r.sub_type) == bucket]


def test_印象桶有_stale_置顶时能重生成(env):
    _make_index(env['engine'])
    stale_id = _add(env['factory'], content=STALE_SUMMARY, pinned=True, status='stale')
    _add(env['factory'], content=MATERIAL)
    env['box']['responses'].append(NEW_TEXT)
    out = asyncio.run(_summary.summarize_memories(CHAR_ID, 'user_info', force=True))
    assert out['generated'] is True, out
    pins = _pinned_in_bucket(env['factory'], 'user_info', '')
    assert len(pins) == 1, [r.id for r in pins]
    assert pins[0].content == NEW_TEXT and pins[0].id != stale_id
    old = [r for r in _all(env['factory']) if r.id == stale_id]
    assert old and old[0].is_pinned is False, '旧 stale 置顶没被放开'


def test_画像桶有_stale_置顶时能重生成(env):
    _make_index(env['engine'])
    stale_id = _add(env['factory'], content=STALE_IDENTITY, sub_type='identity',
                    pinned=True, status='stale')
    _add(env['factory'], content=MATERIAL)
    env['box']['responses'].append(NEW_IDENTITY)
    out = asyncio.run(_summary.summarize_identity(CHAR_ID, USER_ID, force=True))
    assert out['generated'] is True, out
    pins = _pinned_in_bucket(env['factory'], 'user_info', 'identity')
    assert len(pins) == 1 and pins[0].id != stale_id, [r.id for r in pins]
    assert pins[0].content == NEW_IDENTITY


def test_索引在测试库里真的生效(env):
    # 没这条，前两条用例即使不修也会绿（等于没牙）——所以先证明索引真拦得住第二条同桶置顶
    _make_index(env['engine'])
    _add(env['factory'], content='第一条置顶', pinned=True)
    with pytest.raises(Exception) as ei:
        _add(env['factory'], content='第二条同桶置顶', pinned=True)
    assert 'ux_memories_pinned_active' in str(ei.value), str(ei.value)[:200]


def test_别的桶不被误放开(env):
    _make_index(env['engine'])
    ev_id = _add(env['factory'], content='事件桶的置顶', mtype='event', sub_type='summary', pinned=True)
    _add(env['factory'], content=STALE_SUMMARY, pinned=True, status='stale')
    _add(env['factory'], content=MATERIAL)
    env['box']['responses'].append(NEW_TEXT)
    out = asyncio.run(_summary.summarize_memories(CHAR_ID, 'user_info', force=True))
    assert out['generated'] is True, out
    ev = [r for r in _all(env['factory']) if r.id == ev_id][0]
    assert ev.is_pinned is True, '别的桶的置顶被误放开了'