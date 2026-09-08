# -*- coding: utf-8 -*-
"""domain_events 事件流水单测（3.10 P0，outbox-lite）。

项目无 pytest-asyncio，异步用例统一 asyncio.run（对齐 tests/test_events.py）。
覆盖：flag 关=noop / 开事件落库 / 幂等（唯一键冲突吞掉）/ 异常不上抛 / 聊天域键与断言。
"""
import asyncio

from app.events import store as st


def _enable_flag(monkeypatch):
    monkeypatch.setattr(st, "domain_events_enabled", lambda: True)


class _FakeCommitSession:
    """记录 add 的行、模拟 commit；可切换为抛 IntegrityError 验证幂等吞掉。"""

    def __init__(self, fail_integrity=False):
        self.rows, self.committed, self.fail = [], 0, fail_integrity

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def add(self, obj):
        self.rows.append(obj)

    async def commit(self):
        from sqlalchemy.exc import IntegrityError
        self.committed += 1
        if self.fail:
            raise IntegrityError("insert", {}, Exception("uq"))

    async def rollback(self):
        pass


def test_flag_off_is_noop(monkeypatch):
    monkeypatch.setattr(st, "domain_events_enabled", lambda: False)
    monkeypatch.setattr(
        st, "async_session_factory",
        lambda: (_ for _ in ()).throw(AssertionError("flag off must not open session")),
    )
    asyncio.run(st.append_domain_event("chat.message_sent", "chat_session", 1, entity_id=1))
    # 没开 session 即通过


def test_append_happy_path(monkeypatch):
    _enable_flag(monkeypatch)
    fake = _FakeCommitSession()
    monkeypatch.setattr(st, "async_session_factory", lambda: fake)
    asyncio.run(st.append_domain_event(
        "chat.message_sent", "chat_session", 7,
        entity_type="chat_message", entity_id=101, actor_type="user", actor_id=3,
        payload={"content": "x" * 300}, idempotency_key="chat.message_sent:chat_message:101"))
    assert fake.committed == 1
    row = fake.rows[0]
    assert row.aggregate_type == "chat_session" and row.aggregate_id == 7
    assert row.entity_type == "chat_message" and row.entity_id == 101
    assert row.actor_type == "user" and row.actor_id == 3
    assert row.idempotency_key == "chat.message_sent:chat_message:101"
    assert row.origin == "system_event"  # 未显式传 origin 时的缺省（对齐 PROVENANCE_META 白名单）
    assert len(row.payload_json) <= 230  # 长文被截到 200 + 省略号/JSON 包裹


def test_integrity_error_is_swallowed(monkeypatch):
    _enable_flag(monkeypatch)
    monkeypatch.setattr(st, "async_session_factory", lambda: _FakeCommitSession(fail_integrity=True))
    # 唯一键冲突不得上抛
    asyncio.run(st.append_domain_event("x.y", "moment", 1, entity_id=2))


def test_store_never_raises(monkeypatch):
    _enable_flag(monkeypatch)

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(st, "async_session_factory", _boom)
    asyncio.run(st.append_domain_event("x.y", "moment", 1, entity_id=2))  # 不抛即通过


def test_default_idempotency_key_deterministic(monkeypatch):
    _enable_flag(monkeypatch)
    seen = []

    class S(_FakeCommitSession):
        def add(self, o):
            seen.append(o.idempotency_key)

    monkeypatch.setattr(st, "async_session_factory", lambda: S())
    for _ in range(2):
        asyncio.run(st.append_domain_event(
            "moment.liked", "moment", 9, entity_type="moment_like", entity_id=55))
    assert seen[0] == seen[1] == "moment.liked:moment_like:55"


def test_no_ids_is_noop(monkeypatch):
    """无 aggregate_id 且无 entity_id → 无法定位，直接返回（不开 session）。"""
    _enable_flag(monkeypatch)
    monkeypatch.setattr(
        st, "async_session_factory",
        lambda: (_ for _ in ()).throw(AssertionError("no locator must not open session")),
    )
    asyncio.run(st.append_domain_event("chat.turn_completed", "chat_session", None))


def test_event_types_complete():
    from app.events.types import EventType as E
    for t in ("chat.session_created", "chat.message_sent", "chat.turn_completed",
              "chat.message_deleted", "chat.session_read",
              "moment.published", "moment.comment_added", "moment.liked", "moment.unliked"):
        assert any(m.value == t for m in E)


def test_flag_default_is_off():
    """灰度默认关：AGENT_FLAGS 硬编码默认值为 False（上线零行为变化）。"""
    from app.agent.loop import AGENT_FLAGS
    assert "domain_event_log_enabled" in AGENT_FLAGS
    assert AGENT_FLAGS["domain_event_log_enabled"] is False
    assert st.domain_events_enabled() is False


def _temp_factory(tmp_path):
    """临时 SQLite 上只建 domain_events 一张表，返回 (session_factory, dispose)。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.models.domain_event import DomainEvent

    db_file = tmp_path / "domain_events_tmp.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: DomainEvent.__table__.create(c, checkfirst=True)
            )

    return engine, factory, _setup


def test_chat_turn_events_persist_and_idempotent(monkeypatch, tmp_path):
    """聊天一轮事件落临时库：user/AI 块各一条、turn_completed 重复补写只落一条；flag 关后零新增。"""
    from sqlalchemy import select
    from app.models.domain_event import DomainEvent

    engine, factory, setup = _temp_factory(tmp_path)
    _enable_flag(monkeypatch)
    monkeypatch.setattr(st, "async_session_factory", factory)

    async def _run():
        await setup()
        # 一轮：1 条用户消息 + 2 个 AI 块（ws_chunk）+ 1 条清算
        await st.append_domain_event(
            "chat.message_sent", "chat_session", 7, entity_type="chat_message", entity_id=1001,
            actor_type="user", actor_id=3, payload={"sender_type": "user", "route": "ws_chunk",
                                                    "content": "你好"},
            idempotency_key="chat.message_sent:chat_message:1001", origin="user_message")
        for mid in (1002, 1003):
            await st.append_domain_event(
                "chat.message_sent", "chat_session", 7, entity_type="chat_message", entity_id=mid,
                actor_type="ai", actor_id=13, payload={"sender_type": "ai", "route": "ws_chunk",
                                                       "content": f"块{mid}"},
                idempotency_key=f"chat.message_sent:chat_message:{mid}", origin="ai_message")
        for _ in range(2):  # 同键重复：SSE 回退 chunked 也会再发一次
            await st.append_domain_event(
                "chat.turn_completed", "chat_session", 7, actor_type="system",
                payload={"user_message_id": 1001, "ai_message_ids": [1002, 1003],
                         "route": "ws_chunk", "block_count": 2},
                idempotency_key="chat.turn_completed:7:1001", origin="ai_message")

        async with factory() as db:
            rows = (await db.execute(
                select(DomainEvent).order_by(DomainEvent.id))).scalars().all()
        assert len(rows) == 4, f"一轮应为 4 条（turn 去重），实际 {len(rows)}"
        kinds = [r.event_type for r in rows]
        assert kinds.count("chat.message_sent") == 3
        assert kinds.count("chat.turn_completed") == 1
        assert all(r.aggregate_type == "chat_session" and r.aggregate_id == 7 for r in rows)
        turn = [r for r in rows if r.event_type == "chat.turn_completed"][0]
        assert turn.entity_id is None and turn.actor_type == "system"
        assert '"block_count": 2' in turn.payload_json
        user_row = [r for r in rows if r.entity_id == 1001][0]
        assert user_row.idempotency_key == "chat.message_sent:chat_message:1001"
        assert user_row.origin == "user_message"

        # flag 关：零新增
        monkeypatch.setattr(st, "domain_events_enabled", lambda: False)
        await st.append_domain_event(
            "chat.message_sent", "chat_session", 7, entity_type="chat_message", entity_id=1004,
            idempotency_key="chat.message_sent:chat_message:1004")
        async with factory() as db:
            rows2 = (await db.execute(select(DomainEvent))).scalars().all()
        assert len(rows2) == 4
        await engine.dispose()

    asyncio.run(_run())
