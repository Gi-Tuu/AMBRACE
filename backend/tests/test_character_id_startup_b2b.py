# -*- coding: utf-8 -*-
"""B2b：真实 init_db -> ensure_alembic_revision 启动顺序（子进程隔离）。"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
OLD_HEAD = "e2f4a6b8c0d2"
CANDIDATE = "b2a0c1d2e3f4"


def _build_legacy(tmp_path: Path) -> Path:
    tree = tmp_path / "old_tree"
    shutil.copytree(BACKEND / "alembic", tree, ignore=shutil.ignore_patterns("__pycache__"))
    cand = tree / "versions" / "b2a0c1d2e3f4_ai_characters_autoincrement.py"
    if cand.exists():
        cand.unlink()
    db = tmp_path / "old.db"
    env = _env(db, tmp_path)
    code = (
        "import sys\n"
        "from alembic import command\n"
        "from alembic.config import Config\n"
        "cfg=Config()\n"
        "cfg.set_main_option('script_location', sys.argv[1])\n"
        "cfg.set_main_option('prepend_sys_path', sys.argv[2])\n"
        "cfg.set_main_option('sqlalchemy.url','')\n"
        "command.upgrade(cfg,'e2f4a6b8c0d2')\n"
    )
    subprocess.run(
        [sys.executable, "-c", code, str(tree), str(BACKEND)],
        cwd=str(BACKEND), env=env, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    _make_legacy_ai(db)
    return db


def _make_legacy_ai(db: Path) -> None:
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA foreign_keys=OFF")
    ddl = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
    ).fetchone()[0]
    legacy = re.sub(
        r',\s*CONSTRAINT\s+"?ck_ai_characters_client_safe_id"?\s+CHECK\s*\([^)]*\)',
        "", ddl, count=1, flags=re.I,
    )
    legacy = re.sub(
        r"id\s+INTEGER\s+NOT\s+NULL\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
        "id INTEGER NOT NULL", ddl, count=1, flags=re.I,
    ).rstrip()
    assert legacy.endswith(")")
    legacy = legacy[:-1].rstrip().rstrip(",") + ",\n\tPRIMARY KEY (id)\n)"
    cols = [r[1] for r in con.execute("PRAGMA table_info(ai_characters)").fetchall()]
    col_sql = ", ".join('"' + c.replace('"', '""') + '"' for c in cols)
    legacy = legacy.replace(
        'CREATE TABLE "ai_characters"', 'CREATE TABLE "ai_characters_legacy"', 1
    )
    con.execute(legacy)
    con.execute(f"INSERT INTO ai_characters_legacy ({col_sql}) SELECT {col_sql} FROM ai_characters")
    con.execute("DROP TABLE ai_characters")
    con.execute("ALTER TABLE ai_characters_legacy RENAME TO ai_characters")
    con.commit()
    con.close()


def _env(db: Path, tmp_path: Path) -> dict:
    env = os.environ.copy()
    env["DATABASE_URL"] = "sqlite+aiosqlite:///" + Path(db).resolve().as_posix()
    env["CHROMA_PERSIST_DIR"] = str((Path(tmp_path) / "vector").resolve())
    env["PYTHONIOENCODING"] = "utf-8"
    env["AUTH_SECRET_KEY"] = "b2b-startup-only-0123456789abcdef"
    for key in ("AMBRACE_TEST_DB_URL", "AMBRACE_TEST_KEEP_PROD", "AMBRACE_CREDENTIAL_KEY_FILE"):
        env.pop(key, None)
    return env


def _run_startup(db: Path, tmp_path: Path) -> dict:
    out = Path(tmp_path) / "startup.json"
    code = (
        "import asyncio, json, sqlite3, sys\n"
        "from pathlib import Path\n"
        "from app.db.init_db import init_db\n"
        "from app.db.migrate import ensure_alembic_revision\n"
        "asyncio.run(init_db())\n"
        "action=asyncio.run(ensure_alembic_revision())\n"
        "db=sys.argv[1]; out=Path(sys.argv[2])\n"
        "con=sqlite3.connect(db)\n"
        "ddl=con.execute(\"SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'\").fetchone()\n"
        "ver=con.execute('SELECT version_num FROM alembic_version').fetchone()\n"
        "seq=con.execute(\"SELECT seq FROM sqlite_sequence WHERE name='ai_characters'\").fetchone()\n"
        "chars=con.execute('SELECT id,user_id,name FROM ai_characters ORDER BY id').fetchall()\n"
        "objs=con.execute(\"SELECT type,name,sql FROM sqlite_master WHERE tbl_name='ai_characters' AND type IN ('index','trigger') ORDER BY type,name\").fetchall()\n"
        "con.close()\n"
        "out.write_text(json.dumps({'action':action,'version':ver[0] if ver else None,"
        "'autoincrement':bool(ddl and 'AUTOINCREMENT' in (ddl[0] or '').upper()),"
        "'seq':seq[0] if seq else None,'chars':chars,'objects':objs},ensure_ascii=False),encoding='utf-8')\n"
    )
    subprocess.run(
        [sys.executable, "-c", code, str(db), str(out)],
        cwd=str(BACKEND), env=_env(db, tmp_path), check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
    )
    return json.loads(out.read_text(encoding="utf-8"))


def _seed(db: Path) -> None:
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("INSERT INTO users(id,username,nickname) VALUES(1,'b2b','B')")
    con.execute(
        "INSERT INTO ai_characters(id,user_id,name,is_partner,is_active,"
        "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) VALUES(101,1,'keep',0,1,1,1,0)"
    )
    con.execute(
        "INSERT INTO lorebook_entries(user_id,character_id,title,content,keywords,"
        "exclude_keywords,active,is_regex,probability,inclusion_group,sticky_rounds,"
        "cooldown_rounds) VALUES(1,999,'orphan','x','[]','[]',1,0,100,'',0,0)"
    )
    con.commit()
    con.close()


def test_fresh_startup_creates_autoincrement_and_stamps(tmp_path):
    db = tmp_path / "fresh.db"
    result = _run_startup(db, tmp_path)
    assert result["action"].startswith("stamped:") or result["action"].startswith("upgraded:")
    assert result["version"] == CANDIDATE
    assert result["autoincrement"] is True


def test_old_head_startup_preserves_rows_and_upgrades(tmp_path):
    db = _build_legacy(tmp_path)
    _seed(db)
    result = _run_startup(db, tmp_path)
    assert result["version"] == CANDIDATE
    assert result["autoincrement"] is True
    assert result["chars"] == [[101, 1, "keep"]]
    assert result["seq"] >= 999


def test_no_version_legacy_startup_stamps_parent(tmp_path):
    db = _build_legacy(tmp_path)
    _seed(db)
    con = sqlite3.connect(str(db)); con.execute("DROP TABLE alembic_version"); con.commit(); con.close()
    result = _run_startup(db, tmp_path)
    assert result["version"] == CANDIDATE
    assert result["autoincrement"] is True
    assert result["seq"] >= 999


def test_orphan_revision_startup_restamps_parent(tmp_path):
    db = _build_legacy(tmp_path)
    con = sqlite3.connect(str(db)); con.execute("UPDATE alembic_version SET version_num='deadbeefdead'"); con.commit(); con.close()
    result = _run_startup(db, tmp_path)
    assert result["version"] == CANDIDATE
    assert result["autoincrement"] is True


def test_repeat_startup_does_not_rebuild_or_lower_sequence(tmp_path):
    db = _build_legacy(tmp_path)
    _seed(db)
    first = _run_startup(db, tmp_path)
    second = _run_startup(db, tmp_path)
    assert first["version"] == second["version"] == CANDIDATE
    assert second["action"].startswith("already_at_head")
    assert second["seq"] >= first["seq"] >= 999
    assert second["objects"] == first["objects"]
    assert second["chars"] == first["chars"]
