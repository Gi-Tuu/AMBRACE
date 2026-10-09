# -*- coding: utf-8 -*-
'''A46① 守卫：话题**写侧**也带 user 过滤。

事实（10-09 只读核实）：conversation_topics.user_id 是 NOT NULL；读侧 chat_settlement
早就是 (character_id, user_id, status)，而写侧 update_topic_resolution 只按 character_id + status
⇒ 一个角色服务多账号时，B 账号说句「弄好了」可能把 A 账号的进行中话题置完成。
现网 blast radius 为 0（没有一角色多用户的会话）——本单只修形状；user_id 为 None 时保持旧行为。
'''
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import topic_tracker as tracker_mod
from app.agent.topic_tracker import update_topic_resolution


@pytest.fixture()
def topic_db(monkeypatch, tmp_path):
    engine = clone_engine(tmp_path / 'a46_topics.db')
    factory = make_session_factory(engine)
    _sessions = []

    def _make(*a, **kw):
        s = factory(*a, **kw)
        _sessions.append(s)
        return s

    async def _init():
        from app.models.character import AICharacter
        from app.models.user import User
        async with _make() as db:
            db.add(User(id=1, username='a46u1', nickname='甲'))
            db.add(User(id=2, username='a46u2', nickname='乙'))
            db.add(AICharacter(id=1, user_id=1, name='A46 角色', personality='稳',
                               chat_style='口语化', relation_type='朋友', is_active=True))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr(tracker_mod, 'async_session_factory', _make)
    yield _make

    async def _teardown():
        for s in _sessions:
            try:
                await s.close()
            except Exception:
                pass
        await engine.dispose()

    asyncio.run(_teardown())


def _seed(factory, rows):
    '''rows = [(id, user_id, topic, minutes_ago)]，全部是 char 1 的「进行中」话题。'''
    from app.models.memory import ConversationTopic
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            for tid, uid, topic, mins in rows:
                db.add(ConversationTopic(id=tid, character_id=1, user_id=uid, topic=topic,
                                         status='进行中', progress='进行中', importance=0.6,
                                         last_touched_at=now_naive_utc() - timedelta(minutes=mins)))
            await db.commit()

    asyncio.run(_run())


def _status(factory, tid):
    from app.models.memory import ConversationTopic

    async def _run():
        async with factory() as db:
            row = (await db.execute(
                select(ConversationTopic).where(ConversationTopic.id == tid)
            )).scalar_one_or_none()
            return row.status if row else None

    return asyncio.run(_run())


def test_兜底路径只关自己账号的话题(topic_db):
    # user1 的话题更新（1 分钟前）⇒ 旧代码的兜底会关它；user2 的话题是 30 分钟前
    _seed(topic_db, [(1, 1, '去面试新生', 1), (2, 2, '复习搞定了', 30)])
    asyncio.run(update_topic_resolution(1, 2, '弄好了'))
    assert _status(topic_db, 1) == '进行中', '别人的话题被写侧关掉了'
    assert _status(topic_db, 2) == '完成'


def test_点名别人的话题也不许关(topic_db):
    _seed(topic_db, [(3, 1, '去面试新生', 5)])
    asyncio.run(update_topic_resolution(1, 2, '面试搞定了'))
    assert _status(topic_db, 3) == '进行中'


def test_无caller时保持旧行为(topic_db):
    # user_id=None ⇒ 不加过滤（fail-open 旧行为），兜底仍关最近一条；不许静默收紧成「一条都不关」
    _seed(topic_db, [(4, 1, '去面试新生', 1)])
    asyncio.run(update_topic_resolution(1, None, '弄好了'))
    assert _status(topic_db, 4) == '完成'