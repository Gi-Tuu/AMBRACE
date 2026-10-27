"""插件独立表的角色引用水位：合成数据，真实历史迁移。"""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest
from sqlalchemy import create_engine
from app.db.character_id_references import scan_character_id_watermark

BACKEND = Path(__file__).resolve().parents[1]


def scan(path):
    engine = create_engine("sqlite:///" + path.as_posix())
    try:
        with engine.connect() as conn:
            return scan_character_id_watermark(conn)
    finally:
        engine.dispose()


@pytest.mark.parametrize("table", ["wechat_ilink_bindings", "wechat_ilink_messages"])
def test_plugin_reference_preserves_watermark_without_changing_row(tmp_path, table):
    path = tmp_path / "plugin.db"
    with sqlite3.connect(path) as db:
        db.execute("create table ai_characters(id integer primary key)")
        db.execute("insert into ai_characters values(5)")
        db.execute(f"create table {table}(id integer primary key, character_id, marker text)")
        db.execute(f"insert into {table} values(1, 9999, 'synthetic preserved')")
    db.close()
    before = path.read_bytes()
    assert scan(path)["high"] == 9999
    assert path.read_bytes() == before


@pytest.mark.parametrize("bad", [-1, "not-an-id"])
def test_registered_plugin_still_rejects_invalid_ids_before_write(tmp_path, bad):
    path = tmp_path / "invalid.db"
    with sqlite3.connect(path) as db:
        db.execute("create table ai_characters(id integer primary key)")
        db.execute("insert into ai_characters values(5)")
        db.execute("create table wechat_ilink_bindings(id integer primary key, character_id)")
        db.execute("insert into wechat_ilink_bindings values(1, ?)", (bad,))
    db.close()
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        scan(path)
    assert path.read_bytes() == before


def test_unknown_plugin_is_not_implicitly_trusted(tmp_path):
    path = tmp_path / "unknown.db"
    with sqlite3.connect(path) as db:
        db.execute("create table ai_characters(id integer primary key)")
        db.execute("insert into ai_characters values(5)")
        db.execute("create table synthetic_other_plugin(id integer primary key, character_id integer)")
        db.execute("insert into synthetic_other_plugin values(1, 99)")
    db.close()
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="unknown character_id semantics"):
        scan(path)
    assert path.read_bytes() == before


def upgrade(path, revision):
    env = os.environ.copy()
    env["DATABASE_URL"] = "sqlite+aiosqlite:///" + path.as_posix()
    env["CHROMA_PERSIST_DIR"] = str(path.parent / "synthetic_vector")
    process = subprocess.run([sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), "upgrade", revision], cwd=BACKEND, env=env, capture_output=True)
    assert process.returncode == 0, process.stderr.decode("utf-8", "replace")[-2500:]


def test_real_history_upgrade_preserves_plugin_rows_and_reserves_deleted_ids(tmp_path):
    path = tmp_path / "history.db"
    upgrade(path, "e2f4a6b8c0d2")
    with sqlite3.connect(path) as db:
        db.execute("insert into users(id,username,nickname) values(1,'synthetic-plugin','Synthetic')")
        db.execute("insert into ai_characters(id,user_id,name,is_partner,is_active,cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) values(5,1,'Synthetic',0,1,1,1,0)")
        db.execute("create table wechat_ilink_bindings(id integer primary key, user_id integer, character_id integer, enabled integer, bot_token_enc text)")
        db.execute("create table wechat_ilink_messages(id integer primary key, binding_id integer, character_id integer, content text)")
        db.execute("insert into wechat_ilink_bindings values(1,1,9001,0,'synthetic ciphertext')")
        db.execute("insert into wechat_ilink_messages values(1,1,9002,'synthetic payload')")
    db.close()
    upgrade(path, "d6f8a0b2c4e6")
    with sqlite3.connect(path) as db:
        assert db.execute("select * from wechat_ilink_bindings").fetchall() == [(1,1,9001,0,'synthetic ciphertext')]
        assert db.execute("select * from wechat_ilink_messages").fetchall() == [(1,1,9002,'synthetic payload')]
        assert db.execute("select seq from sqlite_sequence where name='ai_characters'").fetchone()[0] >= 9002
        assert db.execute("pragma foreign_key_check").fetchall() == []
        assert db.execute("pragma integrity_check").fetchone()[0] == 'ok'
        assert db.execute("select version_num from alembic_version").fetchone()[0] == 'd6f8a0b2c4e6'
    db.close()
