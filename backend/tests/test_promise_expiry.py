# -*- coding: utf-8 -*-
"""§4.3（2026-09-09）：定时承诺运行期过期上限单测。

背景：``get_due_events`` 原只判 ``trigger_at <= now``，无宽限上限也不回收过期事件；
只有启动时的 ``recover_overdue_events`` 按 GRACE_MINUTES(=120) 回收。服务器长期不重启时，
被每小时限额/配额/DND 反复挡下（未 mark_fired、仍 pending）的事件会一直 due，
限制解除后补发严重过期的「我回来了/我办完事了」，造成剧情穿帮。

覆盖（临时 SQLite + monkeypatch 模块级 async_session_factory，不动真实库）：
- 3h 前到期的 pending → 运行期标 expired 且不再返回；
- 30min / 119min 前到期的 → 仍返回且保持 pending；
- 未到期的 → 不返回、状态不变；
- 回收幂等：二次调用不重复处理，且已 fired/expired 的不被改写。
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.life import ScheduledEvent
from app.scheduling import promise_service as ps


@pytest.fixture()
def tmp_factory(monkeypatch, tmp_path):
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

    asyncio.run(_init())
    monkeypatch.setattr(ps, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def _add(factory, minutes_ago: float, status: str = "pending") -> int:
    """插入一条 trigger_at = now - minutes_ago 的事件（naive UTC，与实库存法一致）"""

    async def _go():
        async with factory() as db:
            e = ScheduledEvent(
                user_id=1, character_id=1, session_id=1,
                trigger_at=datetime.now(timezone.utc).replace(tzinfo=None)
                - timedelta(minutes=minutes_ago),
                status=status, owner="ai", event_type="back",
            )
            db.add(e)
            await db.commit()
            return e.id

    return asyncio.run(_go())


def _status(factory, eid: int) -> str:
    async def _go():
        async with factory() as db:
            return (await db.execute(
                select(ScheduledEvent.status).where(ScheduledEvent.id == eid)
            )).scalar()

    return asyncio.run(_go())


def test_过期3h_运行期标expired且不返回(tmp_factory):
    eid = _add(tmp_factory, 180)
    due = asyncio.run(ps.get_due_events())
    assert due == []
    assert _status(tmp_factory, eid) == "expired"


def test_2h内到期仍返回(tmp_factory):
    a = _add(tmp_factory, 30)
    b = _add(tmp_factory, 119)
    due = asyncio.run(ps.get_due_events())
    assert {e.id for e in due} == {a, b}
    assert _status(tmp_factory, a) == "pending"
    assert _status(tmp_factory, b) == "pending"


def test_宽限边界_刚过界即回收(tmp_factory):
    """判定是严格 ``> GRACE_MINUTES``：刚过界（+1min）即回收，界内（GRACE-1）仍返回。"""
    over = _add(tmp_factory, ps.GRACE_MINUTES + 1)
    inside = _add(tmp_factory, ps.GRACE_MINUTES - 1)
    due = asyncio.run(ps.get_due_events())
    assert [e.id for e in due] == [inside]
    assert _status(tmp_factory, over) == "expired"
    assert _status(tmp_factory, inside) == "pending"


def test_未到期不返回且状态不变(tmp_factory):
    eid = _add(tmp_factory, -30)
    due = asyncio.run(ps.get_due_events())
    assert due == []
    assert _status(tmp_factory, eid) == "pending"


def test_回收幂等且不改已终结事件(tmp_factory):
    stale = _add(tmp_factory, 240)
    fired = _add(tmp_factory, 240, status="fired")
    expired = _add(tmp_factory, 240, status="expired")
    assert asyncio.run(ps.get_due_events()) == []
    assert asyncio.run(ps.get_due_events()) == []  # 二次调用不报错、不重复处理
    assert _status(tmp_factory, stale) == "expired"
    assert _status(tmp_factory, fired) == "fired"
    assert _status(tmp_factory, expired) == "expired"


def test_混合场景_只回收过宽限的(tmp_factory):
    old = _add(tmp_factory, 300)
    fresh = _add(tmp_factory, 10)
    future = _add(tmp_factory, -60)
    due = asyncio.run(ps.get_due_events())
    assert [e.id for e in due] == [fresh]
    assert _status(tmp_factory, old) == "expired"
    assert _status(tmp_factory, future) == "pending"


def test_recover_overdue_events_仍可补触发宽限内事件(tmp_factory):
    """启动恢复路径行为不变：宽限内的仍记为 recovered（不标 expired）"""
    eid = _add(tmp_factory, 45)
    asyncio.run(ps.recover_overdue_events())
    assert _status(tmp_factory, eid) == "pending"


def test_create_event_aware_trigger_落库前归naive(tmp_factory):
    """§3（P2-1 时间口径收敛）：create_event 通过 to_naive_utc 把 aware trigger_at
    归一为 naive UTC 再写裸 DateTime 列，杜绝 aware 写裸列（PG 前瞻）。"""
    info = {
        "user_id": 1,
        "character_id": 1,
        "session_id": 1,
        "trigger_at": datetime.now(timezone.utc) + timedelta(minutes=10),  # aware
        "sender": "ai",
    }
    e = asyncio.run(ps.create_event(info))
    assert e is not None
    assert e.trigger_at.tzinfo is None  # 已归一为 naive UTC
    # 落库后回读，与 naive UTC 值等价（SQLite 字符串比较不变）
    assert e.trigger_at > datetime.now(timezone.utc).replace(tzinfo=None)
