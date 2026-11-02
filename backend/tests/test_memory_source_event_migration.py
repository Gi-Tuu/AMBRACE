# -*- coding: utf-8 -*-
"""M2 2a migration: real Alembic history on an isolated temp SQLite."""
import os
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text


def _backend() -> Path:
    return Path(__file__).resolve().parents[1]


def _run(tmp_path, *args):
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'history.db'}"
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_backend(), env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=180,
    )


def test_single_head_and_upgrade_seeds_source_seq(tmp_path):
    cfg = Config(str(_backend() / "alembic.ini"))
    cfg.set_main_option("script_location", str(_backend() / "alembic"))
    assert ScriptDirectory.from_config(cfg).get_heads() == ["c5e7a9b1d3f6"]
    r = _run(tmp_path, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-2000:]
    eng = create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    insp = inspect(eng)
    for table in ("source_seq", "memory_source_events", "memory_tombstones",
                  "memory_reallow_grants", "memory_restore_quarantine"):
        assert insp.has_table(table), table
    cols = {c["name"] for c in insp.get_columns("memory_write_receipts")}
    assert {"source_event_uid", "owner_user_id", "actor_kind", "actor_user_id",
            "envelope_version", "authoritative", "idempotency_key"} <= cols
    with eng.connect() as conn:
        assert conn.execute(text("SELECT id,seq FROM source_seq")).fetchall() == [(1, 0)]
    eng.dispose()


def test_downgrade_and_reupgrade_keep_single_head(tmp_path):
    r = _run(tmp_path, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-2000:]
    r = _run(tmp_path, "downgrade", "d6f8a0b2c4e6")
    assert r.returncode == 0, r.stderr[-2000:]
    eng = create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    assert not inspect(eng).has_table("memory_source_events")
    r = _run(tmp_path, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-2000:]
    assert inspect(eng).has_table("memory_source_events")
    eng.dispose()


def test_nonempty_upgrade_preserves_old_rows_and_fk(tmp_path):
    import sqlite3
    r = _run(tmp_path, "upgrade", "d6f8a0b2c4e6")
    assert r.returncode == 0, r.stderr[-2000:]
    db = tmp_path / "history.db"
    c = sqlite3.connect(db)
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("INSERT INTO users(id,username,nickname,password_hash,created_at) "
              "VALUES (1,'u1','n1','x','2026-01-01 00:00:00')")
    c.execute("INSERT INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
              "is_active,cognitive_loop_enabled,memory_v2_enabled,created_at) "
              "VALUES (11,1,'c1',0,0,1,0,0,'2026-01-01 00:00:00')")
    c.execute("INSERT INTO chat_sessions(id,user_id,character_id,is_active,created_at) "
              "VALUES (1,1,11,1,'2026-01-01 00:00:00')")
    c.execute("INSERT INTO chat_messages(id,session_id,sender_type,content,created_at) "
              "VALUES (1,1,'user','hello','2026-01-01 00:00:00')")
    c.execute("INSERT INTO memories(id,user_id,character_id,memory_type,content,scope,"
              "importance,is_archived,is_pinned,review_count,is_locked,ai_rated,is_core,"
              "confirmation_count,contradiction_count,version,created_at) "
              "VALUES (9001,1,11,'event','old content','private',40,0,0,0,0,0,0,0,0,1,"
              "'2026-01-01 00:00:00')")
    c.execute("INSERT INTO memory_write_receipts(id,character_id,memory_id,action,created_at) "
              "VALUES (1,11,9001,'create','2026-01-01 00:00:00')")
    c.commit(); c.close()
    r = _run(tmp_path, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-2000:]
    c = sqlite3.connect(db); c.execute("PRAGMA foreign_keys=ON")
    assert c.execute("SELECT content FROM memories WHERE id=9001").fetchone()[0] == "old content"
    assert c.execute("SELECT source_event_uid FROM memories WHERE id=9001").fetchone()[0] is None
    assert c.execute("SELECT source_event_uid FROM memory_write_receipts WHERE id=1").fetchone()[0] is None
    assert c.execute("SELECT envelope_version,authoritative FROM memory_write_receipts WHERE id=1").fetchone() == (0, 0)
    assert c.execute("SELECT seq FROM source_seq WHERE id=1").fetchone()[0] == 0
    fks = {tuple(x[2:5]) for x in c.execute("PRAGMA foreign_key_list(memories)")}
    assert ("memory_source_events", "source_event_uid", "event_uid") in fks
    c.close()


def test_corrupt_existing_new_table_is_rejected(tmp_path):
    import sqlite3
    r = _run(tmp_path, "upgrade", "d6f8a0b2c4e6")
    assert r.returncode == 0, r.stderr[-1000:]
    c = sqlite3.connect(tmp_path / "history.db")
    c.execute("CREATE TABLE memory_source_events (event_uid TEXT PRIMARY KEY)")
    c.commit(); c.close()
    r = _run(tmp_path, "upgrade", "head")
    assert r.returncode != 0, "corrupt new table must not stamp head"
    c = sqlite3.connect(tmp_path / "history.db")
    version = c.execute("SELECT version_num FROM alembic_version").fetchone()
    c.close()
    assert version and version[0] == "d6f8a0b2c4e6"


def test_injected_new_table_fault_then_resume(tmp_path):
    r = _run(tmp_path, "upgrade", "d6f8a0b2c4e6")
    assert r.returncode == 0
    env = os.environ.copy(); env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'history.db'}"; env["M2_2A_FAULT"] = "new_tables"
    import subprocess, sys
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
                       cwd=_backend(), env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert r.returncode != 0, "injected fault must fail"
    r = _run(tmp_path, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-1000:]
