# -*- coding: utf-8 -*-
"""M2 2b 欠账 ①：**真实进程死亡**持有的租约恢复（不是把 lease_until 手工置零）。

做法：子进程真实持有租约后被 SIGKILL（父进程 kill），父进程随后：
- 立刻取 ⇒ 必须取不到（旧租约仍在有效期内，不能双重消费）；
- 等租约过期（子进程把 LEASE_SECONDS 设成 2 秒）⇒ 新消费者收回同一任务，attempts+1。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

CHILD = r'''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app.memory import extract_queue as q
q._QUEUE_FILE = Path(sys.argv[2])
q.LEASE_SECONDS = 2
q.add(9201, {"user_message": "real-process", "ai_response": "ai", "ts": time.time(),
             "source_id": 9101, "source_event_uid": "mse-realproc"})
pairs = q.take(9201)
print("LEASED" if pairs else "NONE", flush=True)
time.sleep(300)
'''

SID = 9201


def _db(q):
    return sqlite3.connect(str(q.database_path()))


def test_real_process_death_lease_reclaimed_after_expiry(monkeypatch, tmp_path):
    from app.memory import extract_queue as q

    queue_file = tmp_path / "extract_queue.json"
    monkeypatch.setattr(q, "_QUEUE_FILE", queue_file)

    backend_dir = str(Path(__file__).resolve().parents[1])
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, backend_dir, str(queue_file)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        # 等子进程真正拿到租约（阻塞读一行）
        line = proc.stdout.readline().strip()
        assert line == "LEASED", f"子进程未租到任务：{line!r} / {proc.stderr.read()[-800:]}"
        with _db(q) as db:
            row = db.execute(
                "SELECT id,state,lease_token,lease_until FROM extraction_tasks WHERE session_id=?",
                (SID,)).fetchone()
        assert row is not None and row[1] == "leased" and row[2], row
        task_id, _state, token, lease_until = row[0], row[1], row[2], row[3]
        assert lease_until > time.time(), "租约应在有效期内"
    finally:
        proc.kill()
        proc.wait(timeout=30)

    # 进程已死：租约仍在有效期内 ⇒ 新消费者不得立刻拿走（防双重消费）
    assert q.take(SID) == [], "旧租约未过期就被抢走 ⇒ 会双重消费"

    # 等过期：新消费者应收回同一任务，attempts+1
    deadline = time.time() + 15
    pairs = []
    while time.time() < deadline:
        pairs = q.take(SID)
        if pairs:
            break
        time.sleep(0.5)
    assert len(pairs) == 1, "租约过期后必须能收回任务"
    assert pairs[0]["_task_id"] == task_id, "收回的应是同一任务"
    assert pairs[0]["source_event_uid"] == "mse-realproc"
    with _db(q) as db:
        attempts, state = db.execute(
            "SELECT attempts,state FROM extraction_tasks WHERE id=?", (task_id,)).fetchone()
    assert state == "leased" and attempts >= 2, (attempts, state)
    assert q.complete(pairs[0]) is True
    with _db(q) as db:
        final = db.execute("SELECT state,lease_token FROM extraction_tasks WHERE id=?",
                           (task_id,)).fetchone()
    assert final == ("succeeded", None), final
