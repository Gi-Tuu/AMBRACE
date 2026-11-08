# -*- coding: utf-8 -*-
"""M2 2b 队列租约恢复切片：受控时钟让 lease 过期，新消费者回收同一载荷。"""
import asyncio

from sqlalchemy import text


async def _seed(u=9681, c=9691, s=9693):
    from test_memory_source_queue_m2b import _seed_and_message
    from app.memory import extractor as ex
    msg_id = await _seed_and_message(u, c, s)
    await ex.add_chat_memory_extraction(s, c, u, "queue source", "synthetic ai", source_id=msg_id)
    return msg_id


def _db():
    import sqlite3
    from app.memory import extract_queue as q
    return sqlite3.connect(str(q.database_path()))


def test_lease_expiry_new_consumer_reclaims_same_payload(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extract_queue as q

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        uid = await _seed(9681, 9691, 9693)
        first = q.take(9693)
        assert len(first) == 1
        task_id = first[0]["_task_id"]
        db = _db()
        db.execute("UPDATE extraction_tasks SET lease_until=0,state='leased' WHERE id=?", (task_id,))
        db.commit(); db.close()
        second = q.take(9693)  # 新消费者：旧 lease 已过期，回收同一任务
        assert len(second) == 1
        assert second[0]["_task_id"] == task_id
        assert second[0]["source_id"] == uid
        assert second[0]["source_event_uid"] == first[0]["source_event_uid"]
        assert q.complete(second[0]) is True
        db = _db()
        row = db.execute("SELECT state,attempts FROM extraction_tasks WHERE id=?", (task_id,)).fetchone()
        n = db.execute("SELECT COUNT(*) FROM extraction_tasks WHERE source_id=?", (uid,)).fetchone()[0]
        db.close()
        return uid, row, n

    uid, row, n = asyncio.run(run())
    assert row[0] == "succeeded"
    assert row[1] >= 2
    assert n == 1


def test_lease_expiry_preserves_uid_and_content(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extract_queue as q
    from app.memory import extraction_worker as worker
    from app.memory import extractor as ex
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        uid = await _seed(9682, 9692, 9694)
        first = q.take(9694)
        task_id = first[0]["_task_id"]
        db = _db(); db.execute("UPDATE extraction_tasks SET lease_until=0 WHERE id=?", (task_id,)); db.commit(); db.close()
        second = q.take(9694)
        assert second[0]["source_event_uid"] == first[0]["source_event_uid"]
        from app.models.chat import ChatSession
        async with async_session_factory() as db:
            msg = (await db.execute(text("SELECT content,source_event_uid FROM chat_messages WHERE id=:m"), {"m": uid})).first()
            session = await db.get(ChatSession, 9694)
        # 新消费者仍能通过同一来源核验（真实 DB 消息/UID/正文）
        ok = await worker._source_belongs_to_session(ex, session, uid, second[0]["source_event_uid"])
        return msg, ok

    msg, ok = asyncio.run(run())
    assert msg is not None and msg[1]
    assert ok is True
