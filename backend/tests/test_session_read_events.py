# -*- coding: utf-8 -*-
"""v3.4.6 审查 G1 · F-14：mark_session_read 事件流水单测。

覆盖：
- 联动标记的同角色其他活跃会话各自落 chat.session_read 事件（聚合各自 session，
  原实现只挂传入 session_id）；
- 幂等键 = session + UTC 自然日（原完整时间戳完全不幂等，高频已读膨胀）——同日重复只一条；
- 联动标记不污染 updated_at（沿用原生 SQL 的历史坑约束）。

（临时 SQLite 只建 chat_sessions + domain_events 两表；flag 经 monkeypatch store 打开，
事件走真实 append_domain_event 落临时库以验证唯一键幂等。）
"""
import asyncio
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.application import chat_service as cs
from app.events import store as st
from app.models.chat import ChatSession
from app.models.domain_event import DomainEvent


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    tmp = str(tmp_path)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(ChatSession.__table__.create, checkfirst=True)
            await conn.run_sync(DomainEvent.__table__.create, checkfirst=True)

    asyncio.run(_init())
    monkeypatch.setattr(st, "async_session_factory", factory)
    monkeypatch.setattr(st, "domain_events_enabled", lambda: True)
    yield factory
    engine.sync_engine.dispose()


def _mk_sessions(db, updated_at_marker):
    """user=1 / char=2：会话 10、11 活跃；12 不活跃；13 活跃但属其它角色。"""
    db.add(ChatSession(id=10, user_id=1, character_id=2, is_active=True,
                       updated_at=updated_at_marker))
    db.add(ChatSession(id=11, user_id=1, character_id=2, is_active=True,
                       updated_at=updated_at_marker))
    db.add(ChatSession(id=12, user_id=1, character_id=2, is_active=False,
                       updated_at=updated_at_marker))
    db.add(ChatSession(id=13, user_id=1, character_id=3, is_active=True,
                       updated_at=updated_at_marker))


def test_联动会话各落事件_幂等键按自然日(tmp_db):
    from datetime import datetime, timezone

    marker = datetime(2026, 1, 1, 0, 0, 0)

    async def _run():
        async with tmp_db() as db:
            _mk_sessions(db, marker)
            await db.commit()
        # 第一次已读：会话 10 传入 → 联动 11 一并标记，各自落事件
        async with tmp_db() as db:
            assert await cs.mark_session_read(db, 10, 1) is True
        async with tmp_db() as db:
            rows = (await db.execute(
                select(DomainEvent).where(DomainEvent.event_type == "chat.session_read")
            )).scalars().all()
        assert sorted(r.aggregate_id for r in rows) == [10, 11]
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert sorted(r.idempotency_key for r in rows) == [
            f"chat.session_read:10:{day}", f"chat.session_read:11:{day}"]
        assert all(r.actor_type == "user" and r.actor_id == 1 for r in rows)

        # 同日重复已读（另一入口会话）：幂等键相同 → 唯一键冲突静默，仍各 1 条
        async with tmp_db() as db:
            assert await cs.mark_session_read(db, 11, 1) is True
        async with tmp_db() as db:
            rows2 = (await db.execute(
                select(DomainEvent).where(DomainEvent.event_type == "chat.session_read")
            )).scalars().all()
        assert len(rows2) == 2

    asyncio.run(_run())


def test_联动标记不污染updated_at(tmp_db):
    from datetime import datetime

    marker = datetime(2026, 1, 1, 0, 0, 0)

    async def _run():
        async with tmp_db() as db:
            _mk_sessions(db, marker)
            await db.commit()
        async with tmp_db() as db:
            await cs.mark_session_read(db, 10, 1)
        async with tmp_db() as db:
            rows = (await db.execute(select(ChatSession))).scalars().all()
        for s in rows:
            assert s.updated_at == marker, f"session {s.id} updated_at 被联动标记污染"
            if s.id in (10, 11):
                assert s.last_read_at is not None
            else:
                assert s.last_read_at is None, f"session {s.id} 不应被标记已读"

    asyncio.run(_run())


def test_会话不存在返回False(tmp_db):
    async def _run():
        async with tmp_db() as db:
            assert await cs.mark_session_read(db, 999, 1) is False
        async with tmp_db() as db:
            rows = (await db.execute(select(DomainEvent))).scalars().all()
        assert rows == []

    asyncio.run(_run())
