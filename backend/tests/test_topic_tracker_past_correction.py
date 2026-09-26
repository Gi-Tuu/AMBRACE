# -*- coding: utf-8 -*-
"""批 F-F1（2026-09-26）：话题跟踪认「过去时间纠正」——能熄灭指针，且不误关、不兜底。

背景：conversation_topics 里「去面试新生」长期卡「进行中」，因为完成词表只认
「装好了/写完了/搞定了」，不认「不是今天的事了 / 别再提」这类纠正话术。

纪律：tmp_path 私有临时库（_dbclone 页级克隆）+ 打桩 topic_tracker 会话工厂；不连生产库。
"""
import asyncio
import os

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import topic_tracker as tracker_mod
from app.agent.topic_tracker import update_topic_resolution


@pytest.fixture
def topic_db(monkeypatch, tmp_path):
    """私有话题库：char=1 / user=1，会话工厂打桩进 topic_tracker。"""
    engine = clone_engine(os.path.join(str(tmp_path), "f1_topics.db"))
    factory = make_session_factory(engine)
    _sessions: list = []

    def _make(*args, **kwargs):
        s = factory(*args, **kwargs)
        _sessions.append(s)
        return s

    async def _init():
        from app.models.character import AICharacter
        from app.models.user import User
        async with _make() as db:
            db.add(User(id=1, username="u1", nickname="用户"))
            db.add(AICharacter(id=1, user_id=1, name="轩", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr(tracker_mod, "async_session_factory", _make)
    yield _make

    async def _teardown():
        for s in _sessions:
            try:
                await s.close()
            except Exception:
                pass
        await engine.dispose()

    asyncio.run(_teardown())


def _seed(factory, topics):
    """topics = [(id, topic, status, progress)]"""
    from app.models.memory import ConversationTopic

    async def _run():
        async with factory() as db:
            for tid, topic, status, progress in topics:
                db.add(ConversationTopic(id=tid, character_id=1, user_id=1, topic=topic,
                                         status=status, progress=progress, importance=0.6))
            await db.commit()

    asyncio.run(_run())


def _state(factory, topic):
    from app.models.memory import ConversationTopic

    async def _run():
        async with factory() as db:
            row = (await db.execute(
                select(ConversationTopic).where(ConversationTopic.topic == topic)
            )).scalar_one_or_none()
            return (row.status, row.progress) if row else None

    return asyncio.run(_run())


def test_past_correction_closes_named_topic(topic_db):
    """「面试不是今天的事了」→ 被点名的进行中话题置完成（status/progress 一致）。"""
    _seed(topic_db, [(1, "去面试新生", "进行中", "进行中")])
    asyncio.run(update_topic_resolution(1, 1, "面试不是今天的事了"))
    assert _state(topic_db, "去面试新生") == ("完成", "完成")


def test_past_correction_dont_mention_again(topic_db):
    """「别再提面试了」同样熄灭指针。"""
    _seed(topic_db, [(2, "去面试新生", "进行中", None)])
    asyncio.run(update_topic_resolution(1, 1, "别再提面试了"))
    assert _state(topic_db, "去面试新生") == ("完成", "完成")


def test_past_correction_does_not_close_unrelated_topic(topic_db):
    """不误伤：纠正话术里没提该话题 → 话题保持进行中（且没有完成词，绝不兜底）。"""
    _seed(topic_db, [(3, "去面试新生", "进行中", None)])
    asyncio.run(update_topic_resolution(1, 1, "今晚吃什么"))
    assert _state(topic_db, "去面试新生") == ("进行中", None)


def test_past_correction_no_fallback(topic_db):
    """不兜底：只有不相干话题进行中 + 「这事早过了」→ 不得被关闭。"""
    _seed(topic_db, [(4, "买卡", "进行中", None)])
    asyncio.run(update_topic_resolution(1, 1, "这事早过了"))
    assert _state(topic_db, "买卡") == ("进行中", None)


def test_past_correction_stopword_overlap_not_enough(topic_db):
    """保守判据：只共享虚词碎片（「的事」对「这事」）不算点名，不关话题。"""
    _seed(topic_db, [(5, "买水的事", "进行中", None)])
    asyncio.run(update_topic_resolution(1, 1, "这事过去了，别再提"))
    assert _state(topic_db, "买水的事") == ("进行中", None)


def test_completion_word_fallback_still_works(topic_db):
    """回归保护：原完成词「搞定」的兜底语义未被改动（用户省略话题时关最近一条）。"""
    _seed(topic_db, [(6, "复习高数", "进行中", "进行中")])
    asyncio.run(update_topic_resolution(1, 1, "搞定了终于"))
    assert _state(topic_db, "复习高数") == ("完成", "完成")
