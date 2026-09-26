# -*- coding: utf-8 -*-
"""批 F-F2（2026-09-26）：working_state 不再复读「已翻日的未来口吻」。

覆盖纯函数 expire_stale_future_clauses：短跨度未来词 + 本地日翻日 → 判过期；
长跨度（下周/下个月）、今天写下、无未来词、时间戳缺失/解析失败 → 一律保留。
另覆盖写入侧（working_state_service）：落库前的过期 ongoing 条目被剔除，别的桶不受影响。

时间构造走项目 timeutil（本地墙钟 → UTC naive），不手搓偏移，也不依赖具体时区配置。
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.memory.working_state import expire_stale_future_clauses
from app.utils.timeutil import app_tz_offset_hours, now_naive_utc, shift_utc_naive

_OFF = app_tz_offset_hours()


def _utc_naive_at_local(year, month, day, hour=12, minute=0):
    """本地墙钟时间 → 库内 UTC naive（shift_utc_naive 的反向用法）。"""
    return shift_utc_naive(datetime(year, month, day, hour, minute), -_OFF)


_NOW = _utc_naive_at_local(2026, 9, 26, 11)          # 本地 09-26 上午
_YESTERDAY = _utc_naive_at_local(2026, 9, 25, 12)     # 本地 09-25 中午
_LAST_WEEK = _utc_naive_at_local(2026, 9, 20, 9)      # 本地上周日


def _item(topic, detail, updated_at):
    return {"topic": topic, "detail": detail, "evidence_ids": [1], "updated_at": updated_at}


def test_stale_future_clause_dropped_when_day_rolled():
    """detail 含「明天」且 updated_at=昨天 → 判过期并剔除。"""
    items = [_item("面试新生", "明天要面试新生", _YESTERDAY.isoformat())]
    kept, expired = expire_stale_future_clauses(items, now=_NOW)
    assert kept == [] and len(expired) == 1
    assert expired[0]["detail"] == "明天要面试新生"


def test_future_clause_written_today_is_kept():
    """今天写下的「明天」还没到 → 保留（今天的明天还没到）。"""
    today = _utc_naive_at_local(2026, 9, 26, 9)
    items = [_item("面试新生", "明天要面试新生", today.isoformat())]
    kept, expired = expire_stale_future_clauses(items, now=_NOW)
    assert len(kept) == 1 and expired == []


def test_long_span_not_expired():
    """长跨度表述（下周）不参与判过期，避免误杀真实未来计划。"""
    items = [_item("交报告", "下周要交报告", _LAST_WEEK.isoformat())]
    kept, expired = expire_stale_future_clauses(items, now=_NOW)
    assert len(kept) == 1 and expired == []


def test_no_future_word_is_kept():
    """正文无未来词 → 保留（旧条目也可能仍然 ongoing）。"""
    items = [_item("复习", "最近每天晚上复习高数", _LAST_WEEK.isoformat())]
    kept, expired = expire_stale_future_clauses(items, now=_NOW)
    assert len(kept) == 1 and expired == []


def test_local_day_boundary_is_authoritative():
    """判据按「本地日历日」：本地今天凌晨写下的保留，本地昨晚写下的过期（哪怕 UTC 已翻日）。"""
    just_after_local_midnight = _utc_naive_at_local(2026, 9, 26, 0, 30)
    just_before_local_midnight = _utc_naive_at_local(2026, 9, 25, 23, 30)
    items = [
        _item("明早进场", "明早面试进场前记得带材料", just_after_local_midnight.isoformat()),
        _item("topic 里写着明天", "提醒带简历", just_before_local_midnight.isoformat()),
    ]
    kept, expired = expire_stale_future_clauses(items, now=_NOW)
    assert len(kept) == 1 and len(expired) == 1
    assert kept[0]["updated_at"] == just_after_local_midnight.isoformat()
    assert expired[0]["detail"] == "提醒带简历"   # topic 命中未来词同样判过期


def test_missing_or_unparsable_timestamp_is_conservative():
    """updated_at 缺失/非 ISO 串 → 保守保留（不误删）。"""
    items = [
        _item("面试新生", "明天要面试新生", None),
        _item("面试新生", "明天要面试新生", "昨天"),
    ]
    kept, expired = expire_stale_future_clauses(items, now=_NOW)
    assert len(kept) == 2 and expired == []


# ───────────────────────── 写入侧（服务层，真实临时库） ─────────────────────────

@pytest.fixture
def ws_db(monkeypatch, tmp_path):
    """私有工作记忆库：打桩全局会话工厂 + 打开 working_state 开关、关掉 trace 埋点。"""
    engine = clone_engine(os.path.join(str(tmp_path), "f2_ws.db"))
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
            db.add(AICharacter(id=11, user_id=1, name="酱", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", _make)
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "working_state_enabled", True)
    monkeypatch.setitem(AGENT_FLAGS, "memory_trace_debug", False)  # 避免后台埋点写库泄漏连接
    yield _make

    async def _teardown():
        for s in _sessions:
            try:
                await s.close()
            except Exception:
                pass
        await engine.dispose()

    asyncio.run(_teardown())


def _seed_state(factory, stale_item_iso):
    """预置：最新 working_state 行（节流窗口已过）含「明天…」旧条目 + 本轮新增证据记忆 700。"""
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            db.add(Memory(id=700, user_id=1, character_id=11, memory_type="event",
                          content="用户说面试是前天的事了", scope="private"))
            db.add(Memory(id=800, user_id=1, character_id=11, memory_type="working_state",
                          content=json.dumps({
                              "version": 1,
                              "ongoing": [{"topic": "面试新生", "detail": "明天要面试新生",
                                           "evidence_ids": [700], "updated_at": stale_item_iso}],
                              "relationship_notes": [],
                              "open_questions": [],
                          }, ensure_ascii=False),
                          status="active", scope="private", source="system",
                          created_at=now_naive_utc() - timedelta(hours=2)))
            await db.commit()

    asyncio.run(_run())


def _ws_rows(factory):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            rows = (await db.execute(
                select(Memory).where(Memory.memory_type == "working_state").order_by(Memory.id)
            )).scalars().all()
            return [(r.id, r.status, json.loads(r.content)) for r in rows]

    return asyncio.run(_run())


@pytest.mark.slow
def test_write_side_drops_stale_future_ongoing(ws_db, monkeypatch, caplog):
    """过期「明天」条目被携带进本轮期望三桶时：落库行不再含它，其它桶照常写入，并打 INFO。"""
    from app.application import working_state_service as svc

    stale_iso = (now_naive_utc() - timedelta(days=2)).isoformat()
    _seed_state(ws_db, stale_iso)

    async def fake_completion(**_kw):
        # LLM 输出「完整期望三桶」：旧 ongoing 原样保留 + 新增一条关系备注（触发写入）
        return json.dumps({
            "ongoing": [{"topic": "面试新生", "detail": "明天要面试新生", "evidence_ids": [700]}],
            "relationship_notes": [{"note": "用户在意面试这件事", "evidence_ids": [700]}],
            "open_questions": [],
        }, ensure_ascii=False)

    monkeypatch.setattr(svc._llm, "chat_completion", fake_completion)
    with caplog.at_level(logging.INFO, logger="memory.working_state"):
        asyncio.run(svc.maybe_evaluate_working_state(1, 11, None, "面试早过了", "嗯"))

    rows = _ws_rows(ws_db)
    assert len(rows) == 2, "新行照常滚动写入"
    new_content = rows[-1][2]
    assert new_content["ongoing"] == [], "已翻日的「明天」条目不得再落库"
    assert [n["note"] for n in new_content["relationship_notes"]] == ["用户在意面试这件事"]
    lines = [r.getMessage() for r in caplog.records if "stale-future ongoing" in r.getMessage()]
    assert len(lines) == 1 and "char=11" in lines[0] and "n=1" in lines[0]
