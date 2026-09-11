# -*- coding: utf-8 -*-
"""§4.5（2026-09-09）：朋友圈归档页 N+1 治理单测（临时 SQLite，不动真实库）。

原 ``list_moments_archive`` 在循环内逐条查 AICharacter / User / MomentLike /
``_likers_for_moment``（后者每条又 3~4 次查询），最多 200 条 ≈ 上千次查询。

覆盖：
- ``_batch_likers`` 与逐条 ``_likers_for_moment`` 结果逐字段等价（总赞数 + 名字列表与顺序）；
- 归档接口返回结构逐字段等价（character_id or 0 / author_tz_offset / liked_by_me /
  likers / likes_count），且查询次数是常数级（不随动态条数线性增长）。
"""
import asyncio
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import moments as moments_api
from app.models.character import AICharacter
from app.models.life import AIMoment, MomentAILike, MomentLike
from app.models.user import User

NOW = datetime(2026, 9, 9, 12, 0, 0)


# 快测档（2026-09-12）：本文件是重量级/集成型用例（每例起一次临时库，约 3s/例），打 slow 标记。
# 全量默认照跑；日常开发用 pytest -m "not slow" 跳过本档（见 docs/engineering-protocol.md 十八）。
pytestmark = pytest.mark.slow

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
                AICharacter(id=11, user_id=1, name="小鹿", is_active=True, timezone_offset=8),
                AICharacter(id=12, user_id=1, name="小狼", is_active=True, timezone_offset=0),
                AIMoment(id=101, character_id=11, user_id=1, sender_type="ai",
                         content="AI 动态", likes_count=2, is_active=True,
                         created_at=NOW),
                AIMoment(id=102, character_id=None, user_id=1, sender_type="user",
                         content="用户动态", likes_count=0, is_active=True,
                         created_at=NOW - timedelta(days=1)),
                AIMoment(id=103, character_id=12, user_id=1, sender_type="ai",
                         content="AI 动态2", likes_count=1, is_active=True,
                         created_at=NOW - timedelta(days=1)),
                MomentLike(id=1, moment_id=101, user_id=1),
                MomentLike(id=2, moment_id=101, user_id=2),
                MomentLike(id=3, moment_id=103, user_id=2),
                MomentAILike(id=1, moment_id=101, character_id=12),
                MomentAILike(id=2, moment_id=103, character_id=11),
            ])
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr(moments_api, "append_domain_event", lambda *a, **k: None)
    yield engine, factory
    engine.sync_engine.dispose()


def _count_queries(engine, factory, coro_factory):
    """执行 coro_factory(session) 并统计期间真实下发到 SQLite 的语句数。"""
    n = {"c": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        n["c"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before)
    try:
        async def _go():
            async with factory() as db:
                return await coro_factory(db)

        return asyncio.run(_go()), n["c"]
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before)


def test_批量likers与逐条等价(env):
    engine, factory = env

    async def _go(db):
        moments = (await db.execute(select(AIMoment).order_by(AIMoment.id))).scalars().all()
        batched = await moments_api._batch_likers(db, moments)
        per_item = {m.id: await moments_api._likers_for_moment(db, m) for m in moments}
        return batched, per_item, [m.id for m in moments]

    (batched, per_item, mids), _ = _count_queries(engine, factory, _go)
    assert mids == [101, 102, 103]
    assert batched == per_item
    assert batched[101] == (3, ["小明", "小红", "小狼"])   # likes_count 2 + AI 赞 1
    assert batched[102] == (0, [])
    assert batched[103] == (2, ["小红", "小鹿"])          # likes_count 1 + AI 赞 1


def test_归档返回结构逐字段等价(env):
    engine, factory = env

    async def _go(db):
        return await moments_api.list_moments_archive(db=db, user_id=1)

    res, _ = _count_queries(engine, factory, _go)
    assert res["total_days"] == 2
    flat = [m for d in res["days"] for m in d["moments"]]
    by_id = {m["id"]: m for m in flat}
    assert set(by_id) == {101, 102, 103}

    a = by_id[101]
    assert a["character_id"] == 11 and a["character_name"] == "小鹿"
    assert a["user_id"] == 1 and a["sender_type"] == "ai"
    assert a["author_tz_offset"] == 8
    assert a["likes_count"] == 3 and a["likers"] == ["小明", "小红", "小狼"]
    assert a["liked_by_me"] is True          # 用户 1 赞过 101
    assert a["is_active"] is True and a["created_at"] == NOW.isoformat()

    b = by_id[102]
    assert b["character_id"] == 0            # 用户动态 → 哨兵 0（与原实现一致）
    assert b["character_name"] == "小明" and b["user_id"] == 1
    assert b["sender_type"] == "user" and b["author_tz_offset"] == 8
    assert b["likes_count"] == 0 and b["likers"] == [] and b["liked_by_me"] is False

    c = by_id[103]
    assert c["character_name"] == "小狼" and c["author_tz_offset"] == 0
    assert c["likes_count"] == 2 and c["likers"] == ["小红", "小鹿"]
    assert c["liked_by_me"] is False         # 赞 103 的是用户 2


def test_归档查询次数为常数级(env):
    """20 条动态下总语句数仍是个位数（原实现随条数线性增长）。"""
    engine, factory = env

    async def _more():
        async with factory() as db:
            for i in range(200, 220):
                db.add(AIMoment(id=i, character_id=11, user_id=1, sender_type="ai",
                                content=f"批量{i}", likes_count=0, is_active=True,
                                created_at=NOW - timedelta(days=i - 199)))
                db.add(MomentLike(id=1000 + i, moment_id=i, user_id=2))
                db.add(MomentAILike(id=1000 + i, moment_id=i, character_id=12))
            await db.commit()

    asyncio.run(_more())

    async def _go(db):
        return await moments_api.list_moments_archive(db=db, user_id=1)

    res, queries = _count_queries(engine, factory, _go)
    total = sum(d["count"] for d in res["days"])
    assert total == 23                       # 3 条原始 + 20 条新增全部返回
    assert queries < 15, f"查询次数 {queries} 未降到常数级"


def test_今日AI评论数_批量父评论判定等价(monkeypatch):
    """同批顺手优化：子评论的父评论判定由「每条新开 session」改为一次批量查询，语义等价。

    计数口径：顶级评论计 1；子评论仅当父评论存在且为 AI 所发时计 1（回复用户的不计）。
    """
    from datetime import datetime, timedelta, timezone

    from app.application import moment_service as ms
    from app.models.life import MomentComment

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        # id, parent_id, sender_type
        (1, None, "ai"),      # 顶级 → 计
        (2, 1, "ai"),         # 父为 AI 评论 → 计
        (3, 1, "ai"),         # 父为 AI 评论 → 计
        (4, 90, "ai"),        # 父是用户评论（存在但非 ai）→ 不计
        (5, 91, "ai"),        # 父不存在 → 不计
    ]
    parents = {1: "ai", 90: "user"}  # id 1 是 AI 顶级评论；id 90 存在但 sender_type=user

    class _Res:
        def __init__(self, r):
            self._r = r

        def scalars(self):
            return self

        def all(self):
            return self._r

    class _Sess:
        def __init__(self):
            self.queries = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt, *a, **k):
            self.queries += 1
            s = str(stmt)
            if "id IN" in s or "id IN (" in s:
                # 批量父评论查询：只返回 sender_type == "ai" 的父 id
                return _Res([i for i in parents if parents[i] == "ai"])
            return _Res([
                MomentComment(id=i, parent_id=p, sender_type=st, moment_id=1,
                              sender_id=7, content="c", created_at=now - timedelta(minutes=1))
                for i, p, st in rows
            ])

    sess = _Sess()
    monkeypatch.setattr(ms, "async_session_factory", lambda: sess)
    assert asyncio.run(ms._get_today_ai_comment_count_for_char(7)) == 3
    assert sess.queries == 2  # 1 次评论列表 + 1 次父评论批量（原为 1 + 子评论条数）


def test_空归档返回空结构(env):
    engine, factory = env

    async def _go(db):
        return await moments_api.list_moments_archive(db=db, user_id=999)

    res, _ = _count_queries(engine, factory, _go)
    assert res == {"days": [], "total_days": 0}
