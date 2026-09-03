# -*- coding: utf-8 -*-
"""Ariadne 模块 A 集成测试：search_memories 时间路（memory_temporal_recall flag）。

真实临时库种子：两条「时间对、语义弱」的记忆（不在查询词的语义路命中范围），
flag 开 + time_range → 时间专属记忆进入结果；flag 关 → 与旧版一致（不进）。
"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.memory.retrieve import search_memories


@pytest.fixture()
def tdb(monkeypatch):
    """临时 SQLite 文件库：patch app.db.database.async_session_factory（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix="timeq_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    from app.memory import service as memsvc
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "async_session_factory", factory)

    async def _seed():
        now = datetime.utcnow()
        async with factory() as db:
            # 时间专属：2026-07 窗口内、与查询词「猫」语义无关（LIKE/向量都不命中）
            db.add(__import__("app.models.memory", fromlist=["Memory"]).Memory(
                character_id=7, user_id=1, memory_type="event", content="七月去了青岛看海",
                importance=60.0, created_at=datetime(2026, 7, 15)))
            # 同窗口高重要度：应排在前面
            db.add(__import__("app.models.memory", fromlist=["Memory"]).Memory(
                character_id=7, user_id=1, memory_type="event", content="七月初搬家了",
                importance=80.0, created_at=datetime(2026, 7, 2)))
            # 窗口外：不应进时间路
            db.add(__import__("app.models.memory", fromlist=["Memory"]).Memory(
                character_id=7, user_id=1, memory_type="event", content="五月看展",
                importance=90.0, created_at=datetime(2026, 5, 20)))
            await db.commit()

    asyncio.run(_seed())
    yield factory
    engine.sync_engine.dispose()


def test_时间路_flag开_时间专属记忆进结果(tdb, monkeypatch):
    from app.agent import loop
    monkeypatch.setattr(loop, "AGENT_FLAGS", {**loop.AGENT_FLAGS, "memory_temporal_recall": True})
    tr = (datetime(2026, 7, 1), datetime(2026, 8, 1))
    out = asyncio.run(search_memories(character_id=7, query="完全无关的查询词量子冲浪",
                                      limit=5, time_range=tr))
    contents = {m["content"] for m in out}
    assert "七月初搬家了" in contents  # 窗口内重要度 80 → 时间路命中
    assert "七月去了青岛看海" in contents
    assert "五月看展" not in contents  # 窗口外不进
    # 排序：重要度高的在前（时间路与语义路合并后统一 rerank）
    ids = [m["content"] for m in out]
    assert ids.index("七月初搬家了") < ids.index("七月去了青岛看海")


def test_时间路_flag关_零行为变化(tdb, monkeypatch):
    from app.agent import loop
    monkeypatch.setattr(loop, "AGENT_FLAGS", {**loop.AGENT_FLAGS, "memory_temporal_recall": False})
    tr = (datetime(2026, 7, 1), datetime(2026, 8, 1))
    out = asyncio.run(search_memories(character_id=7, query="完全无关的查询词量子冲浪",
                                      limit=5, time_range=tr))
    # flag 关：时间路不生效，语义路无命中 → 结果不含时间专属记忆（与旧版一致）
    assert all("七月" not in m["content"] for m in out)


def test_时间路_无区间参数_零行为变化(tdb):
    out = asyncio.run(search_memories(character_id=7, query="搬家", limit=5))
    # 不传 time_range（默认 None）：行为与旧版一致（该查询走 LIKE 兜底可命中「七月初搬家了」）
    assert any("搬家" in m["content"] for m in out)
