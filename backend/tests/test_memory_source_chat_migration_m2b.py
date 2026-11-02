# -*- coding: utf-8 -*-
"""M2 2b migration：d6→b3→c5，旧消息 UID 保持 NULL，单头/降级/重升。"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text


def _backend():
    return Path(__file__).resolve().parents[1]


def _run(tmp_path, *args):
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               DATABASE_URL=f"sqlite:///{tmp_path / 'history.db'}")
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_backend(), env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=180)


def test_single_head_and_old_message_uid_null(tmp_path):
    cfg = Config(str(_backend() / "alembic.ini"))
    cfg.set_main_option("script_location", str(_backend() / "alembic"))
    assert ScriptDirectory.from_config(cfg).get_heads() == ["c5e7a9b1d3f6"]
    r = _run(tmp_path, "upgrade", "d6f8a0b2c4e6")
    assert r.returncode == 0, r.stderr[-2000:]
    db = tmp_path / "history.db"
    con = sqlite3.connect(db)
    con.execute("INSERT INTO users(id,username,nickname,password_hash,created_at) "
                "VALUES (1,'u','n','x','2026-01-01 00:00:00')")
    con.execute("INSERT INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
                "is_active,cognitive_loop_enabled,memory_v2_enabled,created_at) "
                "VALUES (11,1,'c',0,0,1,0,0,'2026-01-01 00:00:00')")
    con.execute("INSERT INTO chat_sessions(id,user_id,character_id,is_active,created_at) "
                "VALUES (1,1,11,1,'2026-01-01 00:00:00')")
    con.execute("INSERT INTO chat_messages(id,session_id,sender_type,content,created_at) "
                "VALUES (1,1,'user','old message','2026-01-01 00:00:00')")
    con.commit(); con.close()
    r = _run(tmp_path, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-2000:]
    eng = create_engine(f"sqlite:///{db}")
    insp = inspect(eng)
    assert "source_event_uid" in {c["name"] for c in insp.get_columns("chat_messages")}
    with eng.connect() as conn:
        assert conn.execute(text(
            "SELECT content, source_event_uid FROM chat_messages WHERE id=1"
        )).fetchone() == ("old message", None)
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "c5e7a9b1d3f6"
    eng.dispose()
    r = _run(tmp_path, "downgrade", "b3d9e1f5a7c2")
    assert r.returncode == 0, r.stderr[-2000:]
    r = _run(tmp_path, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-2000:]




def test_corrupt_existing_chat_uid_column_is_rejected(tmp_path):
    import sqlite3
    r = _run(tmp_path, "upgrade", "d6f8a0b2c4e6")
    assert r.returncode == 0
    c = sqlite3.connect(tmp_path / "history.db")
    c.execute("ALTER TABLE chat_messages ADD COLUMN source_event_uid INTEGER")
    c.commit(); c.close()
    r = _run(tmp_path, "upgrade", "head")
    assert r.returncode != 0, "corrupt existing column must not stamp"
    c = sqlite3.connect(tmp_path / "history.db")
    assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "b3d9e1f5a7c2"
    c.close()

