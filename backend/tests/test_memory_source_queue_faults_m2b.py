# -*- coding: utf-8 -*-
"""M2 2b 队列故障恢复：真实 worker+queue+临时 SQLite，合成模型替身。"""
import asyncio
import sqlite3

from sqlalchemy import text


async def _seed_queue(u=8801, c=8811, s=8821, content="queue source"):
    from test_memory_source_queue_m2b import _seed_and_message
    from app.memory import extractor as ex
    msg_id = await _seed_and_message(u, c, s)
    await ex.add_chat_memory_extraction(s, c, u, content, "synthetic ai", source_id=msg_id)
    return msg_id


def _task_row(source_id):
    from app.memory import extract_queue as q
    db = sqlite3.connect(str(q.database_path()))
    row = db.execute("SELECT state,attempts,last_error FROM extraction_tasks "
                     "WHERE source_id=?", (source_id,)).fetchone()
    db.close()
    return row


async def _set_available(source_id):
    from app.memory import extract_queue as q
    db = sqlite3.connect(str(q.database_path()))
    db.execute("UPDATE extraction_tasks SET available_at=0,state='pending' WHERE source_id=?",
               (source_id,))
    db.commit(); db.close()


def test_memory_receipt_failure_leaves_no_authority_and_retries(monkeypatch):
    import pytest
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extraction_worker as worker
    from app.memory import source_binding
    from app.memory.service import save_memory
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    # 批 1A 后普通回执默认也写（fire-and-forget）→ 本用例只验绑定失败无权威落库，显式关掉免跨用例竞态
    monkeypatch.setitem(AGENT_FLAGS, "memory_write_receipt", False)

    async def fake_model(*args, **kwargs):
        p = args[1]
        return await save_memory(
            user_id=p["user_id"], character_id=p["character_id"],
            memory_type="event", content="synthetic memory detail",
            source="chat", source_id=p["source_id"])

    async def boom(*a, **k):
        raise RuntimeError("receipt_mid_failure")

    async def run():
        uid = await _seed_queue(8801, 8811, 8821)
        monkeypatch.setattr("app.agent.internal_runner.run_internal", fake_model)
        monkeypatch.setattr(source_binding, "bind_memory", boom)
        await worker.process_session(8821, 8811, 8801)
        async with async_session_factory() as db:
            mem = (await db.execute(text(
                "SELECT COUNT(*) FROM memories WHERE source_id=:u"), {"u": uid})).scalar_one()
            rec = (await db.execute(text(
                "SELECT COUNT(*) FROM memory_write_receipts WHERE authoritative=1 "
                "AND source_event_uid=(SELECT source_event_uid FROM chat_messages WHERE id=:u)"),
                {"u": uid})).scalar_one()
        return uid, mem, rec

    uid, mem, rec = asyncio.run(run())
    assert (mem, rec) == (0, 0)
    state, attempts, error = _task_row(uid)
    assert state == "retry" and attempts >= 1 and error == "RuntimeError"


def test_processed_committed_then_ack_error_does_not_rerun_model(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extraction_worker as worker
    from app.memory import extract_queue as q
    from app.memory import extractor as ex
    from app.memory.service import save_memory
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    calls = []

    async def fake_model(*args, **kwargs):
        calls.append(1)
        p = args[1]
        await save_memory(user_id=p["user_id"], character_id=p["character_id"],
                          memory_type="event", content="synthetic ack detail",
                          source="chat", source_id=p["source_id"])
        await ex._mark_processed(p["source_id"])
        return {"status": "ok"}

    real_complete = q.complete

    async def run():
        uid = await _seed_queue(8802, 8812, 8822)
        monkeypatch.setattr("app.agent.internal_runner.run_internal", fake_model)
        monkeypatch.setattr(q, "complete", lambda pair: (_ for _ in ()).throw(RuntimeError("ack_boom")))
        await worker.process_session(8822, 8812, 8802)
        first = (_task_row(uid), len(calls))
        # ack 异常 -> retry；恢复 available 后第二次消费应因 processed 跳过模型
        await _set_available(uid)
        monkeypatch.setattr(q, "complete", real_complete)
        await worker.process_session(8822, 8812, 8802)
        async with async_session_factory() as db:
            mem = (await db.execute(text("SELECT COUNT(*) FROM memories WHERE source_id=:u"), {"u": uid})).scalar_one()
            done = (await db.execute(text("SELECT COUNT(*) FROM processed_extractions WHERE user_message_id=:u"), {"u": uid})).scalar_one()
        return first, len(calls), mem, done, _task_row(uid)

    first, n_calls, mem, done, last = asyncio.run(run())
    assert first[0][0] == "retry" and first[1] == 1
    assert n_calls == 1 and mem == 1 and done == 1
    assert last[0] == "succeeded"


def test_same_source_multi_item_partial_failure_replays_without_loss(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extraction_worker as worker
    from app.memory.service import save_memory
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    calls = []
    fail_once = {"v": True}

    async def fake_model(*args, **kwargs):
        calls.append(1)
        p = args[1]
        await save_memory(user_id=p["user_id"], character_id=p["character_id"],
                          memory_type="event", content="synthetic item A",
                          source="chat", source_id=p["source_id"])
        if fail_once["v"]:
            fail_once["v"] = False
            raise RuntimeError("second_item_failure")
        await save_memory(user_id=p["user_id"], character_id=p["character_id"],
                          memory_type="event", content="synthetic item B",
                          source="chat", source_id=p["source_id"])
        return {"status": "ok"}

    async def run():
        uid = await _seed_queue(8803, 8813, 8823)
        monkeypatch.setattr("app.agent.internal_runner.run_internal", fake_model)
        await worker.process_session(8823, 8813, 8803)
        async with async_session_factory() as db:
            after_fail = (await db.execute(text(
                "SELECT COUNT(*) FROM memories WHERE source_id=:u"), {"u": uid})).scalar_one()
        from app.memory import extract_queue as q
        import sqlite3
        db = sqlite3.connect(str(q.database_path()))
        db.execute("UPDATE extraction_tasks SET available_at=0,state='pending' WHERE source_id=?", (uid,))
        db.commit(); db.close()
        await worker.process_session(8823, 8813, 8803)
        async with async_session_factory() as db:
            after_retry = (await db.execute(text(
                "SELECT COUNT(*) FROM memories WHERE source_id=:u"), {"u": uid})).scalar_one()
        return after_fail, after_retry, len(calls)

    after_fail, after_retry, n = asyncio.run(run())
    assert after_fail == 1  # 第一项已提交，第二项失败；无跨 item 原子
    assert after_retry == 2 and n == 2




def test_business_committed_processed_failure_retries_without_loss(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extraction_worker as worker
    from app.memory import extract_queue as q
    from app.memory import extractor as ex
    from app.memory.service import save_memory
    from app.db.database import async_session_factory
    import sqlite3

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    calls = []
    fail_once = {"v": True}

    async def fake_model(*args, **kwargs):
        calls.append(1)
        p = args[1]
        await save_memory(user_id=p["user_id"], character_id=p["character_id"],
                          memory_type="event", content="synthetic processed failure",
                          source="chat", source_id=p["source_id"])
        if fail_once["v"]:
            fail_once["v"] = False
            raise RuntimeError("processed_marker_failure")
        await ex._mark_processed(p["source_id"])
        return {"status": "ok"}

    async def run():
        uid = await _seed_queue(8804, 8814, 8824)
        monkeypatch.setattr("app.agent.internal_runner.run_internal", fake_model)
        await worker.process_session(8824, 8814, 8804)
        async with async_session_factory() as db:
            mem1 = (await db.execute(text("SELECT COUNT(*) FROM memories WHERE source_id=:u"), {"u": uid})).scalar_one()
            p1 = (await db.execute(text("SELECT COUNT(*) FROM processed_extractions WHERE user_message_id=:u"), {"u": uid})).scalar_one()
        db = sqlite3.connect(str(q.database_path()))
        db.execute("UPDATE extraction_tasks SET available_at=0,state='pending' WHERE source_id=?", (uid,))
        db.commit(); db.close()
        await worker.process_session(8824, 8814, 8804)
        async with async_session_factory() as db:
            mem2 = (await db.execute(text("SELECT COUNT(*) FROM memories WHERE source_id=:u"), {"u": uid})).scalar_one()
            p2 = (await db.execute(text("SELECT COUNT(*) FROM processed_extractions WHERE user_message_id=:u"), {"u": uid})).scalar_one()
        return mem1, p1, mem2, p2, len(calls)

    mem1, p1, mem2, p2, n = asyncio.run(run())
    assert (mem1, p1) == (1, 0)  # 业务已提交、processed 未记
    assert (mem2, p2) == (1, 1)  # 重放幂等，补记 processed
    assert n == 2  # 无持久结果，重试会再次调用模型；如实记录
