# -*- coding: utf-8 -*-
"""T6 朋友圈首页 feed 批量化回归（v3.4.6 第三轮 §5.2，2026-09-10）。

原 list_moments 主循环 N+1（每条 AI 动态查角色 2 次 + 逐条查用户/点赞/点赞人）；本次循环前
一次性 IN 预取。断言与原行为逐字段等价：赞数 / likers 顺序 / liked_by_me / 停用角色过滤。
"""
import asyncio
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import moments as moments_api
from app.models.character import AICharacter
from app.models.life import AIMoment, MomentAILike, MomentLike
from app.models.user import User

NOW = datetime(2026, 9, 10, 12, 0, 0)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    tmp = str(tmp_path)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([
                User(id=1, username="u1", nickname="小明", password_hash="x"),
                User(id=2, username="u2", nickname="小红", password_hash="x"),
                # 小鹿/小狼 active；阿呆停用（其动态应被过滤）
                AICharacter(id=11, user_id=1, name="小鹿", is_active=True, timezone_offset=8),
                AICharacter(id=12, user_id=1, name="小狼", is_active=True, timezone_offset=0),
                AICharacter(id=13, user_id=1, name="阿呆", is_active=False),
                AIMoment(id=101, character_id=11, user_id=1, sender_type="ai",
                         content="小鹿发的动态", likes_count=2, is_active=True, created_at=NOW),
                AIMoment(id=102, character_id=None, user_id=1, sender_type="user",
                         content="用户自己发的动态", likes_count=0, is_active=True,
                         created_at=NOW - timedelta(days=1)),
                AIMoment(id=103, character_id=13, user_id=1, sender_type="ai",
                         content="停用角色的动态", likes_count=0, is_active=True,
                         created_at=NOW - timedelta(days=2)),
                AIMoment(id=104, character_id=12, user_id=1, sender_type="ai",
                         content="小狼发的动态", likes_count=1, is_active=True,
                         created_at=NOW - timedelta(days=3)),
                MomentLike(id=1, moment_id=101, user_id=1),
                MomentLike(id=2, moment_id=101, user_id=2),
                MomentLike(id=3, moment_id=102, user_id=2),
                MomentAILike(id=1, moment_id=101, character_id=12),
                MomentAILike(id=2, moment_id=104, character_id=11),
            ])
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr(moments_api, "append_domain_event", lambda *a, **k: None)
    yield engine, factory
    engine.sync_engine.dispose()


def _feed(env, user_id=1):
    engine, factory = env
    async def _go():
        async with factory() as db:
            return await moments_api.list_moments(skip=0, limit=50, db=db, user_id=user_id)
    return asyncio.run(_go())


def test_feed_批量化_赞数字段等价(env):
    res = _feed(env)
    by_id = {m.id: m for m in res.moments}
    # 停用角色（阿呆）动态被过滤
    assert 103 not in by_id
    # 101：likes_count 2 + AI 赞 1 = 3；likers 顺序 = 用户赞(created_at asc)+AI 赞(created_at asc)
    m101 = by_id[101]
    assert m101.likes_count == 3
    assert m101.likers == ["小明", "小红", "小狼"]   # 用户赞的昵称 + AI 赞的角色名
    assert m101.liked_by_me is True                  # 用户1 赞过 101
    # 102：用户自己动态，likes_count 0，likers=[小红]（用户2 赞），liked_by_me False
    m102 = by_id[102]
    assert m102.likes_count == 0
    assert m102.likers == ["小红"]
    assert m102.liked_by_me is False
    # 104：likes_count 1 + AI 赞 1 = 2；likers=[小鹿]
    m104 = by_id[104]
    assert m104.likes_count == 2
    assert m104.likers == ["小鹿"]
    assert m104.liked_by_me is False
    # 作者时区：AI 取角色 timezone_offset（小狼=0），用户默认 8
    assert by_id[101].author_tz_offset == 8
    assert by_id[102].author_tz_offset == 8
    assert by_id[104].author_tz_offset == 0
