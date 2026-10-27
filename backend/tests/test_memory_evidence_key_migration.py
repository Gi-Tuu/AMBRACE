"""Exercise the actual additive migration against a pre-key SQLite schema."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def test_upgrade_keeps_legacy_rows_and_enforces_unique_replays(tmp_path):
    path = Path(__file__).parents[1] / "alembic/versions/c4e6f8a0b2d4_memory_evidence_write_key.py"
    spec = importlib.util.spec_from_file_location("evidence_key_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE memories (id INTEGER PRIMARY KEY,content TEXT NOT NULL)"))
            connection.execute(text("INSERT INTO memories VALUES (1,'legacy_one'),(2,'legacy_two')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()  # Re-running after a partial startup is safe.
            assert connection.execute(text("SELECT id,content,write_key FROM memories ORDER BY id")).all() == [
                (1, "legacy_one", None), (2, "legacy_two", None)]
            connection.execute(text("INSERT INTO memories VALUES (3,'new','same-evidence-key')"))
        with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                connection.execute(text("INSERT INTO memories VALUES (4,'duplicate','same-evidence-key')"))
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert "write_key" in {col["name"] for col in inspect(connection).get_columns("memories")}
    finally:
        engine.dispose()


def test_official_alembic_upgrades_from_parent_revision(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from app.config import settings

    path = tmp_path / "parent.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE memories (id INTEGER PRIMARY KEY,content TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO memories VALUES (1,'legacy_fact')"))
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('b2a0c1d2e3f4')"))
    backend = Path(__file__).parents[1]
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{path}")
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    try:
        command.upgrade(config, "c4e6f8a0b2d4")  # Test this migration, not future descendants.
        with engine.connect() as connection:
            assert connection.execute(text("SELECT write_key FROM memories WHERE id=1")).scalar() is None
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "c4e6f8a0b2d4"
    finally:
        engine.dispose()
