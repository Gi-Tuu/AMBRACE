# -*- coding: utf-8 -*-
"""C7 时间锚点 helper 回归（正常/轻量共用）。

覆盖：
- source 路径：当前消息排除、跨会话、AI/主动消息不计、同秒 id 稳定序、并发 A/B；
- 非法 source：缺失/已删除/AI/跨账号/跨角色/跨会话 → 明确 invalid_source，不降级读历史；
- 无 source 路径：请求入口捕获的最后用户发言快照、边界后并发消息排除、无历史/边界缺失/未来时间；
- 缺 caller/角色、UTC/时区、展示桶位。

全部使用 tmp_path 合成库；不连生产、不调模型。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401 —— 加载全部模型到 Base.metadata
from app.models import Base


@pytest.fixture()
def c7_db(tmp_path, monkeypatch):
    db_path = (tmp_path / "c7_anchor.db").as_posix()
    assert tmp_path.as_posix() in db_path, "C7 helper DB 必须落在 pytest tmp_path 沙箱"
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


async def _seed_base(factory, *, now, user_id=1, character_id=1, session_id=1):
    from app.models import AICharacter, ChatSession, User

    async with factory() as db:
        db.add(User(id=user_id, username=f"u{user_id}", nickname=f"U{user_id}"))
        db.add(AICharacter(id=character_id, user_id=user_id, name=f"C{character_id}"))
        db.add(ChatSession(
            id=session_id, user_id=user_id, character_id=character_id,
            created_at=now, updated_at=now,
        ))
        await db.commit()


async def _add_message(factory, session_id, sender_type, content, created_at):
    from app.models import ChatMessage

    async with factory() as db:
        msg = ChatMessage(
            session_id=session_id, sender_type=sender_type,
            content=content, created_at=created_at,
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return msg.id


def _state(**over):
    state = {"user_id": 1, "character_id": 1, "session_id": 1}
    state.update(over)
    return state


def test_source_anchor_uses_previous_user_not_current(c7_db):
    """当前消息必须排除：5 天 1 小时的回访不能被本轮消息算成“刚刚”。"""
    from app.agent.context.chat_time import (
        STATUS_OK, format_gap_text, resolve_chat_time_anchor,
    )

    now = datetime(2026, 10, 21, 0, 30, 0)
    old = now - timedelta(days=5, hours=1)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=now)
        old_id = await _add_message(factory, 1, "user", "OLD_USER", old)
        await _add_message(factory, 1, "ai", "AI_REPLY", old + timedelta(minutes=1))
        cur_id = await _add_message(factory, 1, "user", "CURRENT_USER", now)
        anchor = await resolve_chat_time_anchor(_state(source_id=cur_id))
        assert anchor.status == STATUS_OK
        assert anchor.previous_message_id == old_id
        assert anchor.previous_at == old
        assert anchor.gap_seconds == int((now - old).total_seconds())
        assert format_gap_text(anchor) == "5 天前"

    asyncio.run(run())


def test_session_updated_at_and_read_updates_do_not_change_anchor(c7_db):
    """会话 updated_at / last_read_at 被 AI 回复或已读刷新，不得冒充用户回访。"""
    from app.agent.context.chat_time import STATUS_OK, resolve_chat_time_anchor
    from app.models import ChatSession
    from sqlalchemy import select

    now = datetime(2026, 10, 21, 0, 30, 0)
    old = now - timedelta(days=3, hours=2)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=now)
        old_id = await _add_message(factory, 1, "user", "OLD_USER", old)
        # AI 主动消息也会刷新会话 updated_at；它不得成为“用户回访”锚点
        await _add_message(factory, 1, "ai", "AI_PROACTIVE", now - timedelta(minutes=1))
        cur_id = await _add_message(factory, 1, "user", "CURRENT_USER", now)
        async with factory() as db:
            sess = (await db.execute(select(ChatSession).where(ChatSession.id == 1))).scalar_one()
            sess.updated_at = now
            sess.last_read_at = now
            await db.commit()
        anchor = await resolve_chat_time_anchor(_state(source_id=cur_id))
        assert anchor.status == STATUS_OK
        assert anchor.previous_message_id == old_id
        assert anchor.gap_seconds == int((now - old).total_seconds())

    asyncio.run(run())


def test_cross_session_and_ai_messages_ignored(c7_db):
    """同角色跨私聊会话查找；AI/主动消息更新会话不算用户发言。"""
    from app.agent.context.chat_time import STATUS_OK, resolve_chat_time_anchor

    now = datetime(2026, 10, 21, 0, 30, 0)
    old = now - timedelta(days=4)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=now)
        from app.models import ChatSession
        async with factory() as db:
            db.add(ChatSession(id=2, user_id=1, character_id=1, created_at=now, updated_at=now))
            await db.commit()
        old_id = await _add_message(factory, 1, "user", "OLD_SESSION1", old)
        await _add_message(factory, 1, "ai", "AI_LATER", now - timedelta(minutes=2))
        cur_id = await _add_message(factory, 2, "user", "CURRENT_SESSION2", now)
        anchor = await resolve_chat_time_anchor(_state(session_id=2, source_id=cur_id))
        assert anchor.status == STATUS_OK
        assert anchor.previous_message_id == old_id

    asyncio.run(run())


def test_only_ai_history_has_no_user_anchor(c7_db):
    """只有 AI 问候/主动消息时无用户锚点，不能把 AI 消息当回访。"""
    from app.agent.context.chat_time import STATUS_NO_HISTORY, resolve_chat_time_anchor

    now = datetime(2026, 10, 21, 0, 30, 0)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=now)
        await _add_message(factory, 1, "ai", "AI_GREETING", now - timedelta(days=1))
        cur_id = await _add_message(factory, 1, "user", "FIRST_USER", now)
        anchor = await resolve_chat_time_anchor(_state(source_id=cur_id))
        assert anchor.status == STATUS_NO_HISTORY
        assert anchor.gap_seconds is None

    asyncio.run(run())


def test_same_second_stable_order_and_later_concurrent_excluded(c7_db):
    """同秒时间戳按 id 稳定排序；A 先到 B 后到、A 后装配时不得把 B 当前一轮。"""
    from app.agent.context.chat_time import STATUS_OK, resolve_chat_time_anchor

    now = datetime(2026, 10, 21, 0, 30, 0)
    old = now - timedelta(days=5)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=now)
        old_id = await _add_message(factory, 1, "user", "OLD", old)
        a_id = await _add_message(factory, 1, "user", "A", now)
        b_id = await _add_message(factory, 1, "user", "B", now)  # 同秒、id 更大
        assert a_id < b_id
        # A 的回合即使 B 已落库后才装配，也只能看到 old
        anchor_a = await resolve_chat_time_anchor(_state(source_id=a_id))
        assert anchor_a.status == STATUS_OK
        assert anchor_a.previous_message_id == old_id
        # B 的回合，A 是严格早于 B 的上一句用户消息
        anchor_b = await resolve_chat_time_anchor(_state(source_id=b_id))
        assert anchor_b.status == STATUS_OK
        assert anchor_b.previous_message_id == a_id

    asyncio.run(run())


def test_concurrent_a_b_with_distinct_second(c7_db):
    """A 先到 B 后到（不同秒），A 后装配仍以 A 为边界。"""
    from app.agent.context.chat_time import STATUS_OK, resolve_chat_time_anchor

    now = datetime(2026, 10, 21, 0, 30, 0)
    old = now - timedelta(days=5, hours=1)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=now)
        old_id = await _add_message(factory, 1, "user", "OLD", old)
        a_id = await _add_message(factory, 1, "user", "A", now - timedelta(seconds=1))
        await _add_message(factory, 1, "user", "B", now)
        anchor = await resolve_chat_time_anchor(_state(source_id=a_id))
        assert anchor.status == STATUS_OK
        assert anchor.previous_message_id == old_id

    asyncio.run(run())


def test_source_invalid_cases_do_not_fallback(c7_db):
    """source 缺失/已删除/AI/跨账号/跨角色/跨会话 → invalid_source，不读他人历史、不降级。"""
    from app.agent.context.chat_time import STATUS_INVALID_SOURCE, resolve_chat_time_anchor
    from app.models import ChatMessage
    from sqlalchemy import delete

    now = datetime(2026, 10, 21, 0, 30, 0)
    old = now - timedelta(days=2)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=now)
        # 额外账号/角色/会话：user2×char2；user1×char3（另一角色）；user1×char1 的第二会话
        from app.models import AICharacter, ChatSession, User
        async with factory() as db:
            db.add(User(id=2, username="u2", nickname="U2"))
            db.add(AICharacter(id=2, user_id=2, name="C2"))
            db.add(AICharacter(id=3, user_id=1, name="C3"))
            db.add(ChatSession(id=2, user_id=2, character_id=2, created_at=now, updated_at=now))
            db.add(ChatSession(id=3, user_id=1, character_id=3, created_at=now, updated_at=now))
            db.add(ChatSession(id=4, user_id=1, character_id=1, created_at=now, updated_at=now))
            await db.commit()
        await _add_message(factory, 1, "user", "OLD_USER1", old)
        ai_id = await _add_message(factory, 1, "ai", "AI_SOURCE", now)
        other_user_msg = await _add_message(factory, 2, "user", "USER2", now)
        other_char_msg = await _add_message(factory, 3, "user", "CHAR3", now)
        other_session_msg = await _add_message(factory, 4, "user", "SAME_CHAR_OTHER_SESSION", now)
        deleted_id = await _add_message(factory, 1, "user", "TO_DELETE", now)
        async with factory() as db:
            await db.execute(delete(ChatMessage).where(ChatMessage.id == deleted_id))
            await db.commit()

        for sid in (999999, ai_id, other_user_msg, other_char_msg, other_session_msg, deleted_id):
            anchor = await resolve_chat_time_anchor(_state(source_id=sid))
            assert anchor.status == STATUS_INVALID_SOURCE, (sid, anchor)
            assert anchor.previous_message_id is None

    asyncio.run(run())


def test_missing_caller_and_character_are_closed(c7_db):
    from app.agent.context.chat_time import (
        STATUS_MISSING_CALLER, STATUS_MISSING_CONTEXT, resolve_chat_time_anchor,
    )

    now = datetime(2026, 10, 21, 0, 30, 0)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=now)
        await _add_message(factory, 1, "user", "OLD", now - timedelta(days=2))
        cur_id = await _add_message(factory, 1, "user", "CURRENT", now)
        a1 = await resolve_chat_time_anchor(_state(user_id=None, source_id=cur_id))
        a2 = await resolve_chat_time_anchor(_state(character_id=None, source_id=cur_id))
        assert a1.status == STATUS_MISSING_CALLER and a1.previous_message_id is None
        assert a2.status == STATUS_MISSING_CONTEXT and a2.previous_message_id is None

    asyncio.run(run())


def test_time_source_id_takes_precedence_without_changing_memory_source(c7_db):
    """chat_time_source_id 只服务时间锚点；source_id 的其它语义不被替换。"""
    from app.agent.context.chat_time import STATUS_OK, resolve_chat_time_anchor

    now = datetime(2026, 10, 21, 0, 30, 0)
    old = now - timedelta(days=5)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=now)
        old_id = await _add_message(factory, 1, "user", "OLD", old)
        cur_id = await _add_message(factory, 1, "user", "CURRENT", now)
        anchor = await resolve_chat_time_anchor(_state(
            source_id=old_id,  # 记忆语义上的 source（这里故意指旧消息）
            chat_time_source_id=cur_id,  # 时间边界必须用它
        ))
        assert anchor.status == STATUS_OK
        assert anchor.previous_message_id == old_id

    asyncio.run(run())


def test_entry_anchor_snapshot_excludes_later_message(c7_db):
    """轻量/继续：入口捕获真实旧锚点后插入的并发消息不得成为锚点。"""
    from app.agent.context.chat_time import (
        STATUS_OK, capture_request_anchor, resolve_chat_time_anchor,
    )

    now = datetime(2026, 10, 21, 0, 30, 0)
    old = now - timedelta(days=3, hours=2)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=now)
        old_id = await _add_message(factory, 1, "user", "OLD", old)
        boundary = await capture_request_anchor(1, 1)
        snapshot = boundary["chat_time_anchor_snapshot"]
        assert snapshot["status"] == STATUS_OK
        assert snapshot["message_id"] == old_id
        assert snapshot["created_at"] == old
        await _add_message(factory, 1, "user", "LATE_CONCURRENT", now)
        anchor = await resolve_chat_time_anchor(_state(**boundary))
        assert anchor.status == STATUS_OK
        assert anchor.previous_message_id == old_id
        assert anchor.previous_at == old

    asyncio.run(run())


def test_snapshot_missing_and_no_history(c7_db):
    from app.agent.context.chat_time import (
        STATUS_MISSING_BOUNDARY, STATUS_NO_HISTORY,
        capture_request_anchor, resolve_chat_time_anchor,
    )

    now = datetime(2026, 10, 21, 0, 30, 0)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=now)
        a1 = await resolve_chat_time_anchor(_state())
        assert a1.status == STATUS_MISSING_BOUNDARY
        boundary = await capture_request_anchor(1, 1)
        assert boundary["chat_time_anchor_snapshot"]["status"] == STATUS_NO_HISTORY
        a2 = await resolve_chat_time_anchor(_state(**boundary))
        assert a2.status == STATUS_NO_HISTORY
        assert a2.gap_seconds is None

    asyncio.run(run())


def test_future_timestamp_is_not_rendered_as_just_now(c7_db):
    """入口快照里的最后用户发言时间在未来（时钟异常）→ future_data，省略子句。"""
    from app.agent.context.chat_time import (
        STATUS_FUTURE_DATA, STATUS_OK, capture_request_anchor,
        format_gap_text, resolve_chat_time_anchor,
    )
    from app.utils.timeutil import now_naive_utc

    reference = now_naive_utc()
    future = reference + timedelta(hours=2)

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=reference)
        await _add_message(factory, 1, "user", "FUTURE_ROW", future)
        boundary = await capture_request_anchor(1, 1)
        assert boundary["chat_time_anchor_snapshot"]["status"] == STATUS_OK
        anchor = await resolve_chat_time_anchor(_state(**boundary))
        assert anchor.status == STATUS_FUTURE_DATA
        assert anchor.gap_seconds is None
        assert format_gap_text(anchor) is None

    asyncio.run(run())


def test_utc_aware_reference_is_normalized(c7_db):
    """参考时刻带 tzinfo（用户时区）按 UTC 比较；展示桶位不受时区影响。"""
    from app.agent.context.chat_time import (
        STATUS_OK, capture_request_anchor, format_gap_text, resolve_chat_time_anchor,
    )

    utc_old = datetime(2026, 10, 16, 0, 0, 0)
    reference_bj = datetime(2026, 10, 21, 8, 0, 0, tzinfo=timezone(timedelta(hours=8)))

    async def run():
        factory = c7_db["factory"]
        await _seed_base(factory, now=utc_old)
        msg_id = await _add_message(factory, 1, "user", "OLD", utc_old)
        boundary = await capture_request_anchor(1, 1)
        snapshot = boundary["chat_time_anchor_snapshot"]
        assert snapshot["status"] == STATUS_OK and snapshot["message_id"] == msg_id
        anchor = await resolve_chat_time_anchor(_state(
            chat_time_reference_at=reference_bj,
            chat_time_anchor_snapshot=snapshot,
        ))
        assert anchor.status == STATUS_OK
        assert anchor.gap_seconds == 5 * 86400
        assert format_gap_text(anchor) == "5 天前"

    asyncio.run(run())


def test_gap_format_buckets_and_no_fake_for_invalid():
    from app.agent.context.chat_time import (
        STATUS_FUTURE_DATA, STATUS_OK, ChatTimeAnchor, format_gap_text,
    )

    def text(secs):
        return format_gap_text(ChatTimeAnchor(STATUS_OK, gap_seconds=secs))

    assert text(0) == "刚刚"
    assert text(59) == "刚刚"
    assert text(60) == "1 分钟前"
    assert text(3599) == "59 分钟前"
    assert text(3600) == "1 小时 0 分钟前"
    assert text(86400) == "昨天"
    assert text(172800) == "2 天前"
    assert text(7 * 86400 + 1) == "1 周前"
    assert text(31 * 86400) == "1 个月前"
    assert text(400 * 86400) == "很久"
    assert format_gap_text(None) is None
    assert format_gap_text(ChatTimeAnchor(STATUS_FUTURE_DATA)) is None
    assert format_gap_text(ChatTimeAnchor("no_history")) is None


def test_time_paths_no_longer_read_session_timestamps():
    """回归守卫：正常/轻量时间行都不再读 ChatSession.updated_at/last_read_at。"""
    import inspect

    from app.agent import runtime
    from app.agent.context import chat_time, section_world

    for fn in (section_world._compute_current_time_str, runtime.build_light_social_context):
        src = inspect.getsource(fn)
        assert "ChatSession" not in src, fn.__qualname__
        assert ".updated_at" not in src, fn.__qualname__
        assert "last_read_at" not in src, fn.__qualname__

    resolver_src = inspect.getsource(chat_time.resolve_chat_time_anchor)
    assert "chat_time_source_id" in resolver_src
    assert "source_id" in resolver_src
