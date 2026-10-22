# -*- coding: utf-8 -*-
"""B2b 返工 R1/R2 边界：条件引用分类、未来 head 锚点、客户端容量、恢复边界。"""
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from app.config import settings
from app.db import migrate
from app.db.character_id_guard import CHECK_NAME

BACKEND = Path(__file__).resolve().parents[1]
OLD_HEAD = "e2f4a6b8c0d2"
CANDIDATE = "b2a0c1d2e3f4"
FUTURE = "c0ffee123456"
CLIENT_SAFE_MAX_ID = (1 << 53) - 1
ABOVE_CLIENT_SAFE = CLIENT_SAFE_MAX_ID + 2  # 2^53+1，Web 不可精确表示


def _cfg(tree: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(tree.resolve()))
    cfg.set_main_option("prepend_sys_path", str(BACKEND.resolve()))
    cfg.set_main_option("sqlalchemy.url", "")
    return cfg


def _tree(tmp_path: Path, name: str, *, candidate: bool = True,
          future: bool = False) -> Path:
    tree = tmp_path / name
    shutil.copytree(BACKEND / "alembic", tree, ignore=shutil.ignore_patterns("__pycache__"))
    if not candidate:
        p = tree / "versions" / "b2a0c1d2e3f4_ai_characters_autoincrement.py"
        if p.exists():
            p.unlink()
    if future:
        (tree / "versions" / "c0ffee123456_future_noop.py").write_text(
            "# synthetic successor for rework test; not part of delivery chain\n"
            f'revision = "{FUTURE}"\n'
            f'down_revision = "{CANDIDATE}"\n'
            "branch_labels = None\n"
            "depends_on = None\n\n"
            "def upgrade():\n    pass\n\n"
            "def downgrade():\n    pass\n",
            encoding="utf-8",
            newline="\n",
        )
    return tree


def _use(monkeypatch, tmp_path: Path, db: Path, tree: Path) -> Path:
    db = Path(db).resolve()
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///" + db.as_posix())
    monkeypatch.setattr(settings, "chroma_persist_dir", str((tmp_path / "vector").resolve()))
    monkeypatch.setattr(migrate, "_ALEMBIC_DIR", Path(tree).resolve())
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


def _build_legacy(tmp_path: Path, monkeypatch, *, seed: bool = True) -> Path:
    old_tree = _tree(tmp_path, "old_tree", candidate=False)
    db = _use(monkeypatch, tmp_path, tmp_path / "old.db", old_tree)
    command.upgrade(_cfg(old_tree), OLD_HEAD)
    _make_legacy_ai(db)
    if seed:
        con = sqlite3.connect(str(db))
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("INSERT INTO users(id,username,nickname) VALUES(1,'b2b','B')")
        con.execute(
            "INSERT INTO ai_characters(id,user_id,name,is_partner,is_active,"
            "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) "
            "VALUES(101,1,'keep',0,1,1,1,0)"
        )
        con.execute(
            "INSERT INTO lorebook_entries(user_id,character_id,title,content,keywords,"
            "exclude_keywords,active,is_regex,probability,inclusion_group,sticky_rounds,"
            "cooldown_rounds) VALUES(1,999,'orphan','orphan','[]','[]',1,0,100,'',0,0)"
        )
        con.commit()
        con.close()
    return db


def _version(db: Path) -> str | None:
    con = sqlite3.connect(str(db))
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _ddl(db: Path) -> str:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
        ).fetchone()[0]
    finally:
        con.close()


def _state(db: Path) -> dict:
    con = sqlite3.connect(str(db))
    try:
        has_seq = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone() is not None
        return {
            "ddl": con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
            ).fetchone()[0],
            "version": con.execute("SELECT version_num FROM alembic_version").fetchone()[0],
            "sequences": [tuple(r) for r in con.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name")] if has_seq else None,
            "fks": [tuple(r) for r in con.execute("PRAGMA foreign_key_list(ai_characters)")],
            "chars": [tuple(r) for r in con.execute(
                "SELECT id,user_id,name FROM ai_characters ORDER BY id")],
        }
    finally:
        con.close()


# ── R1：条件引用三分类 ─────────────────────────────────────────────

_CONDITIONAL_CASES = [
    (
        "memories",
        "CREATE TABLE memories (character_id INTEGER REFERENCES ai_characters(id), "
        "speaker_type TEXT, speaker_id INTEGER)",
        "INSERT INTO memories VALUES(1,'character',10)",
        "INSERT INTO memories VALUES(1,'user',9007199254739993)",
        "INSERT INTO memories VALUES(1,'ai',999)",
        "INSERT INTO memories VALUES(1,NULL,999)",
        True,
    ),
    (
        "group_memories",
        "CREATE TABLE group_memories (speaker_type TEXT, speaker_id INTEGER)",
        "INSERT INTO group_memories VALUES('ai',10)",
        "INSERT INTO group_memories VALUES('user',9007199254739993)",
        "INSERT INTO group_memories VALUES('weird',999)",
        "INSERT INTO group_memories VALUES(NULL,999)",
        True,
    ),
    (
        "world_facts",
        "CREATE TABLE world_facts (character_id INTEGER, subject_type TEXT, subject_id INTEGER)",
        "INSERT INTO world_facts VALUES(1,'character',10)",
        "INSERT INTO world_facts VALUES(1,'user',9007199254739993)",
        "INSERT INTO world_facts VALUES(1,'tool',999)",
        "INSERT INTO world_facts VALUES(1,NULL,999)",
        True,
    ),
    (
        "moment_comments",
        "CREATE TABLE moment_comments (sender_type TEXT, sender_id INTEGER)",
        "INSERT INTO moment_comments VALUES('ai',10)",
        "INSERT INTO moment_comments VALUES('user',9007199254739993)",
        "INSERT INTO moment_comments VALUES('system',999)",
        "INSERT INTO moment_comments VALUES(NULL,999)",
        True,
    ),
    (
        "pets",
        "CREATE TABLE pets (owner_type TEXT, owner_id INTEGER)",
        "INSERT INTO pets VALUES('ai',10)",
        "INSERT INTO pets VALUES('user',9007199254739993)",
        "INSERT INTO pets VALUES('',999)",
        "INSERT INTO pets VALUES(NULL,999)",
        False,  # NULL 对 pets 是已证明用户宠物，不是未知
    ),
]


def _scan(tmp_path: Path, name: str, ddl_sql: str, rows: list[str]):
    db = tmp_path / f"{name}.db"
    eng = create_engine("sqlite:///" + db.as_posix())
    with eng.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE ai_characters (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO ai_characters(id) VALUES(1)")
        conn.exec_driver_sql(ddl_sql)
        for sql in rows:
            conn.exec_driver_sql(sql)
    try:
        with eng.connect() as conn:
            return migrate.scan_character_id_watermark(conn)
    finally:
        eng.dispose()


@pytest.mark.parametrize("name,ddl_sql,known,nonchar,unknown,null_row,null_is_unknown", _CONDITIONAL_CASES)
def test_known_character_counts_and_known_noncharacter_large_id_ignored(
    tmp_path, name, ddl_sql, known, nonchar, unknown, null_row, null_is_unknown,
):
    scan = _scan(tmp_path, f"{name}_known", ddl_sql, [known, nonchar])
    assert scan["high"] == 10
    assert any(name in r for r in scan["known_refs"])


@pytest.mark.parametrize("name,ddl_sql,known,nonchar,unknown,null_row,null_is_unknown", _CONDITIONAL_CASES)
def test_unknown_discriminator_with_positive_id_rejects(
    tmp_path, name, ddl_sql, known, nonchar, unknown, null_row, null_is_unknown,
):
    with pytest.raises(RuntimeError, match="unknown/null"):
        _scan(tmp_path, f"{name}_unknown", ddl_sql, [known, unknown])
    if null_is_unknown:
        with pytest.raises(RuntimeError, match="unknown/null"):
            _scan(tmp_path, f"{name}_null", ddl_sql, [known, null_row])
    else:
        # pets: NULL 是已证明用户宠物；大 id 不抬高角色序列。
        scan = _scan(tmp_path, f"{name}_null_known", ddl_sql, [known, null_row])
        assert scan["high"] == 10


def test_unknown_discriminator_zero_id_is_sentinel_not_rejected(tmp_path):
    ddl = "CREATE TABLE memories (character_id INTEGER, speaker_type TEXT, speaker_id INTEGER)"
    scan = _scan(tmp_path, "zero", ddl, [
        "INSERT INTO memories VALUES(0,'character',10)",
        "INSERT INTO memories VALUES(0,'ai',0)",
        "INSERT INTO memories VALUES(0,NULL,NULL)",
    ])
    assert scan["high"] == 10


def test_client_safe_range_and_capacity(tmp_path):
    assert migrate.CLIENT_SAFE_MAX_ID == 9007199254740991
    assert migrate.CLIENT_SAFE_MAX_ID - 999 == 9007199254739992  # 不是 9007199254740992
    ddl = "CREATE TABLE lorebook_entries (character_id INTEGER)"
    scan = _scan(tmp_path, "maxsafe", ddl, [
        "INSERT INTO lorebook_entries VALUES(9007199254740991)",
    ])
    assert scan["high"] == 9007199254740991
    with pytest.raises(RuntimeError, match="client-safe"):
        _scan(tmp_path, "above", ddl, [
            f"INSERT INTO lorebook_entries VALUES({ABOVE_CLIENT_SAFE})",
        ])


def test_replay_contract_for_migration_helper(tmp_path):
    source = (BACKEND / "alembic" / "versions" /
              "b2a0c1d2e3f4_ai_characters_autoincrement.py").read_text(encoding="utf-8")
    assert "from app.db.migrate import" in source
    assert "scan_character_id_watermark" in source
    assert "repair_ai_character_sequence" in source
    # 固定扫描结果：helper 语义变化会让历史迁移重放责任显式暴露。
    scan = _scan(tmp_path, "replay",
                 "CREATE TABLE lorebook_entries (character_id INTEGER)",
                 ["INSERT INTO lorebook_entries VALUES(7)"])
    assert scan["high"] == 7
    assert scan["unknown_columns"] == []
    assert any("lorebook_entries.character_id" in x for x in scan["known_refs"])


def test_begin_exclusive_is_not_no_connection_proof(tmp_path):
    """BEGIN EXCLUSIVE 成功只说明探测时无冲突写事务；空闲连接仍可能存在。"""
    db = tmp_path / "idle.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    con.commit()
    idle = sqlite3.connect(str(db))  # 保持打开，不做事务
    probe = sqlite3.connect(str(db), timeout=0.2)
    try:
        probe.execute("BEGIN EXCLUSIVE")
        probe.rollback()
        # 若这里成功，说明“无活动写事务”不能推导出“无空闲连接/已停服”
        assert idle.execute("SELECT 1").fetchone()[0] == 1
    finally:
        probe.close()
        idle.close()
        con.close()


# ── R2：约束 revision 与未来 head 分离 ─────────────────────────────

def _drop_version(db: Path) -> None:
    con = sqlite3.connect(str(db))
    con.execute("DROP TABLE alembic_version")
    con.commit()
    con.close()


def test_future_head_no_version_still_runs_constraint_rebuild(tmp_path, monkeypatch):
    db = _build_legacy(tmp_path, monkeypatch)
    _drop_version(db)
    future = _tree(tmp_path, "future_tree", future=True)
    _use(monkeypatch, tmp_path, db, future)
    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith(f"upgraded:stamped-{OLD_HEAD}->{FUTURE}"), action
    assert _version(db) == FUTURE
    assert "AUTOINCREMENT" in _ddl(db).upper()
    assert CHECK_NAME in _ddl(db)
    con = sqlite3.connect(str(db))
    try:
        assert con.execute("SELECT id,user_id,name FROM ai_characters").fetchall() == [(101, 1, "keep")]
        assert con.execute("SELECT content FROM lorebook_entries WHERE character_id=999").fetchone()[0] == "orphan"
    finally:
        con.close()
    assert migrate._ensure_alembic_revision_sync().startswith("already_at_head")


def test_future_head_orphan_version_still_runs_constraint_rebuild(tmp_path, monkeypatch):
    db = _build_legacy(tmp_path, monkeypatch)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE alembic_version SET version_num='deadbeefdead'")
    con.commit()
    con.close()
    future = _tree(tmp_path, "future_tree", future=True)
    _use(monkeypatch, tmp_path, db, future)
    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith(f"upgraded:re-stamped-{OLD_HEAD}->{FUTURE}"), action
    assert _version(db) == FUTURE
    assert "AUTOINCREMENT" in _ddl(db).upper()
    assert CHECK_NAME in _ddl(db)


def test_current_head_no_version_runs_constraint(tmp_path, monkeypatch):
    db = _build_legacy(tmp_path, monkeypatch)
    _drop_version(db)
    tree = _tree(tmp_path, "current_tree", future=False)
    _use(monkeypatch, tmp_path, db, tree)
    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith(f"upgraded:stamped-{OLD_HEAD}->{CANDIDATE}"), action
    assert _version(db) == CANDIDATE
    assert "AUTOINCREMENT" in _ddl(db).upper()
    assert CHECK_NAME in _ddl(db)


def test_future_head_already_upgraded_is_idempotent(tmp_path, monkeypatch):
    db = _build_legacy(tmp_path, monkeypatch)
    future = _tree(tmp_path, "future_tree", future=True)
    _use(monkeypatch, tmp_path, db, future)
    command.stamp(_cfg(future), OLD_HEAD)
    command.upgrade(_cfg(future), "head")
    assert _version(db) == FUTURE
    assert "AUTOINCREMENT" in _ddl(db).upper()
    assert CHECK_NAME in _ddl(db)
    before = _state(db)
    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith("already_at_head"), action
    assert _state(db) == before
    assert migrate._ensure_alembic_revision_sync().startswith("already_at_head")
    assert _state(db) == before


def test_constraint_anchor_not_latest_head_parent(tmp_path, monkeypatch):
    from alembic.script import ScriptDirectory
    future = _tree(tmp_path, "future_tree", future=True)
    monkeypatch.setattr(migrate, "_ALEMBIC_DIR", future.resolve())
    script = ScriptDirectory.from_config(migrate._alembic_config())
    assert script.get_current_head() == FUTURE
    assert migrate._previous_head(script, FUTURE) == CANDIDATE
    assert migrate._constraint_base(script) == OLD_HEAD
    assert migrate._is_ancestor(script, CANDIDATE, FUTURE)
