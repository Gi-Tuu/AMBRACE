# -*- coding: utf-8 -*-
"""C7-R1：无 source 请求的入口锚点快照与 ROWID 复用反例。

主审反例（真实临时 SQLite、真实 ChatMessage 模型）：
- 捕获入口快照后删除最高消息行；SQLite 普通 ROWID 会复用该编号；
- 晚到用户消息即使拿到旧编号、且 created_at 与请求参考同秒（秒精度），
  也不得穿越请求边界成为“上次用户发言”。

本文件同时保留不复用的负对照，并覆盖缺 caller 不查库、查询失败安全省略、
以及“捕获的旧锚点行本身被删除/编号复用”时保留快照时间的安全口径。
"""
import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.models import AICharacter, Base, ChatSession
from app.utils.timeutil import now_naive_utc
from test_chat_time_anchor_c7 import _add_message, _seed_base, _state  # noqa: F401


@pytest.fixture()
def c7_db(tmp_path, monkeypatch):
    db_path = (tmp_path / "c7_reuse.db").as_posix()
    engine = create_async_engine("sqlite+aiosqlite:///" + db_path, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    from app.db import database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield {"engine": engine, "factory": factory}
    asyncio.run(engine.dispose())


@pytest.mark.parametrize("reuse", [False, True])
def test_request_snapshot_does_not_accept_late_reused_message(c7_db, monkeypatch, reuse):
    """主审反例严格成功断言：复用最高 ROWID 的晚到消息不得成为原请求锚点。"""
    from app.agent.context import chat_time
    from app.models.chat import ChatMessage

    # SQLite CURRENT_TIMESTAMP 无小数；请求参考时刻带微秒。晚到消息用同一秒插入。
    reference = datetime(2026, 10, 21, 12, 0, 0, 500000)
    monkeypatch.setattr(chat_time, "now_naive_utc", lambda: reference)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=reference)
        old = await _add_message(factory, 1, "user", "OLD", reference - timedelta(days=5))
        last_ai = await _add_message(
            factory, 1, "ai", "AI", reference - timedelta(days=5) + timedelta(seconds=1),
        )
        boundary = await chat_time.capture_request_anchor(1, 1)
        snapshot = boundary["chat_time_anchor_snapshot"]
        # 快照取“最后一条用户发言”，不是最后一条消息（AI id 不是锚点）
        assert snapshot["status"] == chat_time.STATUS_OK
        assert snapshot["message_id"] == old
        assert snapshot["created_at"] == reference - timedelta(days=5)

        if reuse:
            async with factory() as db:
                await db.execute(delete(ChatMessage).where(ChatMessage.id == last_ai))
                await db.commit()
        late = await _add_message(
            factory, 1, "user", "LATE", reference.replace(microsecond=0),
        )
        assert (late == last_ai) is reuse, "测试前置：只有删除最高行才应复用 ROWID"

        anchor = await chat_time.resolve_chat_time_anchor(_state(**boundary))
        assert anchor.status == chat_time.STATUS_OK
        assert anchor.previous_message_id == old
        assert anchor.previous_message_id != late
        assert anchor.previous_at == reference - timedelta(days=5)
        assert anchor.gap_seconds == 5 * 86400
        assert chat_time.format_gap_text(anchor) == "5 天前"

    asyncio.run(run())


def test_captured_anchor_survives_anchor_row_delete_and_rowid_reuse(c7_db, monkeypatch):
    """捕获的旧锚点行本身被删除、编号被复用时：保留快照时间，不重查成新行。"""
    from app.agent.context import chat_time
    from app.models.chat import ChatMessage

    reference = datetime(2026, 10, 21, 12, 0, 0, 500000)
    monkeypatch.setattr(chat_time, "now_naive_utc", lambda: reference)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=reference)
        old = await _add_message(factory, 1, "user", "OLD", reference - timedelta(days=5))
        last_ai = await _add_message(
            factory, 1, "ai", "AI", reference - timedelta(days=5) + timedelta(seconds=1),
        )
        boundary = await chat_time.capture_request_anchor(1, 1)
        assert boundary["chat_time_anchor_snapshot"]["message_id"] == old

        # 删除被捕获的旧用户行与最高 AI 行；删除后插入的晚到用户消息复用 id=old。
        async with factory() as db:
            await db.execute(delete(ChatMessage).where(ChatMessage.id.in_([old, last_ai])))
            await db.commit()
        late = await _add_message(
            factory, 1, "user", "LATE_REUSE_OLD", reference.replace(microsecond=0),
        )
        assert late == old, "测试前置：删除全部行后新插入应复用旧 id"

        anchor = await chat_time.resolve_chat_time_anchor(_state(**boundary))
        # 快照是历史事实：旧锚点身份已不可证，但时间仍保留，不能变成“刚刚”。
        assert anchor.status == chat_time.STATUS_OK
        assert anchor.previous_at == reference - timedelta(days=5)
        assert anchor.gap_seconds == 5 * 86400
        assert chat_time.format_gap_text(anchor) == "5 天前"

    asyncio.run(run())


def test_capture_missing_caller_or_character_does_not_query(c7_db, monkeypatch):
    """缺 caller/角色：入口快照为空且不发起用户数据查询。"""
    from app.agent.context import chat_time
    from app.db import database as db_mod

    def _boom():
        raise AssertionError("缺 caller/角色时不应查询用户数据")

    monkeypatch.setattr(db_mod, "async_session_factory", _boom)

    async def run():
        a = await chat_time.capture_request_anchor(None, 1)
        b = await chat_time.capture_request_anchor(1, None)
        for boundary in (a, b):
            assert boundary["chat_time_reference_at"] is not None
            assert boundary["chat_time_anchor_snapshot"] is None
        anchor_a = await chat_time.resolve_chat_time_anchor(_state(user_id=None, **a))
        anchor_b = await chat_time.resolve_chat_time_anchor(_state(character_id=None, **b))
        assert anchor_a.status == chat_time.STATUS_MISSING_CALLER
        assert anchor_b.status == chat_time.STATUS_MISSING_CONTEXT
        assert anchor_a.gap_seconds is None and anchor_b.gap_seconds is None

    asyncio.run(run())


def test_capture_cross_session_and_identity_isolation(c7_db):
    """入口快照跨私聊会话取最后用户发言，但严格隔离 user_id/character_id。"""
    from app.agent.context.chat_time import STATUS_OK, capture_request_anchor

    now = now_naive_utc()

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=now)  # user1 × char1 × session1
        old1 = await _add_message(
            factory, 1, "user", "OLD1", now - timedelta(days=5),
        )
        async with factory() as db:
            db.add(ChatSession(id=2, user_id=1, character_id=1, created_at=now, updated_at=now))
            await db.commit()
        newer1 = await _add_message(
            factory, 2, "user", "NEWER1", now - timedelta(hours=1),
        )
        await _seed_base(factory, now=now, user_id=2, character_id=2, session_id=3)
        user2 = await _add_message(
            factory, 3, "user", "USER2", now - timedelta(minutes=1),
        )
        async with factory() as db:
            db.add(AICharacter(id=3, user_id=1, name="C3"))
            db.add(ChatSession(id=4, user_id=1, character_id=3, created_at=now, updated_at=now))
            await db.commit()
        char3 = await _add_message(
            factory, 4, "user", "CHAR3", now - timedelta(minutes=2),
        )

        b11 = await capture_request_anchor(1, 1)
        b22 = await capture_request_anchor(2, 2)
        b13 = await capture_request_anchor(1, 3)
        # 同用户×角色跨会话：取 session2 的较新用户发言，不取旧会话
        assert b11["chat_time_anchor_snapshot"]["status"] == STATUS_OK
        assert b11["chat_time_anchor_snapshot"]["message_id"] == newer1
        assert b11["chat_time_anchor_snapshot"]["message_id"] != old1
        # 跨账号/跨角色：快照只能取本 user×character 的消息
        assert b22["chat_time_anchor_snapshot"]["message_id"] == user2
        assert b13["chat_time_anchor_snapshot"]["message_id"] == char3
        assert len({newer1, user2, char3}) == 3

    asyncio.run(run())


def test_capture_query_failure_is_safe_omission(c7_db, monkeypatch):
    """入口查询失败：快照标 query_failed，解析安全省略，不抛异常。"""
    from app.agent.context import chat_time
    from app.db import database as db_mod

    def _boom():
        raise RuntimeError("synthetic db failure")

    monkeypatch.setattr(db_mod, "async_session_factory", _boom)

    async def run():
        boundary = await chat_time.capture_request_anchor(1, 1)
        assert boundary["chat_time_anchor_snapshot"]["status"] == chat_time.STATUS_QUERY_FAILED
        anchor = await chat_time.resolve_chat_time_anchor(_state(**boundary))
        assert anchor.status == chat_time.STATUS_QUERY_FAILED
        assert anchor.gap_seconds is None
        assert chat_time.format_gap_text(anchor) is None

    asyncio.run(run())
