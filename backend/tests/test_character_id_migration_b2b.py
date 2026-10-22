# -*- coding: utf-8 -*-
"""B2b：正式链接入后的旧库升级、启动分支、数据保持与失败恢复。"""
import gc
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from app.db import migrate

BACKEND = Path(__file__).resolve().parents[1]
OLD_HEAD = "e2f4a6b8c0d2"
CANDIDATE = "b2a0c1d2e3f4"
CANDIDATE_FILE = BACKEND / "alembic" / "versions" / "b2a0c1d2e3f4_ai_characters_autoincrement.py"
STAGES = [
    "AFTER_CREATE", "AFTER_COPY", "AFTER_DROP",
    "AFTER_RENAME", "AFTER_SEQUENCE", "AFTER_CHECK",
]


@pytest.fixture
def use_db(tmp_path):
    def _use(db: Path):
        db = Path(db).resolve()
        settings.database_url = "sqlite+aiosqlite:///" + db.as_posix()
        settings.chroma_persist_dir = str((tmp_path / "vector").resolve())
        return db
    return _use


def _tree(tmp_path: Path, name: str, *, with_candidate: bool = True,
          stage: str | None = None) -> Path:
    tree = tmp_path / name
    shutil.copytree(BACKEND / "alembic", tree, ignore=shutil.ignore_patterns("__pycache__"))
    if not with_candidate:
        cand = tree / "versions" / CANDIDATE_FILE.name
        if cand.exists():
            cand.unlink()
    elif stage is not None:
        dst = tree / "versions" / CANDIDATE_FILE.name
        lines = dst.read_text(encoding="utf-8").splitlines()
        marker = f"# B2A_FAIL_{stage}"
        for i, line in enumerate(lines):
            if marker in line:
                indent = line[: len(line) - len(line.lstrip())]
                lines[i] = indent + f'raise RuntimeError("injected failure at {stage}")'
                break
        else:
            raise AssertionError(f"marker missing: {marker}")
        dst.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return tree


def _cfg(tree: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(tree.resolve()))
    cfg.set_main_option("prepend_sys_path", str(BACKEND.resolve()))
    cfg.set_main_option("sqlalchemy.url", "")
    return cfg


def _build_old(tmp_path: Path, use_db, *, legacy: bool = True) -> Path:
    db = use_db(tmp_path / "old.db")
    command.upgrade(_cfg(_tree(tmp_path, "old_tree", with_candidate=False)), OLD_HEAD)
    if legacy:
        _make_legacy_ai(db)
    return db


def _make_legacy_ai(db: Path) -> None:
    """把新链按新模型顺带生成的 AUTOINCREMENT 还原为旧 DDL 形态（仅测试 setup）。"""
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
    new_ddl = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
    ).fetchone()[0]
    con.close()
    assert "AUTOINCREMENT" not in new_ddl.upper()


def _seed(db: Path) -> None:
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.executemany(
            "INSERT INTO users(id,username,nickname) VALUES(?,?,?)",
            [(1, "b2b_a", "A"), (2, "b2b_b", "B")],
        )
        con.executemany(
            "INSERT INTO ai_characters(id,user_id,name,is_partner,is_active,"
            "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) "
            "VALUES(?,?,?,0,1,1,1,0)",
            [(101, 1, "A保留"), (102, 2, "B保留"), (103, 1, "A目标")],
        )
        con.execute(
            "INSERT INTO character_states(character_id,mood,body_temp,desire,"
            "possessiveness,fatigue,sensitivity,comfort,anger,trust,attachment,curiosity) "
            "VALUES(103,50,50,50,50,50,50,50,50,50,50,50)"
        )
        con.execute("INSERT INTO chat_sessions(user_id,character_id,is_active) VALUES(1,103,1)")
        con.executemany(
            "INSERT INTO character_creation_requests(user_id,idempotency_key,payload_digest,"
            "character_id,state) VALUES(1,?,?,?,'active')",
            [("keep", "d" * 64, 101), ("target", "d" * 64, 103), ("null-target", "d" * 64, None)],
        )
        con.executemany(
            "INSERT INTO lorebook_entries(user_id,character_id,title,content,keywords,"
            "exclude_keywords,active,is_regex,probability,inclusion_group,sticky_rounds,"
            "cooldown_rounds) VALUES(1,?,?,?,'[]','[]',1,0,100,'',0,0)",
            [(103, "target", "target"), (101, "keep", "keep"), (999, "orphan", "orphan")],
        )
        con.execute(
            "INSERT INTO world_facts(user_id,character_id,subject_type,subject_id,predicate,"
            "object_value,status,confidence,epistemic_status,audience,author,is_authoritative,kind) "
            "VALUES(1,103,'user',777,'status','u','active',1.0,'FACT',"
            "'[\"public\"]','system',0,'status')"
        )
        con.execute(
            "INSERT INTO world_facts(user_id,character_id,subject_type,subject_id,predicate,"
            "object_value,status,confidence,epistemic_status,audience,author,is_authoritative,kind) "
            "VALUES(1,103,'character',103,'status','target','active',1.0,'FACT',"
            "'[\"public\"]','system',0,'status')"
        )
        con.execute(
            "CREATE TABLE b2a_restrict_child ("
            "id INTEGER PRIMARY KEY, character_id INTEGER NOT NULL, note TEXT, "
            "FOREIGN KEY(character_id) REFERENCES ai_characters(id) ON DELETE RESTRICT)"
        )
        con.execute(
            "INSERT INTO b2a_restrict_child(id, character_id, note) VALUES(1,103,'restrict')"
        )
        con.execute("CREATE TABLE b2a_audit(id INTEGER PRIMARY KEY, note TEXT)")
        con.execute("CREATE INDEX b2a_idx_ai_char_user ON ai_characters(user_id)")
        con.execute(
            "CREATE TRIGGER b2a_trg_ai_char_touch AFTER UPDATE ON ai_characters "
            "BEGIN INSERT INTO b2a_audit(note) VALUES('touched:' || NEW.id); END"
        )
        con.commit()
    finally:
        con.close()


def _snapshot(db: Path) -> dict:
    con = sqlite3.connect(str(db))
    try:
        def rows(table):
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                return None
            return [tuple(r) for r in con.execute(f'SELECT * FROM "{table}" ORDER BY id')]

        has_seq = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone() is not None
        sequences = None
        if has_seq:
            sequences = [tuple(r) for r in con.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name")]
        return {
            "chars": rows("ai_characters"),
            "states": rows("character_states"),
            "sessions": rows("chat_sessions"),
            "receipts": rows("character_creation_requests"),
            "lorebook": rows("lorebook_entries"),
            "facts": rows("world_facts"),
            "restrict": rows("b2a_restrict_child"),
            "audit": rows("b2a_audit"),
            "fks": [tuple(r) for r in con.execute("PRAGMA foreign_key_list(ai_characters)")],
            "objects": [tuple(r) for r in con.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE tbl_name='ai_characters' "
                "AND type IN ('index','trigger') ORDER BY type,name")],
            "schema_objects": [tuple(r) for r in con.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")],
            "table_count": con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0],
            "ddl": (
                con.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
                ).fetchone() or [None]
            )[0],
            "sequences": sequences,
        }
    finally:
        con.close()


def _version(db: Path) -> str:
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()


def _fk_check(db: Path) -> list:
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA foreign_keys=ON")
    try:
        return [tuple(r) for r in con.execute("PRAGMA foreign_key_check")]
    finally:
        con.close()


def _new_char(con, user_id: int, name: str) -> int:
    return int(con.execute(
        "INSERT INTO ai_characters(user_id,name,is_partner,is_active,"
        "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) "
        "VALUES(?,?,0,1,1,1,0)",
        (user_id, name),
    ).lastrowid)


def _backup(db: Path, backup: Path) -> None:
    db, backup = Path(db).resolve(), Path(backup).resolve()
    assert db != backup
    src = sqlite3.connect(str(db))
    dst = sqlite3.connect(str(backup))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _restore(db: Path, backup: Path) -> dict:
    """测试专用恢复：先确认无活动写入者，再清 WAL/SHM，用 backup API 恢复同一库。"""
    db, backup = Path(db).resolve(), Path(backup).resolve()
    assert db != backup
    wal, shm = Path(str(db) + "-wal"), Path(str(db) + "-shm")
    had = wal.exists() or shm.exists()
    probe = sqlite3.connect(str(db), timeout=0.2)
    try:
        probe.execute("BEGIN EXCLUSIVE")
        probe.rollback()
    except sqlite3.OperationalError as exc:
        raise RuntimeError("active writer; restore refused") from exc
    finally:
        probe.close()
    gc.collect()
    for p in (wal, shm):
        if p.exists():
            p.unlink()
    src = sqlite3.connect(str(backup))
    dst = sqlite3.connect(str(db))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return {"had_wal_or_shm": had}


def _upgrade(tree: Path, db: Path) -> None:
    expected = str(Path(db).resolve())
    prefix = "sqlite+aiosqlite:///"
    assert settings.database_url.startswith(prefix)
    actual = str(Path(settings.database_url[len(prefix):]).resolve())
    assert actual == expected, f"target guard: {actual} != {expected}"
    command.upgrade(_cfg(tree), "head")


def test_old_head_upgrade_preserves_rows_and_no_reuse(tmp_path, use_db):
    db = _build_old(tmp_path, use_db, legacy=True)
    _seed(db)
    before = _snapshot(db)
    assert "AUTOINCREMENT" not in before["ddl"].upper()
    assert {(t, n) for t, n, _s in before["objects"]} == {
        ("index", "b2a_idx_ai_char_user"), ("trigger", "b2a_trg_ai_char_touch"),
    }

    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith(f"upgraded:{OLD_HEAD}->{CANDIDATE}"), action
    assert "+guard:check" in action, action
    after = _snapshot(db)
    assert _version(db) == CANDIDATE
    assert "AUTOINCREMENT" in after["ddl"].upper()
    assert "ck_ai_characters_client_safe_id" in after["ddl"]
    for key in ("chars", "states", "sessions", "receipts", "lorebook", "facts", "restrict", "audit"):
        assert after[key] == before[key], key
    assert after["fks"] == before["fks"]
    assert after["objects"] == before["objects"]
    # ai_characters 自身 DDL 必须变化；其余 schema 对象集合逐项不变。
    def _non_ai_objects(snapshot):
        return [
            obj for obj in snapshot["schema_objects"]
            if not (obj[0] == "table" and obj[1] == "ai_characters")
        ]

    assert _non_ai_objects(after) == _non_ai_objects(before)
    assert after["table_count"] == before["table_count"]
    assert _fk_check(db) == []
    assert [r for r in after["sequences"] if r[0] == "ai_characters"] == [("ai_characters", 999)]

    con = sqlite3.connect(str(db))
    try:
        plan = " ".join(str(r) for r in con.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM ai_characters WHERE user_id=1"
        ).fetchall())
        assert "b2a_idx_ai_char_user" in plan
        con.execute("UPDATE ai_characters SET name=name WHERE id=101")
        assert con.execute("SELECT note FROM b2a_audit ORDER BY id DESC LIMIT 1").fetchone()[0] == "touched:101"
        con.commit()
        con.execute("PRAGMA foreign_keys=ON")
        # RESTRICT 子表仍存在时，父表删除必须被拒绝。
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM ai_characters WHERE id=103")
        con.execute("DELETE FROM b2a_restrict_child WHERE character_id=103")
        con.execute("DELETE FROM ai_characters WHERE id=103")
        # CASCADE / SET NULL 语义：状态与会话随父删；收据保留但目标置 NULL。
        assert con.execute(
            "SELECT COUNT(*) FROM character_states WHERE character_id=103"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM chat_sessions WHERE character_id=103"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT character_id FROM character_creation_requests WHERE idempotency_key='target'"
        ).fetchone()[0] is None
        assert _new_char(con, 1, "same") == 1000
        assert _new_char(con, 2, "cross") == 1001
        con.commit()
    finally:
        con.close()
    con = sqlite3.connect(str(db))
    try:
        assert _new_char(con, 1, "reopen") == 1002
        con.commit()
    finally:
        con.close()
    assert _fk_check(db) == []


def test_no_version_legacy_table_stamps_parent_then_upgrades(tmp_path, use_db):
    db = _build_old(tmp_path, use_db, legacy=True)
    con = sqlite3.connect(str(db))
    con.execute("DROP TABLE alembic_version")
    con.commit()
    con.close()
    before = _snapshot(db)

    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith(f"upgraded:stamped-{OLD_HEAD}->{CANDIDATE}"), action
    assert "+guard:check" in action, action
    assert _version(db) == CANDIDATE
    after = _snapshot(db)
    assert "AUTOINCREMENT" in after["ddl"].upper()
    assert "ck_ai_characters_client_safe_id" in after["ddl"]
    assert after["schema_objects"] != before["schema_objects"]
    assert _fk_check(db) == []


def test_orphan_revision_legacy_table_restamps_parent_then_upgrades(tmp_path, use_db):
    db = _build_old(tmp_path, use_db, legacy=True)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE alembic_version SET version_num='deadbeefdead'")
    con.commit()
    con.close()

    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith(f"upgraded:re-stamped-{OLD_HEAD}->{CANDIDATE}"), action
    assert _version(db) == CANDIDATE
    ddl = _snapshot(db)["ddl"]
    assert "AUTOINCREMENT" in ddl.upper()
    assert "ck_ai_characters_client_safe_id" in ddl


def test_head_but_no_autoincrement_refuses(tmp_path, use_db):
    db = _build_old(tmp_path, use_db, legacy=True)
    command.stamp(_cfg(BACKEND / "alembic"), CANDIDATE)
    before = _snapshot(db)
    with pytest.raises(RuntimeError, match="lacks AUTOINCREMENT"):
        migrate._ensure_alembic_revision_sync()
    assert _version(db) == CANDIDATE
    assert _snapshot(db) == before
    assert "AUTOINCREMENT" not in before["ddl"].upper()


def test_already_autoincrement_sequence_repair(tmp_path, use_db):
    db = _build_old(tmp_path, use_db, legacy=False)
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("INSERT INTO users(id,username,nickname) VALUES(1,'a','a')")
    con.execute(
        "INSERT INTO ai_characters(id,user_id,name,is_partner,is_active,"
        "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) VALUES(101,1,'c',0,1,1,1,0)"
    )
    con.execute(
        "INSERT INTO lorebook_entries(user_id,character_id,title,content,keywords,"
        "exclude_keywords,active,is_regex,probability,inclusion_group,sticky_rounds,"
        "cooldown_rounds) VALUES(1,500,'x','x','[]','[]',1,0,100,'',0,0)"
    )
    con.commit()
    con.close()

    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith("upgraded:"), action
    assert _version(db) == CANDIDATE
    con = sqlite3.connect(str(db))
    try:
        assert con.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='ai_characters'"
        ).fetchone()[0] >= 500
        con.execute("UPDATE sqlite_sequence SET seq=1 WHERE name='ai_characters'")
        con.commit()
    finally:
        con.close()

    action2 = migrate._ensure_alembic_revision_sync()
    assert action2.startswith("already_at_head+sequence_repaired:1->500"), action2
    con = sqlite3.connect(str(db))
    try:
        assert con.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='ai_characters'"
        ).fetchone()[0] == 500
    finally:
        con.close()


def test_lagging_schema_full_upgrade(tmp_path, use_db):
    db = use_db(tmp_path / "lagging.db")
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(50), nickname VARCHAR(50))")
    con.commit()
    con.close()
    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith(f"upgraded:->{CANDIDATE}"), action
    assert _version(db) == CANDIDATE
    assert "AUTOINCREMENT" in _snapshot(db)["ddl"].upper()


def test_six_stage_failure_on_working_db_then_restore(tmp_path, use_db):
    results = []
    for stage in STAGES:
        stage_dir = tmp_path / stage.lower()
        stage_dir.mkdir()
        db = _build_old(stage_dir, use_db, legacy=True)
        _seed(db)
        before = _snapshot(db)
        backup = stage_dir / "pre_backup.db"
        _backup(db, backup)
        assert _snapshot(backup) == before
        assert str(Path(settings.database_url.split("///", 1)[1]).resolve()) == str(db.resolve())

        with pytest.raises(Exception) as exc:
            _upgrade(_tree(stage_dir, f"candidate_{stage}", stage=stage), db)
        assert f"injected failure at {stage}" in str(exc.value)
        after_failure = _snapshot(db)
        has_new = any(name == "ai_characters_b2a_new" for _t, name, _b, _s in after_failure["schema_objects"])
        assert has_new, f"{stage}: expected leftover new table"
        assert _snapshot(backup) == before
        pre_fp = hashlib.sha256(json.dumps(before, sort_keys=True, default=str).encode()).hexdigest()
        fail_fp = hashlib.sha256(json.dumps(after_failure, sort_keys=True, default=str).encode()).hexdigest()

        info = _restore(db, backup)
        recovered = _snapshot(db)
        assert recovered == before, stage
        assert _version(db) == OLD_HEAD
        assert info["had_wal_or_shm"] in (True, False)
        recovered_fp = hashlib.sha256(json.dumps(recovered, sort_keys=True, default=str).encode()).hexdigest()
        results.append({
            "stage": stage,
            "mode": "backup_restore",
            "had_wal_or_shm": info["had_wal_or_shm"],
            "pre_fingerprint": pre_fp,
            "after_failure_fingerprint": fail_fp,
            "recovered_fingerprint": recovered_fp,
            "pre_table_count": before["table_count"],
            "after_failure_table_count": after_failure["table_count"],
            "after_failure_has_new_table": has_new,
            "after_failure_old_rows": len(after_failure["chars"] or []),
            "recovered_equal": recovered_fp == pre_fp,
        })

    out = BACKEND.parent / "logs" / "stage_results_b2b.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert len(results) == len(STAGES)


def test_wal_leftover_restore_and_active_writer_refusal(tmp_path):
    db = tmp_path / "wal.db"
    backup = tmp_path / "wal_backup.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO t(id,v) VALUES(1,'before')")
    con.commit()
    con.close()
    _backup(db, backup)

    code = (
        "import os,sqlite3,sys\n"
        "p=sys.argv[1]\n"
        "c=sqlite3.connect(p)\n"
        "c.execute('PRAGMA journal_mode=WAL')\n"
        "c.execute(\"UPDATE t SET v='after' WHERE id=1\")\n"
        "c.commit()\n"
        "os._exit(0)\n"
    )
    subprocess.run([sys.executable, "-c", code, str(db)], check=True, timeout=30)
    assert Path(str(db) + "-wal").exists() or Path(str(db) + "-shm").exists()

    info = _restore(db, backup)
    assert info["had_wal_or_shm"] is True
    con = sqlite3.connect(str(db))
    try:
        assert con.execute("SELECT v FROM t WHERE id=1").fetchone()[0] == "before"
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()
    assert _snapshot(backup)["table_count"] == 1

    writer = sqlite3.connect(str(db), timeout=0.2)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE t SET v='held' WHERE id=1")
        with pytest.raises(RuntimeError, match="active writer"):
            _restore(db, backup)
        writer.rollback()
    finally:
        writer.close()


# ── B2b 返工 R1：未知条件引用/客户端上限在写前拒绝 ─────────────────────

def test_unknown_conditional_ref_rejected_before_ddl(tmp_path, use_db):
    db = _build_old(tmp_path, use_db, legacy=True)
    _seed(db)
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO pets(user_id,name,species,level,exp,hunger,mood,energy,"
        "cleanliness,status_text,owner_type,owner_id) "
        "VALUES(1,'p','cat',1,0,80,80,80,80,'ok','',999)"
    )
    con.commit()
    con.close()
    before = _snapshot(db)
    with pytest.raises(RuntimeError, match="unknown/null owner_type"):
        migrate._ensure_alembic_revision_sync()
    assert _snapshot(db) == before
    assert _version(db) == OLD_HEAD
    assert "AUTOINCREMENT" not in _snapshot(db)["ddl"].upper()


def test_above_client_safe_ref_rejected_before_ddl(tmp_path, use_db):
    db = _build_old(tmp_path, use_db, legacy=True)
    _seed(db)
    con = sqlite3.connect(str(db))
    con.execute(
        "UPDATE lorebook_entries SET character_id=? WHERE character_id=999",
        (9007199254740993,),
    )
    con.commit()
    con.close()
    before = _snapshot(db)
    with pytest.raises(RuntimeError, match="client-safe"):
        migrate._ensure_alembic_revision_sync()
    assert _snapshot(db) == before
    assert _version(db) == OLD_HEAD


def test_known_noncharacter_large_id_ignored(tmp_path, use_db):
    db = _build_old(tmp_path, use_db, legacy=True)
    _seed(db)
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO pets(user_id,name,species,level,exp,hunger,mood,energy,"
        "cleanliness,status_text,owner_type,owner_id) "
        "VALUES(1,'p','cat',1,0,80,80,80,80,'ok','user',9007199254739993)"
    )
    con.commit()
    con.close()
    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith(f"upgraded:{OLD_HEAD}->{CANDIDATE}"), action
    after = _snapshot(db)
    assert "AUTOINCREMENT" in after["ddl"].upper()
    assert [r for r in after["sequences"] if r[0] == "ai_characters"] == [("ai_characters", 999)]
    con = sqlite3.connect(str(db))
    try:
        assert con.execute(
            "SELECT owner_type, owner_id FROM pets WHERE name='p'"
        ).fetchone() == ("user", 9007199254739993)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("DELETE FROM b2a_restrict_child WHERE character_id=103")
        con.execute("DELETE FROM ai_characters WHERE id=103")
        cur = con.execute(
            "INSERT INTO ai_characters(user_id,name,is_partner,is_active,"
            "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) "
            "VALUES(1,'n',0,1,1,1,0)"
        )
        assert int(cur.lastrowid) == 1000
        con.commit()
    finally:
        con.close()
