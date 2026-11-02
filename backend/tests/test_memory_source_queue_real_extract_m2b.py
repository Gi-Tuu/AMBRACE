# -*- coding: utf-8 -*-
"""M2 2b 真实提取器链故障：仅替换 llm_call 合成输出，真实 worker/extract_single/save_memory。"""
import asyncio
import sqlite3

from sqlalchemy import text

SYNTH = (
    "USER_INFO: 用户喜欢合成测试主题\n"
    "EVENTS: 今天一起完成合成测试\n"
    "PREFERENCES: 喜欢合成茶\n"
)


async def _setup(u=8901, c=8911, s=8921):
    from test_memory_source_queue_m2b import _seed_and_message
    from app.memory import extractor as ex
    msg_id = await _seed_and_message(u, c, s)
    await ex.add_chat_memory_extraction(s, c, u, "queue source", "synthetic ai", source_id=msg_id)
    return msg_id


def _fake_llm(calls):
    async def fake(*args, **kwargs):
        calls.append(1)
        return SYNTH
    return fake


def _task_row(source_id):
    from app.memory import extract_queue as q
    db = sqlite3.connect(str(q.database_path()))
    row = db.execute("SELECT state,attempts,last_error FROM extraction_tasks WHERE source_id=?",
                     (source_id,)).fetchone()
    db.close()
    return row


def _make_available(source_id):
    from app.memory import extract_queue as q
    db = sqlite3.connect(str(q.database_path()))
    db.execute("UPDATE extraction_tasks SET available_at=0,state='pending' WHERE source_id=?",
               (source_id,))
    db.commit(); db.close()


async def _mem_counts(uid):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        mem = (await db.execute(text("SELECT COUNT(*) FROM memories WHERE source_id=:u"),
                                {"u": uid})).scalar_one()
        rec = (await db.execute(text(
            "SELECT COUNT(*) FROM memory_write_receipts WHERE authoritative=1 "
            "AND source_event_uid=(SELECT source_event_uid FROM chat_messages WHERE id=:u)"),
            {"u": uid})).scalar_one()
        proc = (await db.execute(text(
            "SELECT COUNT(*) FROM processed_extractions WHERE user_message_id=:u"),
            {"u": uid})).scalar_one()
    return mem, rec, proc


def test_real_extract_memory_receipt_failure_rolls_back(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extraction_worker as worker
    from app.memory import extractor as ex
    from app.memory import source_binding

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    calls = []
    monkeypatch.setattr(ex, "llm_call", _fake_llm(calls))

    async def boom(*a, **k):
        raise RuntimeError("real_extract_receipt_failure")

    async def run():
        uid = await _setup(8901, 8911, 8921)
        monkeypatch.setattr(source_binding, "bind_memory", boom)
        await worker.process_session(8921, 8911, 8901)
        return uid, await _mem_counts(uid)

    uid, counts = asyncio.run(run())
    assert counts == (0, 0, 0), counts
    assert _task_row(uid)[0] == "retry"
    assert len(calls) == 2  # tool_runner 内部重试一次


def test_real_extract_processed_write_failure_retries_and_reruns_model(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extraction_worker as worker
    from app.memory import extractor as ex
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    calls = []
    monkeypatch.setattr(ex, "llm_call", _fake_llm(calls))

    async def run():
        uid = await _setup(8902, 8912, 8922)
        async with async_session_factory() as db:
            await db.execute(text(
                "CREATE TRIGGER IF NOT EXISTS fail_processed BEFORE INSERT ON processed_extractions "
                "BEGIN SELECT RAISE(ABORT,'processed_fail'); END"))
            await db.commit()
        await worker.process_session(8922, 8912, 8902)
        after_fail = await _mem_counts(uid)
        task_fail = _task_row(uid)
        async with async_session_factory() as db:
            await db.execute(text("DROP TRIGGER IF EXISTS fail_processed"))
            await db.commit()
        _make_available(uid)
        await worker.process_session(8922, 8912, 8902)
        after_retry = await _mem_counts(uid)
        return after_fail, task_fail, after_retry, len(calls)

    after_fail, task_fail, after_retry, n = asyncio.run(run())
    assert after_fail[0] >= 2 and after_fail[2] == 0, after_fail
    assert task_fail[0] == "retry", task_fail
    assert after_retry[2] == 1 and after_retry[0] == after_fail[0]
    assert n == 2  # 无持久 processed 结果，重试确实再次调用模型


def test_real_extract_processed_then_ack_error_does_not_rerun(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extraction_worker as worker
    from app.memory import extractor as ex
    from app.memory import extract_queue as q

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    calls = []
    monkeypatch.setattr(ex, "llm_call", _fake_llm(calls))
    real_complete = q.complete

    async def run():
        uid = await _setup(8903, 8913, 8923)
        monkeypatch.setattr(q, "complete", lambda pair: (_ for _ in ()).throw(RuntimeError("ack_boom")))
        await worker.process_session(8923, 8913, 8903)
        first = await _mem_counts(uid)
        task_fail = _task_row(uid)
        _make_available(uid)
        monkeypatch.setattr(q, "complete", real_complete)
        await worker.process_session(8923, 8913, 8903)
        return first, task_fail, await _mem_counts(uid), _task_row(uid), len(calls)

    first, task_fail, second, task_ok, n = asyncio.run(run())
    assert first[2] == 1 and task_fail[0] == "retry"
    assert second[2] == 1 and task_ok[0] == "succeeded"
    assert n == 1


def test_real_extract_multi_item_second_failure_replay(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extraction_worker as worker
    from app.memory import extractor as ex
    import app.memory as memory_pkg

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    calls = []
    monkeypatch.setattr(ex, "llm_call", _fake_llm(calls))
    real_save = memory_pkg.save_memory
    state = {"n": 0}

    async def flaky(*a, **k):
        state["n"] += 1
        if state["n"] in (2, 5):
            raise RuntimeError("second_item_failure")
        return await real_save(*a, **k)

    async def run():
        uid = await _setup(8904, 8914, 8924)
        monkeypatch.setattr(memory_pkg, "save_memory", flaky)
        await worker.process_session(8924, 8914, 8904)
        after_fail = await _mem_counts(uid)
        _make_available(uid)
        await worker.process_session(8924, 8914, 8904)
        return after_fail, await _mem_counts(uid), len(calls), _task_row(uid)

    after_fail, after_retry, n, task = asyncio.run(run())
    assert 1 <= after_fail[0] < 3  # 第一项已提交，第二项失败，无跨 item 原子
    assert after_retry[0] == 3 and after_retry[2] == 1
    assert n == 3 and task[0] == "succeeded"
