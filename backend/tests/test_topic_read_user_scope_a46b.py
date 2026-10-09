# -*- coding: utf-8 -*-
'''A46② 守卫：话题**读侧文本出口**也带 user 过滤（与写侧 A46① 同口径）。

事实：conversation_topics.user_id 是 NOT NULL；写侧 update_topic_resolution（A46①）与结构化
出口 load_active_topics_rows（A43）都带 user 过滤，只剩两个文本出口 load_active_topics_text /
load_fresh_active_topics_text 仍只按 character_id + status
⇒ 一个角色服务多账号时，A 账号的进行中话题会注入 B 账号的上下文。
现网 blast radius 为 0（没有一角色多用户的会话）——本单只修形状；user_id 为 None 时保持旧行为。

（项目未装 pytest-asyncio，统一 asyncio.run；临时库走 tests/_dbclone.py，绝不碰生产库。）
'''
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import topic_tracker as tracker_mod
from app.agent.topic_tracker import (
    load_active_topics_rows,
    load_active_topics_text,
    load_fresh_active_topics_text,
    update_topic_resolution,
)

TOPIC_A = '交周报'      # 1 号账号的进行中话题
TOPIC_B = '学吉他'      # 2 号账号的进行中话题（与 TOPIC_A 无 4 字公共子串，写侧不会互相点名）


@pytest.fixture()
def topic_db(monkeypatch, tmp_path):
    engine = clone_engine(tmp_path / 'a46b_topics.db')
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
            db.add(User(id=1, username='a46bu1', nickname='甲'))
            db.add(User(id=2, username='a46bu2', nickname='乙'))
            db.add(AICharacter(id=1, user_id=1, name='A46b 角色', personality='稳',
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


def _topics_of(uid):
    '''经结构化出口（A43 那条）取话题名列表——证明文本出口与它同口径。'''
    return [r['topic'] for r in asyncio.run(load_active_topics_rows(1, uid))]


def test_文本出口只装自己账号的进行中话题(topic_db):
    '''一个角色两个账号：A 的注入文本不含 B 的话题（旧 SQL 只按 character_id ⇒ 必然串进来）。'''
    _seed(topic_db, [(1, 1, TOPIC_A, 5), (2, 2, TOPIC_B, 30)])
    text_u1 = asyncio.run(load_active_topics_text(1, 1))
    assert TOPIC_A in text_u1, '带 caller 时自己的话题没渲染出来 → 下面的 not in 会是空断言'
    assert TOPIC_B not in text_u1, 'A 账号的注入文本串到了 B 账号的进行中话题'

    text_u2 = asyncio.run(load_active_topics_text(1, 2))
    assert TOPIC_B in text_u2 and TOPIC_A not in text_u2, '反向串号（B 拿到 A 的话题）'


def test_无caller时保持旧行为(topic_db):
    '''user_id=None ⇒ 不加过滤（fail-open 旧行为），仍能拿到全部进行中话题；不许静默变空。'''
    _seed(topic_db, [(1, 1, TOPIC_A, 5), (2, 2, TOPIC_B, 30)])
    text = asyncio.run(load_active_topics_text(1, None))
    assert TOPIC_A in text and TOPIC_B in text, '无 caller 被收紧成「一条都拿不到」（注入块整块消失）'


def test_时效出口同口径(topic_db):
    '''主动接触那条（load_fresh_active_topics_text）此前形参不进 SQL，与主聊天同修。'''
    _seed(topic_db, [(1, 1, TOPIC_A, 5), (2, 2, TOPIC_B, 30)])
    fresh_u1 = asyncio.run(load_fresh_active_topics_text(1, 1))
    assert TOPIC_A in fresh_u1, '带 caller 时时效出口没渲染出来 → 反证会是空断言'
    assert TOPIC_B not in fresh_u1, '主动接触串到别的账号的进行中话题'
    fresh_none = asyncio.run(load_fresh_active_topics_text(1, None))
    assert TOPIC_A in fresh_none and TOPIC_B in fresh_none, '时效出口的 None 腿被静默收紧'


def test_读写口径一致(topic_db):
    '''写侧关掉 1 号的话题后，三个出口（文本/时效/结构化）与库内 status 一致，且 2 号不受影响。'''
    _seed(topic_db, [(1, 1, TOPIC_A, 5), (2, 2, TOPIC_B, 30)])
    # 「弄好了」没点名话题 ⇒ 走兜底关最近一条（1 号那条更近）；写侧带 user 过滤 ⇒ 只关 1 号
    asyncio.run(update_topic_resolution(1, 1, '弄好了'))
    assert _status(topic_db, 1) == '完成', '写侧兜底没关掉 1 号的话题（本例的读侧断言会失真）'
    assert _status(topic_db, 2) == '进行中', '写侧把 2 号的话题一起关了'

    assert TOPIC_A not in asyncio.run(load_active_topics_text(1, 1)), '读侧仍注入已被写侧关闭的话题'
    assert TOPIC_A not in asyncio.run(load_fresh_active_topics_text(1, 1)), '时效读侧仍注入已关闭的话题'
    assert _topics_of(1) == [], '结构化出口与文本出口不一致'
    assert _topics_of(2) == [TOPIC_B], '2 号的出口被误清空'
