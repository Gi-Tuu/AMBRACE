# -*- coding: utf-8 -*-
"""B2c 返工 R1：守卫必须按规范定义核验，不接受同名/正确 WHEN 的空壳对象。"""
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.db.character_id_guard import (
    CHECK_NAME,
    CLIENT_SAFE_MAX_ID,
    INSERT_TRIGGER,
    UPDATE_TRIGGER,
    ensure_client_safe_id_guard,
    verify_client_safe_id_guard,
)


def _db(tmp_path: Path, ddl: str | None = None) -> Path:
    db = tmp_path / "guard.db"
    con = sqlite3.connect(str(db))
    con.execute(
        ddl
        or "CREATE TABLE ai_characters(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
    )
    con.execute("INSERT INTO ai_characters(id,name) VALUES(?, 'seed')", (CLIENT_SAFE_MAX_ID - 1,))
    con.commit()
    con.close()
    return db


def _verify(db: Path) -> dict:
    eng = create_engine("sqlite:///" + db.as_posix())
    try:
        with eng.connect() as conn:
            return verify_client_safe_id_guard(conn)
    finally:
        eng.dispose()


def _ensure(db: Path) -> dict:
    eng = create_engine("sqlite:///" + db.as_posix())
    try:
        with eng.begin() as conn:
            return ensure_client_safe_id_guard(conn)
    finally:
        eng.dispose()


def _trigger_sql(db: Path) -> dict:
    con = sqlite3.connect(str(db))
    try:
        return dict(con.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='ai_characters'"
        ).fetchall())
    finally:
        con.close()


def _assert_boundary_blocks(db: Path):
    con = sqlite3.connect(str(db))
    try:
        assert con.execute("INSERT INTO ai_characters(name) VALUES('last')").lastrowid == CLIENT_SAFE_MAX_ID
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO ai_characters(name) VALUES('overflow')")
        con.rollback()
        assert con.execute("SELECT MAX(id) FROM ai_characters").fetchone()[0] == CLIENT_SAFE_MAX_ID
    finally:
        con.close()


def test_noop_same_name_triggers_are_not_present_and_are_repaired(tmp_path):
    db = _db(tmp_path)
    con = sqlite3.connect(str(db))
    for event in ("INSERT", "UPDATE OF id"):
        name = INSERT_TRIGGER if event == "INSERT" else UPDATE_TRIGGER
        con.execute(
            f'CREATE TRIGGER "{name}" AFTER {event} ON ai_characters FOR EACH ROW '
            f"WHEN NEW.id > {CLIENT_SAFE_MAX_ID} BEGIN SELECT 1; END"
        )
    con.commit()
    con.close()

    bad = _verify(db)
    assert bad["present"] is False
    assert bad["mode"] == "invalid"
    assert bad["check"] is False
    assert sorted(bad["invalid_triggers"]) == sorted([INSERT_TRIGGER, UPDATE_TRIGGER])

    fixed = _ensure(db)
    assert fixed["present"] is True and fixed["mode"] == "triggers", fixed
    assert fixed["invalid_triggers"] == []
    sqls = _trigger_sql(db)
    assert "RAISE(ABORT" in sqls[INSERT_TRIGGER].upper()
    assert "RAISE(ABORT" in sqls[UPDATE_TRIGGER].upper()
    _assert_boundary_blocks(db)


def test_ineffective_check_is_not_present_and_triggers_are_installed(tmp_path):
    ddl = (
        "CREATE TABLE ai_characters(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, "
        f'CONSTRAINT "{CHECK_NAME}" CHECK (id <= {CLIENT_SAFE_MAX_ID} OR 1))'
    )
    db = _db(tmp_path, ddl)
    bad = _verify(db)
    assert bad["present"] is False
    assert bad["mode"] == "none"
    assert bad["check"] is False

    fixed = _ensure(db)
    assert fixed["present"] is True and fixed["mode"] == "triggers", fixed
    assert fixed["check"] is False
    _assert_boundary_blocks(db)


@pytest.mark.parametrize("event,when,body", [
    ("DELETE", f"NEW.id > {CLIENT_SAFE_MAX_ID}", "BEGIN SELECT RAISE(ABORT, 'character_id_capacity_exhausted'); END"),
    ("INSERT", f"NEW.id > {CLIENT_SAFE_MAX_ID + 1}", "BEGIN SELECT RAISE(ABORT, 'character_id_capacity_exhausted'); END"),
    ("INSERT", "1", "BEGIN SELECT RAISE(ABORT, 'character_id_capacity_exhausted'); END"),
    ("INSERT", f"NEW.id > {CLIENT_SAFE_MAX_ID}", "BEGIN SELECT 1; END"),
])
def test_invalid_trigger_variants_are_repaired(tmp_path, event, when, body):
    db = _db(tmp_path)
    con = sqlite3.connect(str(db))
    con.execute(
        f'CREATE TRIGGER "{INSERT_TRIGGER}" AFTER {event} ON ai_characters '
        f"FOR EACH ROW WHEN {when} {body}"
    )
    con.commit()
    con.close()
    bad = _verify(db)
    assert bad["present"] is False
    assert INSERT_TRIGGER in bad["invalid_triggers"]
    fixed = _ensure(db)
    assert fixed["present"] is True and fixed["mode"] == "triggers", fixed
    _assert_boundary_blocks(db)


def test_verify_is_read_only_and_preserves_business_trigger(tmp_path):
    db = _db(tmp_path)
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE audit(id INTEGER PRIMARY KEY, note TEXT)")
    con.execute(
        'CREATE TRIGGER "business_audit" AFTER UPDATE ON ai_characters '
        "BEGIN INSERT INTO audit(note) VALUES('touched'); END"
    )
    con.commit()
    before_objects = dict(con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type IN ('table','trigger','index')"
    ).fetchall())
    before_seq = con.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name").fetchall()
    before_rows = con.execute("SELECT id,name FROM ai_characters ORDER BY id").fetchall()
    con.close()

    assert _verify(db)["present"] is False
    assert _verify(db)["present"] is False

    con = sqlite3.connect(str(db))
    after_verify = dict(con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type IN ('table','trigger','index')"
    ).fetchall())
    assert after_verify == before_objects
    assert con.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name").fetchall() == before_seq
    assert con.execute("SELECT id,name FROM ai_characters ORDER BY id").fetchall() == before_rows
    con.close()

    fixed = _ensure(db)
    assert fixed["present"] is True
    sqls = _trigger_sql(db)
    assert sqls["business_audit"] == before_objects["business_audit"]
    first_guard = {n: sqls[n] for n in (INSERT_TRIGGER, UPDATE_TRIGGER)}
    assert _ensure(db)["present"] is True
    sqls2 = _trigger_sql(db)
    assert {n: sqls2[n] for n in (INSERT_TRIGGER, UPDATE_TRIGGER)} == first_guard
    assert sqls2["business_audit"] == before_objects["business_audit"]

    con = sqlite3.connect(str(db))
    con.execute("UPDATE ai_characters SET name=name WHERE id=?", (CLIENT_SAFE_MAX_ID - 1,))
    assert con.execute("SELECT note FROM audit ORDER BY id DESC LIMIT 1").fetchone()[0] == "touched"
    con.commit()
    con.close()


def test_valid_check_with_invalid_trigger_is_not_present_and_is_cleaned(tmp_path):
    ddl = (
        "CREATE TABLE ai_characters(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, "
        f'CONSTRAINT "{CHECK_NAME}" CHECK (id <= {CLIENT_SAFE_MAX_ID}))'
    )
    db = _db(tmp_path, ddl)
    con = sqlite3.connect(str(db))
    con.execute(
        f'CREATE TRIGGER "{INSERT_TRIGGER}" AFTER INSERT ON ai_characters FOR EACH ROW '
        f"WHEN NEW.id > {CLIENT_SAFE_MAX_ID} BEGIN SELECT 1; END"
    )
    con.commit()
    con.close()
    bad = _verify(db)
    assert bad["present"] is False
    assert bad["mode"] == "invalid"
    assert bad["check"] is True
    assert bad["invalid_triggers"] == [INSERT_TRIGGER]
    fixed = _ensure(db)
    assert fixed["present"] is True and fixed["mode"] == "check", fixed
    assert fixed["invalid_triggers"] == []
    assert _trigger_sql(db) == {}
    _assert_boundary_blocks(db)
