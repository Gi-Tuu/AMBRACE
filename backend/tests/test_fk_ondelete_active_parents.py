# -*- coding: utf-8 -*-
"""P3-1：活跃业务父表（会话/群/朋友圈/对局）子外键 ondelete 落库 + 级联行为回归（2026-09-11）。

覆盖（方案 §3/§4.2）：
① create_all 建的新库：15 处子 FK 物理 on_delete = 目标值（CASCADE / SET NULL）；
② 临时库整链 upgrade head 后：同样成立（守迁移路径）；
③ PRAGMA foreign_keys=ON 下删群，成员/消息被级联删除（行为金标准）；
④ 重建涉及的表中，指向 users 父表的子 FK 仍是 NO ACTION（本轮明确不动 users）；
⑤ 另三组父表的行为金标准：删会话 → 5 子表级联归零、主动消息日志保留且 session_id 置
   NULL；删朋友圈 → 点赞/AI 赞/评论（含楼中楼 parent_id 级联）归零；删对局 → 玩家/事件/
   记忆归零。

注：15 处清单与方案 §3.1 一一对应；另断言模型层 ForeignKey.ondelete 已声明（单一事实源）。
"""
from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine

# (子表, 外键列, 目标 on_delete)——P3-1 本轮 15 处
ONDELETE_CASES = [
    ("chat_messages", "session_id", "CASCADE"),
    ("daily_summaries", "session_id", "CASCADE"),
    ("pending_permission_actions", "session_id", "CASCADE"),
    ("proactive_message_logs", "session_id", "SET NULL"),
    ("proactive_storyline_items", "session_id", "CASCADE"),
    ("scheduled_events", "session_id", "CASCADE"),
    ("chat_group_members", "group_id", "CASCADE"),
    ("chat_group_messages", "group_id", "CASCADE"),
    ("moment_likes", "moment_id", "CASCADE"),
    ("moment_ai_likes", "moment_id", "CASCADE"),
    ("moment_comments", "moment_id", "CASCADE"),
    ("moment_comments", "parent_id", "CASCADE"),
    ("game_players", "session_id", "CASCADE"),
    ("game_events", "session_id", "CASCADE"),
    ("game_memories", "session_id", "CASCADE"),
]

# 重建涉及的表中指向 users 的子 FK：本轮明确不动，必须仍是 NO ACTION
USERS_FK_UNTOUCHED = [
    ("pending_permission_actions", "user_id"),
    ("proactive_storyline_items", "user_id"),
    ("scheduled_events", "user_id"),
    ("moment_likes", "user_id"),
]


def _fk_rows(con: sqlite3.Connection, table: str):
    return con.execute(f"PRAGMA foreign_key_list({table})").fetchall()


def _physical_ondelete(con: sqlite3.Connection, table: str, col: str) -> str:
    hit = [r for r in _fk_rows(con, table) if r[3] == col]
    assert hit, f"{table}.{col} 无外键"
    return str(hit[0][6]).upper()


def _assert_all_cases(con: sqlite3.Connection) -> None:
    for table, col, want in ONDELETE_CASES:
        got = _physical_ondelete(con, table, col)
        assert got == want, f"{table}.{col} 物理 on_delete={got}，应为 {want}"
    for table, col in USERS_FK_UNTOUCHED:
        got = _physical_ondelete(con, table, col)
        assert got == "NO ACTION", f"{table}.{col}→users 应为 NO ACTION，实际 {got}"


def _assert_users_fks_match_orm(con: sqlite3.Connection) -> None:
    """全库扫：所有指向 users 的物理 FK 必须与 ORM 声明一致（本轮未动 users 父表）。"""
    import app.models  # noqa: F401
    from app.models._all import Base

    checked = 0
    for tname, tbl in Base.metadata.tables.items():
        for fk in tbl.foreign_keys:
            if not fk.target_fullname.startswith("users."):
                continue
            want = (fk.ondelete or "NO ACTION").upper()
            got = _physical_ondelete(con, tname, fk.parent.name)
            assert got == want, f"{tname}.{fk.parent.name}→users 物理 {got} ≠ ORM {want}"
            checked += 1
    assert checked >= 37, f"users 子 FK 扫描数异常（{checked}），用例前提不成立"


def test_ondelete_declared_in_orm():
    """模型层断言：15 处 ForeignKey.ondelete 已声明（单一事实源）。"""
    import app.models  # noqa: F401
    from app.models._all import Base

    for table, col, want in ONDELETE_CASES:
        fk = next(
            f for f in Base.metadata.tables[table].foreign_keys
            if f.parent.name == col
        )
        assert (fk.ondelete or "").upper() == want, f"{table}.{col} ondelete 应为 {want}"


def test_physical_ondelete_after_create_all(tmp_path):
    """① 新库 create_all：15 处物理 on_delete 即目标值，users 相关未被改动。"""
    import app.models  # noqa: F401
    from app.models._all import Base

    db = tmp_path / "fk_create_all.db"
    eng = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        Base.metadata.create_all(eng)
    finally:
        eng.dispose()

    con = sqlite3.connect(str(db))
    try:
        _assert_all_cases(con)
        _assert_users_fks_match_orm(con)
    finally:
        con.close()


def test_physical_ondelete_after_migrate(monkeypatch, tmp_path):
    """② 临时库整链 upgrade head 后：15 处物理 on_delete 落库，users 相关仍 NO ACTION。"""
    from alembic import command
    from app.config import settings
    from app.db.migrate import _alembic_config

    db = tmp_path / "fk_migrate.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db.as_posix()}")
    command.upgrade(_alembic_config(), "head")

    con = sqlite3.connect(str(db))
    try:
        ver = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert ver == "f2b3c4d5e6f7", f"迁移链 head 应为 f2b3c4d5e6f7，实际 {ver}"
        _assert_all_cases(con)
        _assert_users_fks_match_orm(con)
    finally:
        con.close()


def test_group_cascade_behavior_on_create_all(tmp_path):
    """③ PRAGMA FK=ON 下删群 → 成员/消息被级联删除（引擎按声明的 on_delete 执行）。

    技巧：先 FK=OFF 插入「最小子行」（不必造 users/ai_characters 整条父链），再切 ON 删父行。
    """
    import app.models  # noqa: F401
    from app.models._all import Base

    db = tmp_path / "fk_behavior.db"
    eng = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        Base.metadata.create_all(eng)
    finally:
        eng.dispose()

    con = sqlite3.connect(str(db))
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("INSERT INTO chat_groups (id,user_id,name) VALUES (1,999,'g')")
        # 非 Optional 的列即使有 Python default，裸 sqlite INSERT 仍要显式给值
        con.execute("INSERT INTO chat_group_members (id,group_id,character_id,muted) VALUES (1,1,888,0)")
        con.execute(
            "INSERT INTO chat_group_messages "
            "(id,group_id,sender_type,content,notify_user,msg_type) "
            "VALUES (1,1,'user','x',0,'normal')"
        )
        con.commit()

        con.execute("PRAGMA foreign_keys=ON")
        con.execute("DELETE FROM chat_groups WHERE id=1")
        con.commit()

        assert con.execute("SELECT COUNT(*) FROM chat_group_members WHERE group_id=1").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM chat_group_messages WHERE group_id=1").fetchone()[0] == 0
    finally:
        con.close()


def _dummy_value(ctype: str):
    """按 SQLite 物理列类型给一个可插入的占位值（仅用于构造最小子行）。"""
    t = (ctype or "").upper()
    if "INT" in t or "BOOL" in t:
        return 0
    if any(k in t for k in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL")):
        return 0.0
    if "DATE" in t or "TIME" in t:
        return "2026-01-01 00:00:00"
    return "x"


def _insert_min_row(con: sqlite3.Connection, table: str, **overrides) -> None:
    """向 table 插一行最小行：overrides 显式给外键/必需列，其余物理 NOT NULL 且无
    ``DEFAULT`` 的列自动补类型占位值。

    为什么不能只给外键：裸 sqlite INSERT 不跑 ORM 的 Python 端 default，凡物理 NOT NULL
    又无 DDL ``DEFAULT`` 的列都必须显式给值（此前踩过 ``chat_group_members.muted``、
    ``chat_group_messages.notify_user/msg_type``）；``INTEGER PRIMARY KEY`` 是 rowid
    别名，省略即自增。
    """
    # PRAGMA table_info 行结构：(cid, name, type, notnull, dflt_value, pk)
    cols, vals = [], []
    for _cid, name, ctype, notnull, dflt, pk in con.execute(f"PRAGMA table_info({table})").fetchall():
        if name in overrides:
            cols.append(name)
            vals.append(overrides[name])
            continue
        if pk and "INT" in (ctype or "").upper():
            continue  # rowid 别名，自增
        if notnull and dflt is None:
            cols.append(name)
            vals.append(_dummy_value(ctype))
    if not cols:
        con.execute(f"INSERT INTO {table} DEFAULT VALUES")
        return
    placeholders = ",".join("?" * len(cols))
    con.execute(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})", vals
    )


def _make_min_child_fk_db(tmp_path, filename: str):
    """create_all 建库并返回 ``(db_path, connection)``，FK 由调用方按需开关。"""
    import app.models  # noqa: F401
    from app.models._all import Base

    db = tmp_path / filename
    eng = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        Base.metadata.create_all(eng)
    finally:
        eng.dispose()
    return db, sqlite3.connect(str(db))


def test_session_cascade_behavior_on_create_all(tmp_path):
    """⑤-1 删会话 → 5 子表级联归零；proactive_message_logs 按 SET NULL 保留并置空 session_id。

    技巧同删群：FK=OFF 插最小子行（不必造 users/ai_characters 整条父链），再切 ON 删父行。
    """
    db, con = _make_min_child_fk_db(tmp_path, "fk_behavior_session.db")
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        _insert_min_row(con, "chat_sessions")  # id 自增 = 1
        _insert_min_row(con, "chat_messages", session_id=1, sender_type="user", content="x")
        _insert_min_row(con, "daily_summaries", session_id=1, summary_date="2026-01-01", summary_text="x")
        _insert_min_row(con, "pending_permission_actions", session_id=1, scope="s", action="{}")
        _insert_min_row(
            con, "proactive_storyline_items",
            session_id=1, group_id="g", content="x", send_at="2026-01-01 00:00:00",
        )
        _insert_min_row(con, "scheduled_events", session_id=1, trigger_at="2026-01-01 00:00:00")
        _insert_min_row(con, "proactive_message_logs", session_id=1, message_type="proactive", content="x")
        con.commit()

        con.execute("PRAGMA foreign_keys=ON")
        con.execute("DELETE FROM chat_sessions WHERE id=1")
        con.commit()

        for child in (
            "chat_messages", "daily_summaries", "pending_permission_actions",
            "proactive_storyline_items", "scheduled_events",
        ):
            assert con.execute(f"SELECT COUNT(*) FROM {child} WHERE session_id=1").fetchone()[0] == 0, child
        # SET NULL 语义单独断言：日志行必须保留（不是被删），且 session_id 被置 NULL
        total, non_null = con.execute(
            "SELECT COUNT(*), COUNT(session_id) FROM proactive_message_logs"
        ).fetchone()
        assert total == 1, "proactive_message_logs 应保留（SET NULL），不应被级联删除"
        assert non_null == 0, "proactive_message_logs.session_id 应被置 NULL"
    finally:
        con.close()
    assert db.exists()


def test_moment_cascade_behavior_on_create_all(tmp_path):
    """⑤-2 删朋友圈 → 点赞/AI 赞/评论归零；楼中楼 parent_id 级联删子评论。"""
    db, con = _make_min_child_fk_db(tmp_path, "fk_behavior_moment.db")
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        _insert_min_row(con, "ai_moments", content="m")  # id 自增 = 1
        _insert_min_row(con, "moment_likes", moment_id=1, user_id=1)
        _insert_min_row(con, "moment_ai_likes", moment_id=1, character_id=1)
        # 评论 1（根，id=1）、评论 2（楼中楼，parent_id=1）、评论 3（另一条根）
        _insert_min_row(
            con, "moment_comments",
            moment_id=1, sender_type="ai", sender_id=1, sender_name="n", content="root",
        )
        _insert_min_row(
            con, "moment_comments", moment_id=1, parent_id=1,
            sender_type="user", sender_id=1, sender_name="n", content="child",
        )
        _insert_min_row(
            con, "moment_comments",
            moment_id=1, sender_type="ai", sender_id=1, sender_name="n", content="root2",
        )
        con.commit()

        con.execute("PRAGMA foreign_keys=ON")
        # 先单独验证 parent_id 自引用级联：删根评论 1 → 楼中楼 2 消失，另一根 3 保留
        con.execute("DELETE FROM moment_comments WHERE id=1")
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM moment_comments WHERE id=2").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM moment_comments WHERE id=3").fetchone()[0] == 1
        # 再删朋友圈父行 → 点赞/AI 赞/剩余评论全部归零
        con.execute("DELETE FROM ai_moments WHERE id=1")
        con.commit()
        for child in ("moment_likes", "moment_ai_likes", "moment_comments"):
            assert con.execute(f"SELECT COUNT(*) FROM {child}").fetchone()[0] == 0, child
    finally:
        con.close()
    assert db.exists()


def test_game_cascade_behavior_on_create_all(tmp_path):
    """⑤-3 删对局 → 玩家/事件/记忆归零。"""
    db, con = _make_min_child_fk_db(tmp_path, "fk_behavior_game.db")
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        _insert_min_row(
            con, "game_sessions", user_id=1, game_type="undercover", player_mode="single"
        )  # id 自增 = 1
        _insert_min_row(con, "game_players", session_id=1, player_type="user")
        _insert_min_row(con, "game_events", session_id=1, event_type="deal")
        _insert_min_row(con, "game_memories", session_id=1, character_id=1)
        con.commit()

        con.execute("PRAGMA foreign_keys=ON")
        con.execute("DELETE FROM game_sessions WHERE id=1")
        con.commit()

        for child in ("game_players", "game_events", "game_memories"):
            assert con.execute(f"SELECT COUNT(*) FROM {child} WHERE session_id=1").fetchone()[0] == 0, child
    finally:
        con.close()
    assert db.exists()
