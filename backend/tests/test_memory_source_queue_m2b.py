# -*- coding: utf-8 -*-
"""M2 2b 队列：延迟任务绑定来源 UID，重复入队/重放不重复。"""
import asyncio

from sqlalchemy import text


async def _seed_and_message(u=7401, c=7411, s=7421):
    from app.db.database import async_session_factory
    from app.application import chat_store
    async with async_session_factory() as db:
        await db.execute(text(
            "INSERT OR IGNORE INTO users(id,username,nickname,password_hash) "
            "VALUES (:u,:n,'n','x')"), {"u": u, "n": f"m2b_{u}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,"
            "talkativeness_locked,is_active,cognitive_loop_enabled,memory_v2_enabled) "
            "VALUES (:c,:u,'c',0,0,1,0,0)"), {"c": c, "u": u})
        await db.execute(text(
            "INSERT OR IGNORE INTO chat_sessions(id,user_id,character_id,is_active) "
            "VALUES (:s,:u,:c,1)"), {"s": s, "u": u, "c": c})
        await db.commit()
    msg_id, _ = await chat_store._persist_user_message(s, u, c, "queue source", True)
    return msg_id


def test_queue_payload_binds_source_uid(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extract_queue as q
    from app.memory import extractor as ex

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        msg_id = await _seed_and_message()
        await ex.add_chat_memory_extraction(7421, 7411, 7401, "queue source", "AI reply", source_id=msg_id)
        pairs = q.peek(7421)
        return msg_id, pairs

    msg_id, pairs = asyncio.run(run())
    assert len(pairs) == 1
    assert pairs[0]["source_id"] == msg_id
    assert pairs[0]["source_event_uid"]


def test_queue_duplicate_source_has_one_task(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extract_queue as q
    from app.memory import extractor as ex

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        msg_id = await _seed_and_message(7402, 7412, 7422)
        await ex.add_chat_memory_extraction(7422, 7412, 7402, "same", "AI", source_id=msg_id)
        await ex.add_chat_memory_extraction(7422, 7412, 7402, "same", "AI", source_id=msg_id)
        return len(q.peek(7422))

    assert asyncio.run(run()) == 1


def test_take_complete_is_single_ack(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extract_queue as q
    from app.memory import extractor as ex

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        msg_id = await _seed_and_message(7403, 7413, 7423)
        await ex.add_chat_memory_extraction(7423, 7413, 7403, "same", "AI", source_id=msg_id)
        pair = q.take(7423)[0]
        first = q.complete(pair)
        second = q.complete(pair)
        return first, second

    assert asyncio.run(run()) == (True, False)


def test_worker_rejects_uid_drift(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extraction_worker as worker
    from app.db.database import async_session_factory
    from app.models.chat import ChatMessage, ChatSession
    from sqlalchemy import text

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        msg_id = await _seed_and_message(7404, 7414, 7424)
        async with async_session_factory() as db:
            sess = await db.get(ChatSession, 7424)
            msg = await db.get(ChatMessage, msg_id)
            old = msg.source_event_uid
            await db.execute(text("UPDATE chat_messages SET source_event_uid='wrong' WHERE id=:m"),
                             {"m": msg_id})
            await db.commit()
            ok = await worker._source_belongs_to_session(
                __import__("app.memory.extractor", fromlist=["x"]), sess, msg_id, old)
            return ok

    assert asyncio.run(run()) is False


def test_worker_business_failure_retries_without_processed(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extract_queue as q
    from app.memory import extractor as ex
    from app.memory import extraction_worker as worker
    from app.agent import internal_runner
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def fail(*a, **k):
        raise RuntimeError("business_before_commit")

    async def run():
        msg_id = await _seed_and_message(7405, 7415, 7425)
        await ex.add_chat_memory_extraction(7425, 7415, 7405, "queue source", "AI", source_id=msg_id)
        monkeypatch.setattr(internal_runner, "run_internal", fail)
        await worker.process_session(7425, 7415, 7405)
        pairs = q.peek(7425)
        async with async_session_factory() as db:
            n = (await db.execute(text("SELECT COUNT(*) FROM processed_extractions WHERE user_message_id=:m"), {"m": msg_id})).scalar_one()
        return pairs, n

    pairs, n = asyncio.run(run())
    assert n == 0
    assert q.take(7425) == []



def test_ack_false_is_retried_not_ignored(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extract_queue as q
    from app.memory import extractor as ex
    from app.memory import extraction_worker as worker
    from app.db.database import async_session_factory
    from sqlalchemy import text
    import sqlite3

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        msg_id = await _seed_and_message(7406, 7416, 7426)
        async with async_session_factory() as db:
            await db.execute(text("INSERT OR IGNORE INTO processed_extractions(user_message_id) VALUES (:m)"), {"m": msg_id})
            await db.commit()
        await ex.add_chat_memory_extraction(7426, 7416, 7406, "queue source", "AI", source_id=msg_id)
        monkeypatch.setattr(q, "complete", lambda pair: False)
        await worker.process_session(7426, 7416, 7406)
        db = sqlite3.connect(str(q.database_path()))
        row = db.execute("SELECT state,last_error FROM extraction_tasks WHERE source_id=?", (msg_id,)).fetchone()
        db.close()
        return row

    state, error = asyncio.run(run())
    assert state == "retry" and error == "ack_failed"
