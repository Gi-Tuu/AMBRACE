"""ensure_indexes 与真实历史 schema 差异的回放兼容性。

验证口径：
- 旧表尚未加未来列时，ensure_indexes 不尝试该未来列索引（不报 no such column）。
- 旧表已有列却缺索引时，ensure_indexes 补建且唯一约束真实生效。
- 后期补列后再次调用 ensure_indexes，能补对应索引。
- 重复执行不重复创建（幂等）。
- 列存在但真实 DDL 失败（如唯一索引列已有重复值）仍抛出，不被静默吞掉。
- 官方 alembic 从空库 baseline 完整 upgrade head，最终 write_key 列与唯一索引真实可用。

全部使用 tmp_path 的真文件 SQLite + 真实 ORM metadata / alembic Operations，
engine 按顺序 dispose，不裸 mkdtemp，不 monkeypatch.undo。
"""
import importlib.util
from pathlib import Path

from alembic import op as _alembic_op
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

HELPERS_PATH = Path(__file__).parents[1] / "alembic" / "_helpers.py"


def _load_ensure_indexes():
    spec = importlib.util.spec_from_file_location("ambrace_helpers_replay", HELPERS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ensure_indexes


def _load_metadata():
    import app.models  # noqa: F401  # 注册全部模型到 Base.metadata
    from app.models.base import Base
    return Base.metadata


def _ensure_indexes_on(connection, metadata):
    with Operations.context(MigrationContext.configure(connection)):
        ensure_indexes = _load_ensure_indexes()
        return ensure_indexes(_alembic_op, metadata)


def _columns(connection, table):
    return {c["name"] for c in inspect(connection).get_columns(table)}


def _indexes(connection, table):
    return {ix["name"] for ix in inspect(connection).get_indexes(table)}


def test_旧表无未来列时不尝试未来索引(tmp_path):
    """模拟 e1b2c3d4e5f6 运行点：memories 只有旧列，metadata 前瞻声明了 write_key 索引。

    ensure_indexes 不应因 ix_memories_write_key 引用的 write_key 不存在而报错
    或尝试 CREATE INDEX；应跳过该索引。
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'pre_key.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT NOT NULL)"))
            metadata = _load_metadata()
            created = _ensure_indexes_on(connection, metadata)
        assert "memories.ix_memories_write_key" not in created
        with engine.connect() as connection:
            assert "ix_memories_write_key" not in _indexes(connection, "memories")
    finally:
        engine.dispose()


def test_已有列缺索引时补建且唯一约束生效(tmp_path):
    """memories 表已有 write_key 列但缺 ix_memories_write_key，ensure_indexes 补建唯一索引。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'has_col.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE memories (id INTEGER PRIMARY KEY, write_key VARCHAR(64))"))
            metadata = _load_metadata()
            created = _ensure_indexes_on(connection, metadata)
        assert "memories.ix_memories_write_key" in created
        with engine.connect() as connection:
            assert "ix_memories_write_key" in _indexes(connection, "memories")

        # 唯一约束真实生效：同一 write_key 不得重复插入。
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO memories (id, write_key) VALUES (1, 'same-key')"))
            with pytest.raises(IntegrityError):
                connection.execute(text("INSERT INTO memories (id, write_key) VALUES (2, 'same-key')"))
    finally:
        engine.dispose()


def test_后期补列后再次调用能补对应索引(tmp_path):
    """先无 write_key 列跳过；后期通过真实 DDL 补列后，再次调用能补 ix_memories_write_key。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'late_col.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT NOT NULL)"))
            metadata = _load_metadata()
            first = _ensure_indexes_on(connection, metadata)
            assert "memories.ix_memories_write_key" not in first
            # 后期补列（模拟 c4e6f8a0b2d4 真实加列）。
            connection.execute(text("ALTER TABLE memories ADD COLUMN write_key VARCHAR(64)"))
            second = _ensure_indexes_on(connection, metadata)
            assert "memories.ix_memories_write_key" in second
    finally:
        engine.dispose()


def test_重复执行不重复创建(tmp_path):
    """同一 ensure_indexes 两次调用，第二次 created 为空（幂等）。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'idempotent.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE memories (id INTEGER PRIMARY KEY, write_key VARCHAR(64))"))
            metadata = _load_metadata()
            first = _ensure_indexes_on(connection, metadata)
            second = _ensure_indexes_on(connection, metadata)
        assert "memories.ix_memories_write_key" in first
        assert second == []
        with engine.connect() as connection:
            assert "ix_memories_write_key" in _indexes(connection, "memories")
    finally:
        engine.dispose()


def test_列存在但真实DDL失败仍抛出(tmp_path):
    """列存在且索引尚未建，但唯一索引列已有重复值；CREATE UNIQUE INDEX 必被拒绝且异常透传。

    ensure_indexes 不包裹 try/except，真正的 DDL 失败自然抛出，不被静默吞掉。
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'ddl_fail.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE memories (id INTEGER PRIMARY KEY, write_key VARCHAR(64))"))
            connection.execute(text("INSERT INTO memories (id, write_key) VALUES (1, 'dup-key')"))
            connection.execute(text("INSERT INTO memories (id, write_key) VALUES (2, 'dup-key')"))
            metadata = _load_metadata()
            with pytest.raises((IntegrityError, OperationalError)):
                _ensure_indexes_on(connection, metadata)
    finally:
        engine.dispose()


def test_官方alembic_空库baseline升grade_head_写键列与唯一索引可用(tmp_path, monkeypatch):
    """从空库 baseline 完整重放到 head，最终 memories.write_key 列与 ix_memories_write_key 唯一索引真实可用。

    这也是旧链升级（baseline -> head）不再因 e1b2c3d4e5f6 前瞻 metadata 报 no such column 的正对照。
    """
    from alembic import command
    from alembic.config import Config
    from app.config import settings

    path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{path}")
    backend = Path(__file__).parents[1]
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{path}")
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            cols = _columns(connection, "memories")
            assert "write_key" in cols
            assert "ix_memories_write_key" in _indexes(connection, "memories")
        # 建立合法合成归属，使用ORM默认值补齐历史表的必填字段。
        from sqlalchemy.orm import Session
        from app.models.user import User
        from app.models.character import AICharacter
        from app.models.memory import Memory
        with Session(engine) as db:
            db.add(User(id=501, username="SYNTHETIC_INDEX_OWNER", nickname="SYNTHETIC_OWNER"))
            db.flush()
            db.add(AICharacter(id=502, user_id=501, name="SYNTHETIC_INDEX_CHARACTER"))
            db.flush()
            db.add(Memory(id=1, user_id=501, character_id=502, memory_type="EPISODIC",
                          content="SYNTHETIC_FIRST", write_key="k1"))
            db.commit()
            db.add(Memory(id=2, user_id=501, character_id=502, memory_type="EPISODIC",
                          content="SYNTHETIC_SECOND", write_key="k1"))
            with pytest.raises(IntegrityError, match="UNIQUE constraint failed: memories.write_key"):
                db.commit()
            db.rollback()
    finally:
        engine.dispose()
