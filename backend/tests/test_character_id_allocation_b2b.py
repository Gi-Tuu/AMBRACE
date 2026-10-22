# -*- coding: utf-8 -*-
"""B2b：模型 create_all 的 SQLite AUTOINCREMENT 与分配不复用。"""
import sqlite3

from sqlalchemy import create_engine


def _make_db(tmp_path):
    import app.models._all  # noqa: F401  # 填充 Base.metadata
    from app.models.base import Base

    db = tmp_path / "alloc.db"
    engine = create_engine("sqlite:///" + db.as_posix())
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    return db


def _con(db):
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _seed(con):
    con.executemany(
        "INSERT INTO users(id,username,nickname) VALUES(?,?,?)",
        [(1, "b2b_a", "A"), (2, "b2b_b", "B")],
    )
    con.executemany(
        "INSERT INTO ai_characters(id,user_id,name,is_partner,is_active,"
        "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) "
        "VALUES(?,?,?,0,1,1,1,0)",
        [(1, 1, "C1"), (2, 1, "C2"), (3, 2, "C3")],
    )


def _insert(con, user_id, name):
    cur = con.execute(
        "INSERT INTO ai_characters(user_id,name,is_partner,is_active,"
        "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) "
        "VALUES(?,?,0,1,1,1,0)",
        (user_id, name),
    )
    return int(cur.lastrowid)


def test_create_all_ddl_has_sqlite_autoincrement(tmp_path):
    db = _make_db(tmp_path)
    con = _con(db)
    try:
        ddl = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
        ).fetchone()[0]
        assert "AUTOINCREMENT" in ddl.upper()
        # 其它表仍保持旧 DDL（本约束只落在 ai_characters）
        users = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()[0]
        assert "AUTOINCREMENT" not in users.upper()
    finally:
        con.close()


def test_delete_max_then_same_and_cross_account_no_reuse(tmp_path):
    db = _make_db(tmp_path)
    con = _con(db)
    try:
        _seed(con)
        con.execute("DELETE FROM ai_characters WHERE id=3")
        assert _insert(con, 1, "same-account") == 4
        assert _insert(con, 2, "cross-account") == 5
        con.commit()
    finally:
        con.close()

    # 重开连接（模拟进程重启）后仍不复用
    con = _con(db)
    try:
        assert _insert(con, 1, "after-reopen") == 6
        seq = con.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='ai_characters'"
        ).fetchone()[0]
        assert seq == 6
        con.commit()
    finally:
        con.close()


def test_delete_all_then_insert_still_above_old_max(tmp_path):
    db = _make_db(tmp_path)
    con = _con(db)
    try:
        _seed(con)
        con.execute("DELETE FROM ai_characters")
        con.commit()
    finally:
        con.close()

    con = _con(db)
    try:
        assert _insert(con, 1, "after-delete-all") == 4
        assert _insert(con, 2, "after-delete-all-2") == 5
        con.commit()
    finally:
        con.close()


def test_sequence_is_above_surviving_orphan_ref(tmp_path):
    db = _make_db(tmp_path)
    con = _con(db)
    try:
        _seed(con)
        con.execute(
            "INSERT INTO lorebook_entries(user_id,character_id,title,content,keywords,"
            "exclude_keywords,active,is_regex,probability,inclusion_group,sticky_rounds,"
            "cooldown_rounds) VALUES(1,999,'orphan','x','[]','[]',1,0,100,'',0,0)"
        )
        con.commit()
    finally:
        con.close()

    # create_all 不会自动把孤儿引用纳入 sequence；迁移/启动修正负责抬高。
    from app.db import migrate

    engine = create_engine("sqlite:///" + db.as_posix())
    try:
        with engine.begin() as conn:
            fixed = migrate.repair_ai_character_sequence(conn)
    finally:
        engine.dispose()
    assert fixed["after"] == 999
    con = _con(db)
    try:
        assert _insert(con, 1, "above-orphan") == 1000
        con.commit()
    finally:
        con.close()
