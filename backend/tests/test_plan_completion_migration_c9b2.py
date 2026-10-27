"""Actual Alembic history and SQLite FK/backup safety, never dbclone/create_all."""
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import importlib.util

from app.config import settings
from app.models import Base, User, AICharacter, ChatSession, ChatMessage

BACKEND = Path(__file__).parents[1]
PARENT = "c4e6f8a0b2d4"
REV = "d6f8a0b2c4e6"
TABLES = ("business_plan_items", "plan_completion_evidence")


@pytest.fixture
def history(tmp_path, monkeypatch):
    path = tmp_path / "historical.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{path}")
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, PARENT)  # Full real historical chain, no version stamping.
    engine = sa.create_engine(f"sqlite:///{path}")
    @sa.event.listens_for(engine, "connect")
    def fk_on(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    # Legacy initial migration reads latest metadata. Remove only future tables to
    # reproduce the actual pre-C9 shape; old tables/data/revision remain untouched.
    with engine.begin() as conn:
        for name in reversed(TABLES):
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {name}")
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == PARENT
    with Session(engine) as db:
        db.add(User(id=101, username="synthetic_migration_owner",nickname="Synthetic Owner"))
        db.commit()
        db.add(AICharacter(id=201,user_id=101,name="Synthetic",personality="test",
                           chat_style="plain",relation_type="assistant"))
        db.commit()
        db.add(ChatSession(id=301,user_id=101,character_id=201))
        db.commit()
        db.add(ChatMessage(id=401,session_id=301,sender_type="user",content="synthetic plan"))
        db.commit()
    try:
        yield path, engine, cfg
    finally:
        engine.dispose()


def _insert(conn, key):
    conn.exec_driver_sql("INSERT INTO business_plan_items (item_key,user_id,character_id,chat_session_id,source_message_id,source_event_at,source_hash,ordinal,target_text) VALUES (?,101,201,301,401,'2026-01-01','hash',0,'synthetic')",(key,))
    return conn.exec_driver_sql("SELECT last_insert_rowid()").scalar_one()


def test_real_history_upgrade_fk_unique_sequence_backup_and_restore(history,tmp_path):
    path, engine, cfg = history
    backup = tmp_path / "before.db"
    with sqlite3.connect(path) as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
    command.upgrade(cfg, REV)
    command.upgrade(cfg, REV)
    with engine.begin() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == REV
        assert conn.exec_driver_sql("SELECT content FROM chat_messages WHERE id=401").scalar_one() == "synthetic plan"
        first = _insert(conn,"one")
        with pytest.raises(sa.exc.IntegrityError):
            _insert(conn,"one")
        with pytest.raises(sa.exc.IntegrityError):
            conn.exec_driver_sql("UPDATE business_plan_items SET user_id=999 WHERE id=?",(first,))
        conn.exec_driver_sql("DELETE FROM business_plan_items WHERE id=?",(first,))
        second = _insert(conn,"two")
        assert second > first
        conn.exec_driver_sql("DELETE FROM chat_messages WHERE id=401")
        assert conn.exec_driver_sql("SELECT source_message_id FROM business_plan_items").scalar_one() is None
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    with pytest.raises(RuntimeError,match="restore the pre-migration backup"):
        command.downgrade(cfg,PARENT)
    engine.dispose()
    with sqlite3.connect(backup) as src, sqlite3.connect(path) as dst:
        src.backup(dst)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == PARENT
        assert conn.exec_driver_sql("SELECT content FROM chat_messages WHERE id=401").scalar_one() == "synthetic plan"
        assert not sa.inspect(conn).has_table(TABLES[0])
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert conn.exec_driver_sql("SELECT seq FROM sqlite_sequence WHERE name='ai_characters'").scalar_one() == 201
    command.upgrade(cfg,REV)  # Restore is genuinely usable, not merely readable.


def test_migration_schema_matches_orm_and_handles_bootstrap_replay(history):
    _, engine, cfg = history
    command.upgrade(cfg,REV)
    module_path = BACKEND / "alembic/versions/d6f8a0b2c4e6_plan_completion_evidence.py"
    spec = importlib.util.spec_from_file_location("completion_migration",module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()  # Already-created latest bootstrap schema must verify.
        inspector = sa.inspect(conn)
        for name in TABLES:
            actual = {c["name"]:c for c in inspector.get_columns(name)}
            expected = Base.metadata.tables[name]
            assert set(actual) == set(expected.columns.keys())
            for column in expected.columns:
                assert str(actual[column.name]["type"]) == str(column.type)
                assert actual[column.name]["nullable"] == column.nullable
            fks = {(f["constrained_columns"][0],f["referred_table"],f["options"].get("ondelete"))
                   for f in inspector.get_foreign_keys(name)}
            assert fks == {(c.name,f.column.table.name,f.ondelete)
                           for c in expected.columns for f in c.foreign_keys}
        _insert(conn,"cleanup")
        conn.exec_driver_sql("DELETE FROM chat_sessions WHERE id=301")
        assert conn.exec_driver_sql("SELECT count(*) FROM business_plan_items").scalar_one() == 0
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_partial_preexisting_table_refuses_to_stamp(history):
    _, engine, cfg = history
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE business_plan_items (id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError,match="Incomplete C9 ledger schema"):
        command.upgrade(cfg,REV)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == PARENT
        assert conn.exec_driver_sql("SELECT count(*) FROM users WHERE id=101").scalar_one() == 1



def test_interrupted_install_resumes_without_losing_parent_data(history,monkeypatch):
    from alembic import op
    _, engine, cfg = history
    original = op.create_table
    def interrupted(name,*args,**kwargs):
        if name == "plan_completion_evidence":
            raise RuntimeError("synthetic second-table interruption")
        return original(name,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(op,"create_table",interrupted)
        with pytest.raises(RuntimeError,match="synthetic second-table interruption"):
            command.upgrade(cfg,REV)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == PARENT
        assert sa.inspect(conn).has_table("business_plan_items")
        assert not sa.inspect(conn).has_table("plan_completion_evidence")
        assert conn.exec_driver_sql("SELECT content FROM chat_messages WHERE id=401").scalar_one() == "synthetic plan"
    command.upgrade(cfg,REV)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == REV
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_preexisting_wrong_default_is_rejected(history):
    _, engine, cfg = history
    command.upgrade(cfg,REV)
    path = BACKEND / "alembic/versions/d6f8a0b2c4e6_plan_completion_evidence.py"
    spec = importlib.util.spec_from_file_location("completion_default_check",path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        ddl = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='business_plan_items'").scalar_one()
        conn.exec_driver_sql("DROP TABLE plan_completion_evidence")
        conn.exec_driver_sql("DROP TABLE business_plan_items")
        altered = ddl.replace("DEFAULT '0'","DEFAULT '1'").replace("DEFAULT 0","DEFAULT 1")
        assert altered != ddl
        conn.exec_driver_sql(altered)
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError,match="Invalid C9 default"):
                module.upgrade()
