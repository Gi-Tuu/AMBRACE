# -*- coding: utf-8 -*-
"""批 F-F3（2026-09-26）：过期未来口吻记忆「只观察」扫描——只统计、绝不改数据。

落点说明：派单写的 backend/app/memory/maintenance.py 不存在，长周期记忆维护的调度函数是
app/memory/maintenance_schedule.py 的 run_if_due（记忆衰减 + AI 评星同拍），扫描加在该文件内。

纪律：tmp_path 私有临时库（_dbclone 页级克隆）；不连生产库；用例前后校验库内容零变化。
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.memory.maintenance_schedule import scan_stale_future_memories
from app.utils.timeutil import app_tz_offset_hours, now_naive_utc, shift_utc_naive

_OFF = app_tz_offset_hours()


def _local_ago(days: int, hour: int = 12, minute: int = 0) -> datetime:
    """「本地今天往前 days 天」的某个墙钟时刻 → 库内 UTC naive。"""
    today_local = shift_utc_naive(now_naive_utc(), _OFF).date()
    d = today_local - timedelta(days=days)
    return shift_utc_naive(datetime(d.year, d.month, d.day, hour, minute), -_OFF)


@pytest.fixture
def mem_db(tmp_path):
    """私有记忆库（char 1/2 + user 1），返回会话工厂。"""
    engine = clone_engine(os.path.join(str(tmp_path), "f3_memories.db"))
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
            db.add(AICharacter(id=2, user_id=1, name="sam", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    yield _make

    async def _teardown():
        for s in _sessions:
            try:
                await s.close()
            except Exception:
                pass
        await engine.dispose()

    asyncio.run(_teardown())


def _add(factory, *, mid, char, mtype, content, status="active", created_at=None):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            db.add(Memory(id=mid, user_id=1, character_id=char, memory_type=mtype,
                          content=content, status=status, scope="private",
                          created_at=created_at or _local_ago(5)))
            await db.commit()

    asyncio.run(_run())


def _snapshot(factory):
    """全表快照（用于「扫描不改数据」断言）。"""
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            rows = (await db.execute(
                select(Memory.id, Memory.status, Memory.memory_type, Memory.content,
                       Memory.created_at, Memory.updated_at).order_by(Memory.id)
            )).fetchall()
            return [tuple(r) for r in rows]

    return asyncio.run(_run())


def _scan(factory):
    return asyncio.run(scan_stale_future_memories(session_factory=factory))


def test_stale_future_candidate_is_listed(mem_db):
    """active + user_info + 含「明天」+ 本地日早于今天 ≥2 天 → 进候选（角色分桶）。"""
    _add(mem_db, mid=101, char=1, mtype="user_info",
         content="用户明天要面试新生", created_at=_local_ago(3))
    result = _scan(mem_db)
    assert list(result) == [1]
    assert [mid for mid, _s in result[1]] == [101]
    assert result[1][0][1] == "用户明天要面试新生"


def test_recent_memories_not_candidates(mem_db):
    """未满 2 个本地日的不算候选（今天/昨天写下的「明天」仍然成立）。"""
    _add(mem_db, mid=201, char=1, mtype="user_info", content="用户明天要面试新生",
         created_at=_local_ago(1, hour=9))
    _add(mem_db, mid=202, char=1, mtype="event", content="用户明天要面试新生",
         created_at=_local_ago(0, hour=9))
    assert _scan(mem_db) == {}


def test_local_day_boundary(mem_db):
    """判据按本地日历日：本地前天 23:30 写下的算候选，本地昨天 00:30 的不算。"""
    _add(mem_db, mid=301, char=1, mtype="insight", content="用户明天要面试新生",
         created_at=_local_ago(2, hour=23, minute=30))
    _add(mem_db, mid=302, char=1, mtype="insight", content="用户明天要面试新生",
         created_at=_local_ago(1, hour=0, minute=30))
    result = _scan(mem_db)
    assert [mid for mid, _s in result.get(1, [])] == [301]


def test_type_and_status_and_word_filters(mem_db):
    """类型不在白名单 / 非 active / 无短日未来词 / 长跨度（下周）一律不进候选。"""
    _add(mem_db, mid=401, char=1, mtype="preference", content="用户明天想早点睡")
    _add(mem_db, mid=402, char=1, mtype="user_info", content="用户明天要面试新生",
         status="superseded")
    _add(mem_db, mid=403, char=1, mtype="event", content="用户上周面试了新生")
    _add(mem_db, mid=404, char=1, mtype="user_info", content="用户下周要交报告")
    assert _scan(mem_db) == {}


def test_grouping_per_character_and_sample_limit(mem_db):
    """按角色分桶统计；摘要截断 30 字。"""
    for i, char in ((501, 1), (502, 1), (503, 2)):
        _add(mem_db, mid=i, char=char, mtype="event",
             content="用户明天要面试新生" + "补" * 40)
    result = _scan(mem_db)
    assert sorted(result) == [1, 2]
    assert [mid for mid, _s in result[1]] == [501, 502]
    assert [mid for mid, _s in result[2]] == [503]
    assert len(result[2][0][1]) == 30


def test_scan_is_read_only(mem_db):
    """只观察：扫描前后全表逐列一致（不改 status/updated_at，也不写任何行）。"""
    _add(mem_db, mid=601, char=1, mtype="user_info", content="用户明天要面试新生")
    _add(mem_db, mid=602, char=2, mtype="event", content="用户明早要交材料")
    before = _snapshot(mem_db)
    _scan(mem_db)
    assert _snapshot(mem_db) == before


def test_scan_failure_is_isolated(mem_db, caplog):
    """异常隔离：会话工厂炸了也不抛（不影响维护主流程），返回空。"""
    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    with caplog.at_level(logging.WARNING, logger="memory.maintenance"):
        result = asyncio.run(scan_stale_future_memories(session_factory=_boom))
    assert result == {}
    assert any("Stale-future memory scan failed" in r.message for r in caplog.records)


def test_scan_logs_info_per_character(mem_db, caplog):
    """命中候选时打 INFO（角色 + 条数 + 前 3 条 id 与 30 字摘要）。"""
    _add(mem_db, mid=701, char=1, mtype="user_info", content="用户明天要面试新生")
    with caplog.at_level(logging.INFO, logger="memory.maintenance"):
        _scan(mem_db)
    lines = [r.getMessage() for r in caplog.records if "Stale-future memory candidates" in r.message]
    assert len(lines) == 1
    assert "char=1" in lines[0] and "count=1" in lines[0] and "701" in lines[0]
