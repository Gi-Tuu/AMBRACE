"""Durable extraction tasks with transactional claims and renewable leases.

The old JSON path remains the migration/test seam. Its sibling SQLite file owns
new tasks. Pending work is never discarded because it is old or a queue is full.
Legacy JSON is imported once, without deleting the original recovery evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

_QUEUE_FILE = Path(__file__).resolve().parents[2] / "data" / "extract_queue.json"
LEASE_SECONDS = 900
MAX_ATTEMPTS = 8
BATCH_LIMIT = 10


def database_path() -> Path:
    return _QUEUE_FILE.with_suffix(".sqlite3")


def _insert(db, session_id, pair):
    source = pair.get("source_id")
    key = f"message:{source}" if source is not None else f"anonymous:{uuid.uuid4().hex}"
    db.execute(
        "INSERT OR IGNORE INTO extraction_tasks "
        "(event_key,session_id,source_id,payload,state,created_at,available_at) "
        "VALUES (?,?,?,?, 'pending',?,?)",
        (key, session_id, source, json.dumps(pair, ensure_ascii=False),
         float(pair.get("ts", time.time())), time.time()),
    )


def _import_legacy(db):
    if db.execute("SELECT 1 FROM extraction_queue_meta WHERE key='json_imported'").fetchone():
        return
    if _QUEUE_FILE.exists():
        # Invalid input must remain visible; never replace a damaged file with empty work.
        doc = json.loads(_QUEUE_FILE.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("Invalid legacy extraction queue")
        for sid, pairs in (doc.get("sessions") or {}).items():
            for pair in pairs:
                _insert(db, int(sid), pair)
        for source, stamp in (doc.get("inflight") or {}).items():
            # The old file lost these payloads. Recover the source from SQL at consumption.
            _insert(db, 0, {"source_id": int(source), "ts": float(stamp),
                            "legacy_source_only": True})
    db.execute("INSERT INTO extraction_queue_meta VALUES ('json_imported','1')")


@contextmanager
def _transaction():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS extraction_queue_meta (key TEXT PRIMARY KEY,value TEXT)")
        db.execute("""CREATE TABLE IF NOT EXISTS extraction_tasks (
            id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
            session_id INTEGER NOT NULL, source_id INTEGER, payload TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('pending','leased','retry','succeeded','dead')),
            created_at REAL NOT NULL, available_at REAL NOT NULL,
            lease_until REAL, lease_token TEXT, attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT)""")
        db.execute("CREATE INDEX IF NOT EXISTS ix_extraction_due ON extraction_tasks(state,available_at,session_id)")
        _import_legacy(db)
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _recover(db, now):
    db.execute("""UPDATE extraction_tasks SET
        state=CASE WHEN attempts >= ? THEN 'dead' ELSE 'retry' END,
        available_at=?, lease_token=NULL, lease_until=NULL, last_error='lease_expired'
        WHERE state='leased' AND lease_until <= ?""", (MAX_ATTEMPTS, now, now))


def peek(session_id: int) -> list[dict]:
    with _transaction() as db:
        _recover(db, time.time())
        rows = db.execute("SELECT payload FROM extraction_tasks WHERE session_id=? "
                          "AND state IN ('pending','retry') ORDER BY id", (session_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]


def add(session_id: int, pair: dict) -> list[dict]:
    with _transaction() as db:
        _insert(db, session_id, pair)
    return peek(session_id)


def take(session_id: int) -> list[dict]:
    """Atomically lease eligible work; retain the complete payload until success."""
    with _transaction() as db:
        now = time.time()
        _recover(db, now)
        rows = db.execute("SELECT * FROM extraction_tasks WHERE session_id=? "
                          "AND state IN ('pending','retry') AND available_at<=? "
                          "ORDER BY id LIMIT ?", (session_id, now, BATCH_LIMIT)).fetchall()
        result = []
        for row in rows:
            token = uuid.uuid4().hex
            db.execute("UPDATE extraction_tasks SET state='leased',lease_token=?,lease_until=?,"
                       "attempts=attempts+1 WHERE id=?", (token, now + LEASE_SECONDS, row["id"]))
            pair = json.loads(row["payload"])
            pair.update(_task_id=row["id"], _lease_token=token)
            result.append(pair)
        return result


def renew(pair: dict) -> bool:
    with _transaction() as db:
        return db.execute("UPDATE extraction_tasks SET lease_until=? WHERE id=? AND lease_token=? "
                          "AND state='leased' AND lease_until>?",
                          (time.time() + LEASE_SECONDS, pair["_task_id"], pair["_lease_token"],
                           time.time())).rowcount == 1


def complete(pair: dict) -> bool:
    with _transaction() as db:
        return db.execute("UPDATE extraction_tasks SET state='succeeded',payload='{}',"
                          "lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=? "
                          "AND state='leased' AND lease_until>?",
                          (pair["_task_id"], pair["_lease_token"], time.time())).rowcount == 1


def retry(pair: dict, reason: str = "extraction_failed") -> bool:
    with _transaction() as db:
        row = db.execute("SELECT attempts FROM extraction_tasks WHERE id=? AND lease_token=? "
                         "AND state='leased'", (pair["_task_id"], pair["_lease_token"])).fetchone()
        if row is None:
            return False
        dead = row[0] >= MAX_ATTEMPTS
        db.execute("UPDATE extraction_tasks SET state=?,available_at=?,lease_token=NULL,lease_until=NULL,"
                   "last_error=? WHERE id=?", ("dead" if dead else "retry",
                   time.time() + min(3600, 30 * 2 ** min(row[0], 7)), reason[:80], pair["_task_id"]))
        return True


def pending_sessions(max_age: float) -> list[int]:
    with _transaction() as db:
        now = time.time()
        _recover(db, now)
        return [row[0] for row in db.execute(
            "SELECT session_id FROM extraction_tasks WHERE state IN ('pending','retry') "
            "AND available_at<=? AND (created_at<=? OR state='retry') "
            "GROUP BY session_id ORDER BY MIN(id) LIMIT 50", (now, now - max_age))]


def remove_source(session_id: int, uid) -> None:
    with _transaction() as db:
        db.execute("UPDATE extraction_tasks SET state='succeeded',payload='{}' "
                   "WHERE session_id=? AND source_id=? AND state IN ('pending','retry')", (session_id, uid))


def pending_source_ids() -> set[int]:
    with _transaction() as db:
        _recover(db, time.time())
        return {row[0] for row in db.execute("SELECT source_id FROM extraction_tasks "
                                           "WHERE state!='succeeded' AND source_id IS NOT NULL")}


def abandon(pair: dict, reason: str) -> bool:
    with _transaction() as db:
        return db.execute("UPDATE extraction_tasks SET state='dead',last_error=?,lease_token=NULL,"
                          "lease_until=NULL WHERE id=? AND lease_token=? AND state='leased'",
                          (reason, pair["_task_id"], pair["_lease_token"])).rowcount == 1


def recover_payload(pair: dict, session_id: int, payload: dict) -> bool:
    with _transaction() as db:
        return db.execute("UPDATE extraction_tasks SET session_id=?,payload=?,state='pending',"
                          "available_at=?,lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=? "
                          "AND state='leased'", (session_id, json.dumps(payload, ensure_ascii=False),
                          time.time(), pair["_task_id"], pair["_lease_token"])).rowcount == 1
