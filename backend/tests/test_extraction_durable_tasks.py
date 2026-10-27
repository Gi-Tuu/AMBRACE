"""Durable task contracts: synthetic payloads, real SQLite and process death."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from app.memory import extract_queue as q
from app.memory import extractor as ex
from app.memory import extraction_worker as worker


@pytest.fixture(autouse=True)
def isolated_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "_QUEUE_FILE", tmp_path / "extract_queue.json")


def pair(source=11, stamp=0):
    return {"source_id": source, "ts": stamp, "user_message": "合成用户证据", "ai_response": "合成角色回复"}


async def _seed_process_session_db(tmp_path, session_id=1, character_id=2, user_id=3, source_id=11):
    """为 process_session 用例补合法的临时数据库身份关系（session/char/user/消息）。"""
    from _dbclone import clone_engine, make_session_factory
    from app.models.user import User
    from app.models.character import AICharacter
    from app.models.chat import ChatSession, ChatMessage

    engine = clone_engine(tmp_path / "process_session.db")
    factory = make_session_factory(engine)
    async with factory() as db:
        db.add(User(id=user_id, username=f"ps_u{user_id}", nickname=f"合成用户{user_id}"))
        await db.flush()
        db.add(AICharacter(id=character_id, user_id=user_id, name=f"合成角色{character_id}"))
        await db.flush()
        db.add(ChatSession(id=session_id, user_id=user_id, character_id=character_id))
        await db.flush()
        db.add(ChatMessage(id=source_id, session_id=session_id, sender_type="user", content="合成用户证据"))
        await db.commit()
    return engine, factory


def task_rows():
    with sqlite3.connect(q.database_path()) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM extraction_tasks ORDER BY id")]


def test_idle_old_pending_is_never_pruned(monkeypatch):
    q.add(1, pair())
    assert q.pending_sessions(600) == [1]
    assert q.peek(1) == [pair()]
    assert q.pending_source_ids() == {11}
    assert not q._QUEUE_FILE.exists()
    assert q.database_path().exists()


def test_expired_lease_restores_full_payload_and_fences_old_worker(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(q.time, "time", lambda: clock[0])
    q.add(1, pair())
    old = q.take(1)[0]
    assert q.take(1) == []
    clock[0] += q.LEASE_SECONDS + 1
    new = q.take(1)[0]
    assert new["user_message"] == pair()["user_message"]
    assert new["_lease_token"] != old["_lease_token"]
    assert not q.complete(old)
    assert not q.retry(old)
    assert not q.renew(old)
    assert q.complete(new)
    assert task_rows()[0]["payload"] == "{}"


def test_retry_backoff_and_exhaustion_remain_inspectable(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(q.time, "time", lambda: clock[0])
    q.add(1, pair())
    for _ in range(q.MAX_ATTEMPTS):
        claim = q.take(1)[0]
        assert q.retry(claim, "synthetic_failure")
        assert q.take(1) == []
        clock[0] += 3601
    row = task_rows()[0]
    assert row["state"] == "dead"
    assert json.loads(row["payload"]) == pair()
    assert row["last_error"] == "synthetic_failure"
    assert q.pending_source_ids() == {11}


def test_replayed_source_does_not_create_a_second_task():
    q.add(1, pair())
    q.add(1, pair())
    claim = q.take(1)[0]
    assert q.complete(claim)
    q.add(1, pair())
    assert len(task_rows()) == 1
    assert q.peek(1) == []


def test_import_legacy_pending_and_lost_inflight_once():
    doc = {"sessions": {"1": [pair()]}, "inflight": {"12": 0}}
    q._QUEUE_FILE.write_text(json.dumps(doc))
    assert q.peek(1) == [pair()]
    assert q.take(0)[0]["legacy_source_only"]
    assert q.pending_source_ids() == {11, 12}
    assert json.loads(q._QUEUE_FILE.read_text()) == doc
    assert len(task_rows()) == 2


def test_damaged_legacy_file_is_not_silently_replaced():
    q._QUEUE_FILE.write_text("{bad")
    with pytest.raises(json.JSONDecodeError):
        q.peek(1)
    assert q._QUEUE_FILE.read_text() == "{bad"


def test_process_exit_after_claim_retains_payload(monkeypatch):
    q.add(1, pair())
    module_path = Path(q.__file__).resolve()
    code = '''import importlib.util, pathlib, os, sys
spec=importlib.util.spec_from_file_location('queue_under_test',sys.argv[1])
q=importlib.util.module_from_spec(spec); spec.loader.exec_module(q)
q._QUEUE_FILE=pathlib.Path(sys.argv[2])
assert len(q.take(1)) == 1
os._exit(17)
'''
    result = subprocess.run([sys.executable, "-c", code, str(module_path), str(q._QUEUE_FILE)])
    assert result.returncode == 17
    row = task_rows()[0]
    assert row["state"] == "leased"
    assert json.loads(row["payload"]) == pair()
    monkeypatch.setattr(q.time, "time", lambda: row["lease_until"] + 1)
    assert q.take(1)[0]["user_message"] == pair()["user_message"]


def test_two_processes_cannot_claim_same_task():
    q.add(1, pair())
    code = '''import importlib.util,pathlib,sys,json
s=importlib.util.spec_from_file_location('queue_under_test',sys.argv[1])
q=importlib.util.module_from_spec(s);s.loader.exec_module(q)
q._QUEUE_FILE=pathlib.Path(sys.argv[2]);print(len(q.take(1)))
'''
    args = [sys.executable, "-c", code, str(Path(q.__file__).resolve()), str(q._QUEUE_FILE)]
    # C1：子进程必须显式钉死 UTF-8——只写 text=True 时父进程按本机 ANSI 码页解码（10-04 CI 就这样红过）
    processes = [
        subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        for _ in range(2)
    ]
    counts = []
    for process in processes:
        output, _ = process.communicate(timeout=15)
        assert process.returncode == 0
        counts.append(int(output))
    assert sorted(counts) == [0, 1]


@pytest.mark.parametrize("success", [True, False])
def test_worker_requires_success_and_processed_marker(monkeypatch, tmp_path, success):
    q.add(1, pair())
    engine, factory = asyncio.run(_seed_process_session_db(tmp_path))
    monkeypatch.setattr(ex, "async_session_factory", factory)
    completed = []

    async def processed(source):
        return bool(completed)

    async def run(*args, **kwargs):
        if success:
            completed.append(11)
        return {"status": "ok" if success else "error"}

    monkeypatch.setattr(worker, "_processed", processed)
    monkeypatch.setattr("app.agent.internal_runner.run_internal", run)
    monkeypatch.setattr(ex, "_mark_batch_taken", lambda *args: None)
    try:
        asyncio.run(worker.process_session(1, 2, 3))
    finally:
        asyncio.run(engine.dispose())
    assert task_rows()[0]["state"] == ("succeeded" if success else "retry")


def test_tool_ok_without_processed_marker_is_retry(monkeypatch, tmp_path):
    q.add(1, pair())
    engine, factory = asyncio.run(_seed_process_session_db(tmp_path))
    monkeypatch.setattr(ex, "async_session_factory", factory)

    async def processed(source):
        return False

    async def run(*args, **kwargs):
        return {"status": "ok", "result": 0}

    monkeypatch.setattr(worker, "_processed", processed)
    monkeypatch.setattr("app.agent.internal_runner.run_internal", run)
    monkeypatch.setattr(ex, "_mark_batch_taken", lambda *args: None)
    try:
        asyncio.run(worker.process_session(1, 2, 3))
    finally:
        asyncio.run(engine.dispose())
    assert task_rows()[0]["state"] == "retry"


def test_catchup_drains_due_work_without_any_new_chat(monkeypatch):
    called = []

    async def drain():
        called.append(True)

    async def processed():
        return set()

    class EmptyDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, stmt):
            from types import SimpleNamespace
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    monkeypatch.setattr(worker, "drain_due", drain)
    monkeypatch.setattr(ex, "_load_processed_ids", processed)
    monkeypatch.setattr(ex, "async_session_factory", EmptyDB)
    asyncio.run(ex.catchup_extract_all())
    assert called == [True]


@pytest.mark.parametrize("legacy_inflight", [False, True])
def test_idle_consumer_processes_real_session_after_user_leaves(monkeypatch, tmp_path, legacy_inflight):
    from _dbclone import clone_engine, make_session_factory
    from app.models.user import User
    from app.models.character import AICharacter
    from app.models.chat import ChatSession, ChatMessage

    engine = clone_engine(tmp_path / "idle.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(ex, "async_session_factory", factory)
    monkeypatch.setattr(ex, "_mark_batch_taken", lambda *args: None)
    calls = []

    async def run(tool, payload, **kwargs):
        calls.append(payload)
        await ex._mark_processed(payload["source_id"])
        return {"status": "ok", "result": 0}

    monkeypatch.setattr("app.agent.internal_runner.run_internal", run)

    async def scenario():
        async with factory() as db:
            db.add(User(id=907, username="durable_idle_synthetic", nickname="合成用户"))
            await db.flush()
            db.add(AICharacter(id=908, user_id=907, name="合成角色"))
            await db.flush()
            db.add(ChatSession(id=909, user_id=907, character_id=908))
            await db.flush()
            db.add_all([
                ChatMessage(id=910, session_id=909, sender_type="user", content="合成用户证据"),
                ChatMessage(id=911, session_id=909, sender_type="ai", content="合成角色回复"),
            ])
            await db.commit()
        if legacy_inflight:
            q._QUEUE_FILE.write_text(json.dumps({"sessions": {}, "inflight": {"910": 0}}))
            await worker.drain_due()  # Recover old lost payload without a catchup time window.
        else:
            q.add(909, pair(source=910))
        await worker.drain_due()
        assert len(calls) == 1, json.dumps(task_rows())
        assert calls[0]["user_msg"] == "合成用户证据"
        assert calls[0]["character_id"] == 908
        assert calls[0]["user_id"] == 907
        assert task_rows()[0]["state"] == "succeeded"

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(engine.dispose())


def test_lost_heartbeat_cancels_inflight_operation(monkeypatch):
    monkeypatch.setattr(q, "LEASE_SECONDS", 0.03)
    monkeypatch.setattr(q, "renew", lambda pair: False)
    cancelled = []

    async def operation():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    with pytest.raises(RuntimeError, match="lease_lost"):
        asyncio.run(worker._with_heartbeat({}, operation()))
    assert cancelled == [True]


def test_worker_cancellation_retains_recoverable_payload(monkeypatch, tmp_path):
    q.add(1, pair())
    engine, factory = asyncio.run(_seed_process_session_db(tmp_path))
    monkeypatch.setattr(ex, "async_session_factory", factory)

    async def processed(source):
        return False

    async def run(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(worker, "_processed", processed)
    monkeypatch.setattr("app.agent.internal_runner.run_internal", run)
    monkeypatch.setattr(ex, "_mark_batch_taken", lambda *args: None)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(worker.process_session(1, 2, 3))
    finally:
        asyncio.run(engine.dispose())
    row = task_rows()[0]
    assert row["state"] == "leased"
    assert json.loads(row["payload"]) == pair()
