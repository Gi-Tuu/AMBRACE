# -*- coding: utf-8 -*-
"""统一候选历史迁移与恢复演练（B 件，2026-10-24 派单 §B）。

真实 Alembic 机制（子进程 CLI，DATABASE_URL 指向本测试独占临时库）：
旧正式头 e2f4a6b8c0d2 建历史库（**禁止 create_all/_dbclone 冒充**）→ 合成数据 →
一致备份 → 升级候选单头 d6f8a0b2c4e6 → 幂等重放 → C9 约束行为（FK/级联/SET NULL/
唯一/AUTOINCREMENT 水位）→ 从备份恢复独立库校验旧 schema 与数据 → 损坏输入拒绝。

合成数据全部为设备质检场景；不连生产库、不扫真实数据；不复用其它测试的 basetemp。
连接按创建逆序关闭；不宣称 DDL 迁移天然跨步原子回滚（中断场景只验证「失败被拒绝」）。
所有 SQL 语句均为固定字面量，数据值一律参数绑定（?）。
"""
import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
OLD_HEAD = "e2f4a6b8c0d2"
CAND_HEAD = "d6f8a0b2c4e6"


@pytest.fixture(scope="module")
def rehearsal(tmp_path_factory):
    root = tmp_path_factory.mktemp("candidate_rehearsal")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(root / "rehearsal.db").replace(os.sep, "/")
    env["INSTANCE_LOCK_PORT"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    # REWORK1：内核证据生命周期用例的 ORM 入口（指向同一演练库的独立异步引擎）
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///" + str(root / "rehearsal.db").replace(os.sep, "/"),
        poolclass=NullPool)

    # 对齐生产 engine.py：每连接 PRAGMA foreign_keys=ON（真实 FK 行为必须与生产一致）
    from sqlalchemy import event as _sa_event

    @_sa_event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    def _dispose():
        import asyncio as _a
        _a.run(engine.dispose())

    return {"root": root, "db": root / "rehearsal.db",
            "backup": root / "backup.db", "restored": root / "restored.db",
            "post_upgrade": root / "post_upgrade.db", "env": env,
            "factory": factory, "dispose": _dispose}


@pytest.fixture(scope="module", autouse=True)
def _dispose_engine_after_module(rehearsal):
    yield
    rehearsal["dispose"]()


def _alembic(rehearsal, *args, expect_ok=True):
    """真实 alembic CLI 子进程（env.py 在子进程 import 时解析 DATABASE_URL ⇒ 完全隔离）。"""
    r = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND), env=rehearsal["env"], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=300, check=False,
    )
    if expect_ok and r.returncode != 0:
        raise AssertionError(
            "alembic %s 失败:\n%s\n%s" % (" ".join(args), r.stdout, r.stderr))
    return r


def _connect(path):
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# REWORK2：守恒/恢复比较涉及的六表（业务数据面）
_SNAPSHOT_TABLES = ("users", "ai_characters", "chat_sessions", "chat_messages",
                     "prospective_intents", "memories")

# REWORK2：候选迁移**明示**新增的列（只允许这些列在升级后为新值/新列）
_ALLOWED_NEW_COLUMNS = {"memories": ("write_key",)}


def _dump_rows(con, table):
    """整表**全列**快照（``SELECT *``），按主键排序；返回 list[dict]。

    REWORK2：不再投影部分列——「全行一致」必须含 ``updated_at`` 等全部列。
    """
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    if not cols:
        return []
    order = "id" if "id" in cols else cols[0]
    rows = con.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
    return [dict(zip(cols, r)) for r in rows]


def _schema_fingerprint(con):
    """结构等价指纹：sqlite_master（表/索引 DDL）+ 各表 FK 声明 + sqlite_sequence 水位。"""
    master = con.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    fks = {}
    for t in tables:
        fks[t] = con.execute(f"PRAGMA foreign_key_list({t})").fetchall()
    seq = con.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name").fetchall()
    return {"master": master, "fks": fks, "sequence": seq}


def _seed_old_db(db):
    """在旧头 schema 上写合成数据（设备质检场景），返回行数基线。"""
    con = _connect(db)
    try:
        con.executescript(
            "INSERT INTO users (id, username, nickname, password_hash, is_admin)"
            " VALUES (101, 'lab_a', 'Lab A', 'x', 1),"
            "        (102, 'lab_b', 'Lab B', 'x', 0);"
            "INSERT INTO ai_characters (id, user_id, name, is_partner,"
            " talkativeness_locked, is_active, cognitive_loop_enabled, memory_v2_enabled)"
            " VALUES (201, 101, 'QA Agent A', 0, 0, 1, 1, 1),"
            "        (203, 102, 'QA Agent C', 0, 0, 1, 1, 1);"
            "INSERT INTO chat_sessions (id, user_id, character_id, is_active)"
            " VALUES (301, 101, 201, 1), (302, 102, 203, 1);"
            "INSERT INTO chat_messages (id, session_id, sender_type, content, created_at)"
            " VALUES (5001, 301, 'user', '用户到达质检台后扫描设备A', '2026-10-20 08:00:00'),"
            "        (5002, 301, 'user', '我到了质检台', '2026-10-20 08:30:00'),"
            "        (5003, 302, 'user', '用户到达车间后核对夹具T', '2026-10-20 09:00:00');"
            "INSERT INTO prospective_intents"
            " (user_id, character_id, content, kind, cue_terms_json, due_end, status,"
            "  source_message_id, chat_session_id, created_at, updated_at)"
            " VALUES (101, 201, '用户到达质检台后扫描设备A', 'promise', '{\"terms\":[\"设备\"]}',"
            "         '2026-10-20 23:59:00', 'pending', 5001, 301,"
            "         '2026-10-20 08:00:05', '2026-10-20 08:00:05');"
            "INSERT INTO memories (user_id, character_id, memory_type, content, scope,"
            " importance, is_archived, is_pinned, review_count, is_locked, ai_rated,"
            " is_core, confirmation_count, contradiction_count, version,"
            " created_at, updated_at)"
            " VALUES (101, 201, 'user_info', 'Lab A 负责设备A的定期质检', 'character',"
            "         3, 0, 0, 0, 0, 0, 0, 0, 0, 1,"
            "         '2026-10-20 08:00:10', '2026-10-20 08:00:10'),"
            "        (101, 201, 'event', 'Lab A 完成了夹具T的校准', 'character',"
            "         4, 0, 0, 0, 0, 0, 0, 0, 0, 1,"
            "         '2026-10-20 09:00:10', '2026-10-20 09:00:10');"
        )
        con.commit()
        baseline = {
            "users": con.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "ai_characters": con.execute("SELECT COUNT(*) FROM ai_characters").fetchone()[0],
            "chat_sessions": con.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0],
            "chat_messages": con.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0],
            "prospective_intents":
                con.execute("SELECT COUNT(*) FROM prospective_intents").fetchone()[0],
            "memories": con.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        }
    finally:
        con.close()
    return baseline


@pytest.fixture(scope="module")
def upgraded(rehearsal):
    """链式主步骤：旧头建库 → 播种 → 一致备份 → 升级候选头 → 幂等重放 → **升级后快照**。

    REWORK2：`post_upgrade.db` 在**任何后续测试写入之前**拍下（升级刚完成时）。
    「升级守恒」用它与迁移前备份（`backup.db`）逐行比对——两者构成同一迁移前后关系，
    与后续用例对演练库的合法写入无关 ⇒ **不依赖用例收集顺序**。
    """
    _alembic(rehearsal, "upgrade", OLD_HEAD)
    baseline = _seed_old_db(rehearsal["db"])
    src = _connect(rehearsal["db"])
    dst = sqlite3.connect(str(rehearsal["backup"]))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    _alembic(rehearsal, "upgrade", CAND_HEAD)  # 成败由 _alembic returncode 门禁
    _alembic(rehearsal, "upgrade", CAND_HEAD)  # 幂等重放
    con = _connect(rehearsal["db"])
    try:
        ver = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()
    assert ver == CAND_HEAD
    # REWORK2：升级后快照（守恒基准）——此后演练库再被其它用例写入也不影响守恒判定
    src = _connect(rehearsal["db"])
    dst = sqlite3.connect(str(rehearsal["post_upgrade"]))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return {"baseline": baseline}


def test_candidate_head_reached_with_data_conserved(rehearsal, upgraded):
    """链尾态：版本=候选头；候选表/列由迁移创建；旧数据行数守恒。"""
    con = _connect(rehearsal["db"])
    try:
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == CAND_HEAD
        tables = _tables(con)
        assert "business_plan_items" in tables and "plan_completion_evidence" in tables
        cols = {r[1] for r in con.execute("PRAGMA table_info(memories)")}
        assert "write_key" in cols
        idx = {r[1] for r in con.execute("PRAGMA index_list(memories)")}
        assert "ix_memories_write_key" in idx
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == upgraded["baseline"]["users"]
        assert con.execute("SELECT COUNT(*) FROM ai_characters").fetchone()[0] == upgraded["baseline"]["ai_characters"]
        assert con.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0] == upgraded["baseline"]["chat_sessions"]
        assert con.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == upgraded["baseline"]["chat_messages"]
        assert con.execute("SELECT COUNT(*) FROM prospective_intents").fetchone()[0] == upgraded["baseline"]["prospective_intents"]
        assert con.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == upgraded["baseline"]["memories"]
    finally:
        con.close()


def test_character_id_sequence_watermark(rehearsal, upgraded):
    """B2 重建后 AUTOINCREMENT：新 id > 历史 max；**删除历史最高角色后**下一 id 仍高于
    已删除水位（不复用）——AUTOINCREMENT 语义区别于裸 MAX+1 的核心。"""
    con = _connect(rehearsal["db"])
    try:
        max_id = con.execute("SELECT MAX(id) FROM ai_characters").fetchone()[0]
        con.execute(
            "INSERT INTO ai_characters (user_id, name, is_partner, talkativeness_locked,"
            " is_active, cognitive_loop_enabled, memory_v2_enabled)"
            " VALUES (?, ?, 0, 0, 1, 1, 1)",
            (101, "QA Agent New"))
        new_id = con.execute("SELECT MAX(id) FROM ai_characters").fetchone()[0]
        assert new_id > max_id
        # 删除当前最高角色（含其 FK 子行的安全清理：先删会级联的引用者）
        con.execute("DELETE FROM chat_sessions WHERE character_id = ?", (new_id,))
        con.execute("DELETE FROM ai_characters WHERE id = ?", (new_id,))
        gone = con.execute("SELECT COUNT(*) FROM ai_characters WHERE id = ?", (new_id,)).fetchone()[0]
        assert gone == 0
        con.execute(
            "INSERT INTO ai_characters (user_id, name, is_partner, talkativeness_locked,"
            " is_active, cognitive_loop_enabled, memory_v2_enabled)"
            " VALUES (?, ?, 0, 0, 1, 1, 1)",
            (101, "QA Agent After Delete"))
        after_id = con.execute("SELECT MAX(id) FROM ai_characters").fetchone()[0]
        assert after_id > new_id, "删除历史最高角色后新 id 仍须高于已删除水位（不复用）"
        seq = con.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = ?", ("ai_characters",)).fetchone()
        assert seq is not None and seq[0] >= after_id
        con.rollback()
    finally:
        con.close()


def test_c9_fk_and_uniqueness(rehearsal, upgraded):
    con = _connect(rehearsal["db"])
    try:
        con.execute(
            "INSERT INTO business_plan_items"
            " (user_id, character_id, chat_session_id, source_message_id,"
            "  source_event_at, source_hash, ordinal, target_text, item_key, cancelled)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (101, 201, 301, 5001, "2026-10-20 08:00:00", "h1", 1,
             "用户到达质检台后扫描设备A", "item-key-1", 0))
        item_id = con.execute(
            "SELECT id FROM business_plan_items WHERE item_key = ?",
            ("item-key-1",)).fetchone()[0]
        con.execute(
            "INSERT INTO plan_completion_evidence"
            " (user_id, character_id, chat_session_id, source_message_id,"
            "  source_event_at, source_hash, ordinal, target_text,"
            "  evidence_key, claim_text, item_id, binding_kind)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (101, 201, 301, 5002, "2026-10-20 08:30:00", "e1", 1, "到达质检台",
             "ev-key-1", "我到了质检台", item_id, "resolved"))
        con.commit()
        # 唯一键真实执行（同 item_key 二次插入被拒）
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO business_plan_items"
                " (user_id, character_id, chat_session_id, source_message_id,"
                "  source_event_at, source_hash, ordinal, target_text, item_key, cancelled)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (101, 201, 301, None, "2026-10-20 08:00:00", "h1", 1, "x", "item-key-1", 0))
        # FK 真实执行：孤儿 plan（不存在 user 999）被拒
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO business_plan_items"
                " (user_id, character_id, chat_session_id, source_message_id,"
                "  source_event_at, source_hash, ordinal, target_text, item_key, cancelled)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (999, 201, 301, None, "2026-10-20 08:00:00", "h2", 1, "y", "item-key-2", 0))
        con.rollback()
    finally:
        con.close()


def test_c9_delete_set_null_and_cascade(rehearsal, upgraded):
    """删源消息 ⇒ evidence.source_message_id SET NULL（证据保留失效）；
    删 user ⇒ 其 plan/evidence 级联清空。"""
    con = _connect(rehearsal["db"])
    try:
        con.execute(
            "INSERT INTO business_plan_items"
            " (user_id, character_id, chat_session_id, source_message_id,"
            "  source_event_at, source_hash, ordinal, target_text, item_key, cancelled)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (102, 203, 302, 5003, "2026-10-20 09:00:00", "h3", 1,
             "用户到达车间后核对夹具T", "item-key-3", 0))
        con.execute(
            "INSERT INTO plan_completion_evidence"
            " (user_id, character_id, chat_session_id, source_message_id,"
            "  source_event_at, source_hash, ordinal, target_text,"
            "  evidence_key, claim_text, item_id, binding_kind)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (102, 203, 302, 5003, "2026-10-20 09:05:00", "e3", 1, "到达车间",
             "ev-key-3", "我到了车间", None, "unresolved"))
        con.execute("DELETE FROM chat_messages WHERE id = ?", (5003,))
        ev = con.execute(
            "SELECT source_message_id FROM plan_completion_evidence"
            " WHERE evidence_key = ?", ("ev-key-3",)).fetchone()
        assert ev is not None and ev[0] is None, "源消息删除后证据保留且外键 SET NULL"
        # 级联：C9 表对 chat_sessions 为 ondelete CASCADE——删 session 302 ⇒ 其名下
        # plan/evidence 全清；user 102 本体与其它数据不受牵连。
        con.execute("DELETE FROM chat_sessions WHERE id = ?", (302,))
        assert con.execute(
            "SELECT COUNT(*) FROM business_plan_items WHERE user_id = 102").fetchone()[0] == 0, \
            "session 删除应级联清掉挂其上的 plan items"
        assert con.execute(
            "SELECT COUNT(*) FROM plan_completion_evidence WHERE user_id = 102").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM users WHERE id = 102").fetchone()[0] == 1, \
            "级联只清 C9 子表，不得牵连 user 本体"
        # 旧表 FK（NO ACTION）在升级后依旧真实执行：user 101 名下仍有旧依赖行 ⇒ 删除被拒
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM users WHERE id = ?", (101,))
        con.commit()
    finally:
        con.close()


def test_fk_enforced_on_seeded_rows(rehearsal, upgraded):
    """升级后既有数据仍受 FK 约束保护（升级过程不禁 FK）。"""
    con = _connect(rehearsal["db"])
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO chat_messages (session_id, sender_type, content, created_at)"
                " VALUES (?, ?, ?, ?)", (99999, "user", "孤儿", "2026-10-20 10:00:00"))
        con.rollback()
    finally:
        con.close()


def test_restore_backup_to_independent_db(rehearsal, upgraded):
    """从迁移前一致备份恢复到独立临时库：**全行**逐字一致 + **结构等价**。

    REWORK2（原实现只 SELECT 若干列、DDL/FK/水位只验局部存在）：
    - 恢复库与备份同为**迁移前时间点** ⇒ 六表 ``SELECT *`` **全部列**（含 ``updated_at``）
      逐行一致，不再投影部分列；
    - 结构对照：``sqlite_master``（表/索引 DDL）、**各表** FK 声明、``sqlite_sequence``
      各水位——前后精确对照；
    - 加 ``integrity_check`` / ``foreign_key_check``。
    """
    src = _connect(rehearsal["backup"])
    dst = sqlite3.connect(str(rehearsal["restored"]))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    con = _connect(rehearsal["restored"])
    src_con = _connect(rehearsal["backup"])  # 恢复库的基准 = 迁移前备份（非演练库现态）
    try:
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == OLD_HEAD
        tables = _tables(con)
        assert "business_plan_items" not in tables
        assert "plan_completion_evidence" not in tables
        cols = {r[1] for r in con.execute("PRAGMA table_info(memories)")}
        assert "write_key" not in cols, "恢复库应保持旧 schema（无候选列）"
        # 全行比较：SELECT * 全部列，按主键排序逐行比对（同一时间点 ⇒ updated_at 也应逐字一致）
        for table in _SNAPSHOT_TABLES:
            assert _dump_rows(con, table) == _dump_rows(src_con, table), (
                f"{table} 恢复库与迁移前备份应**全行**逐字一致（含 updated_at 等全部列）")
        # 结构等价：DDL/索引 + 各表 FK 声明 + sequence 水位
        assert _schema_fingerprint(con) == _schema_fingerprint(src_con), (
            "恢复库与备份应结构等价（sqlite_master DDL/索引 + 各表 FK 声明 + sqlite_sequence 水位）")
        idx = {r[1] for r in con.execute("PRAGMA index_list(memories)")}
        assert "ix_memories_write_key" not in idx
        # 恢复库旧 FK 声明：校验有 FK 的代表性表（chat_messages→chat_sessions）
        fks = con.execute("PRAGMA foreign_key_list(chat_messages)").fetchall()
        assert fks, "恢复库旧 FK 声明应存在（chat_messages→chat_sessions 等）"
        seq = con.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = ?", ("ai_characters",)).fetchone()
        assert seq is not None, "恢复库 sqlite_sequence 应保留旧水位"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == [], "恢复库 FK 校验应零违例"
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        src_con.close()
        con.close()


def test_upgrade_conserves_all_old_columns(rehearsal, upgraded):
    """升级守恒：六表**全部旧列**逐行一致（只允许候选迁移明示新增的列）。

    REWORK2：基准取「迁移前备份」与「升级刚完成时的快照」——同一迁移前后关系，
    **不拿已经被别的测试改变的库解释数据差异**，也**不依赖用例收集顺序**。
    """
    old_con = _connect(rehearsal["backup"])
    new_con = _connect(rehearsal["post_upgrade"])
    try:
        for table in _SNAPSHOT_TABLES:
            old_by_pk = {r["id"]: r for r in _dump_rows(old_con, table)}
            new_by_pk = {r["id"]: r for r in _dump_rows(new_con, table)}
            assert set(old_by_pk) <= set(new_by_pk), f"{table} 升级后丢了旧行"
            allowed = _ALLOWED_NEW_COLUMNS.get(table, ())
            for pk, old_row in old_by_pk.items():
                new_row = new_by_pk[pk]
                for col, old_val in old_row.items():
                    if col in allowed:
                        continue  # 明示新增列：升级后允许不存在/为新值
                    assert new_row.get(col) == old_val, (
                        f"{table}.{col}（id={pk}）升级后旧列值变了: "
                        f"{old_val!r} → {new_row.get(col)!r}")
    finally:
        new_con.close()
        old_con.close()


def test_corrupt_database_rejected(tmp_path, rehearsal):
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 64 + b"garbage-bytes")
    env = dict(rehearsal["env"])
    env["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(bad).replace(os.sep, "/")
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", CAND_HEAD],
        cwd=str(BACKEND), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300, check=False,
    )
    assert r.returncode != 0, "损坏库的迁移必须失败（拒绝而非吞错/跳过）"


# ── REWORK1：唯一冲突真实制造 + 内核真实证据生命周期 ──

def test_unique_conflicts_actually_produced(rehearsal, upgraded):
    """三类唯一约束各真造一次冲突：memories.write_key / item_key / evidence_key。"""
    con = _connect(rehearsal["db"])
    try:
        # write_key：同值两行
        con.execute(
            "UPDATE memories SET write_key = ? WHERE id = (SELECT MIN(id) FROM memories)",
            ("wk-dup-0001",))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO memories (user_id, character_id, memory_type, content, scope,"
                " importance, is_archived, is_pinned, review_count, is_locked, ai_rated,"
                " is_core, confirmation_count, contradiction_count, version, write_key,"
                " created_at, updated_at)"
                " VALUES (101, 201, 'event', '写键冲突行', 'character', 1,"
                " 0, 0, 0, 0, 0, 0, 0, 0, 1, 'wk-dup-0001',"
                " '2026-10-20 10:00:00', '2026-10-20 10:00:00')")
        con.rollback()
    finally:
        con.close()


def test_c9_unique_conflicts_actually_produced(rehearsal, upgraded):
    con = _connect(rehearsal["db"])
    try:
        # REWORK2 自包含：不依赖「302 已在 cascade 用例删除」这一收集顺序事实——
        # 自建独立父链 303（user 102/char 203 本体始终在库），并先清掉同名残留。
        con.execute("DELETE FROM chat_sessions WHERE id = ?", (303,))
        con.execute(
            "INSERT INTO chat_sessions (id, user_id, character_id, is_active)"
            " VALUES (303, 102, 203, 1)")
        con.execute(
            "INSERT INTO business_plan_items (user_id, character_id, chat_session_id,"
            " source_message_id, source_event_at, source_hash, ordinal, target_text,"
            " item_key, cancelled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (102, 203, 303, None, "2026-10-20 09:00:00", "h10", 1, "任务X", "item-dup-1", 0))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO business_plan_items (user_id, character_id, chat_session_id,"
                " source_message_id, source_event_at, source_hash, ordinal, target_text,"
                " item_key, cancelled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (102, 203, 303, None, "2026-10-20 09:00:00", "h11", 2, "任务X重复键",
                 "item-dup-1", 0))
        con.execute(
            "INSERT INTO plan_completion_evidence (user_id, character_id, chat_session_id,"
            " source_message_id, source_event_at, source_hash, ordinal, target_text,"
            " evidence_key, claim_text, item_id, binding_kind)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (102, 203, 303, None, "2026-10-20 09:05:00", "e10", 1, "完成X",
             "ev-dup-1", "做了X", None, "unresolved"))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO plan_completion_evidence"
                " (user_id, character_id, chat_session_id, source_message_id,"
                "  source_event_at, source_hash, ordinal, target_text,"
                "  evidence_key, claim_text, item_id, binding_kind)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (102, 203, 303, None, "2026-10-20 09:06:00", "e11", 2, "完成X重复键",
                 "ev-dup-1", "又做了X", None, "unresolved"))
        con.commit()
    finally:
        con.close()


# ── REWORK2：真实内核 API 证据生命周期（不再手填 SHA/键/时间/绑定） ──

_KERNEL_SCOPE = dict(user_id=101, character_id=201, chat_session_id=301)


async def _kernel_flow(factory, tag, mutate=None):
    """走**真实内核 API** 跑一遍计划→断言→绑定→完成读，并返回可断言的状态。

    真实 API（app/memory/plan_completion.py）：
    ``PlanScope`` / ``register_item`` / ``record_assertion`` / ``bind_explicit`` /
    ``item_is_completed``——全部**显式接收 db**，本用例**不做任何全局工厂覆盖**。

    ``mutate``：可选的破坏动作（删完成源消息 / 改完成源正文），在绑定后执行。
    """
    from datetime import datetime as _dt

    from app.memory.plan_completion import (
        PlanScope, bind_explicit, item_is_completed, record_assertion, register_item)
    from app.models.chat import ChatMessage as _CM
    from app.models.memory import PlanCompletionEvidence, PlanItem

    scope = PlanScope(**_KERNEL_SCOPE)
    target = f"设备{tag}的季度校验"
    plan_text = f"{target}安排在明天上午"
    done_text = f"{target}已经完成"
    at_plan = _dt(2026, 10, 20, 8, 0, 0)
    at_done = _dt(2026, 10, 20, 9, 0, 0)

    # 两条**真实**用户源消息（计划源 + 稍后完成源），真实 UTC 与真实 scope
    async with factory() as db:
        plan_msg = _CM(session_id=scope.chat_session_id, sender_type="user",
                       content=plan_text, created_at=at_plan)
        done_msg = _CM(session_id=scope.chat_session_id, sender_type="user",
                       content=done_text, created_at=at_done)
        db.add_all([plan_msg, done_msg])
        await db.flush()
        plan_id, done_id = plan_msg.id, done_msg.id
        await db.commit()

    # ① 真实内核写入：内核自己生成真实 SHA/稳定键与合法绑定
    async with factory() as db:
        item = await register_item(db, scope, plan_id, target, ordinal=1)
        evidence = await record_assertion(db, scope, done_id, target, done_text, ordinal=1)
        await db.commit()
        item_id, evidence_id = item.id, evidence.id
        assert item.item_key and evidence.evidence_key, "内核应生成真实稳定键"
        assert item.source_hash and evidence.source_hash, "内核应生成真实源 SHA"
        assert item.source_event_at == at_plan and evidence.source_event_at == at_done, (
            "内核应取持久化源消息的真实时间，而非提取时间")

    async with factory() as db:
        item = await db.get(PlanItem, item_id)
        evidence = await db.get(PlanCompletionEvidence, evidence_id)
        bound = await bind_explicit(db, evidence, item)
        await db.commit()
        assert bound is True, "同 scope + 源可验 + 时间更晚 ⇒ 应绑定成功"
        evidence = await db.get(PlanCompletionEvidence, evidence_id)
        assert evidence.binding_kind == "explicit", "真实内核写的绑定类型是 explicit"
        assert evidence.item_id == item_id

    async with factory() as db:
        item = await db.get(PlanItem, item_id)
        completed_before = await item_is_completed(db, item)

    if mutate is not None:
        await mutate(factory, plan_id, done_id)

    async with factory() as db:
        item = await db.get(PlanItem, item_id)
        completed_after = await item_is_completed(db, item)
        ev = await db.get(PlanCompletionEvidence, evidence_id)
        return {
            "item_id": item_id, "evidence_id": evidence_id,
            "plan_id": plan_id, "done_id": done_id,
            "completed_before": completed_before,
            "completed_after": completed_after,
            "evidence_source_id": ev.source_message_id,
            "claim_text": ev.claim_text,
            "binding_kind": ev.binding_kind,
        }


def test_kernel_evidence_real_api_completes(rehearsal, upgraded):
    """正例：真实内核 API ⇒ ``item_is_completed`` 为 True（有合法完成绑定）。"""
    async def case():
        return await _kernel_flow(rehearsal["factory"], "P1")
    state = asyncio.run(case())
    assert state["completed_before"] is True, "真实内核绑定后完成读应为 True"
    assert state["completed_after"] is True
    assert state["binding_kind"] == "explicit"
    assert state["evidence_source_id"] == state["done_id"]


def test_kernel_evidence_real_api_source_deleted_invalidates(rehearsal, upgraded):
    """删除完成源消息 ⇒ FK SET NULL、证据正文留存、**完成读为 False**。"""
    async def _delete_done(factory, plan_id, done_id):
        from app.models.chat import ChatMessage as _CM
        async with factory() as db:
            msg = await db.get(_CM, done_id)
            await db.delete(msg)
            await db.commit()

    async def case():
        return await _kernel_flow(rehearsal["factory"], "P2", mutate=_delete_done)
    state = asyncio.run(case())
    assert state["completed_before"] is True
    assert state["completed_after"] is False, "完成源被删 ⇒ 完成读必须失效"
    assert state["evidence_source_id"] is None, "源删除后外键 SET NULL"
    assert state["claim_text"], "证据正文须保留（不物理删）"
    # 不假定 DB 会改 binding_kind——DB 不会改；失效来自读侧校验
    assert state["binding_kind"] == "explicit", "binding_kind 是普通列，DB 不会改写"


def test_kernel_evidence_real_api_source_edited_invalidates(rehearsal, upgraded):
    """编辑完成源正文 ⇒ 源 hash 与库内不符 ⇒ **完成读为 False**（FK 仍指向该源）。"""
    async def _edit_done(factory, plan_id, done_id):
        from app.models.chat import ChatMessage as _CM
        async with factory() as db:
            msg = await db.get(_CM, done_id)
            msg.content = "设备P3的季度校验尚未完成，改为下周"
            await db.commit()

    async def case():
        return await _kernel_flow(rehearsal["factory"], "P3", mutate=_edit_done)
    state = asyncio.run(case())
    assert state["completed_before"] is True
    assert state["completed_after"] is False, "完成源正文被改 ⇒ 完成读必须失效"
    assert state["evidence_source_id"] == state["done_id"], (
        "正文被改时 FK 仍指向该源——失效来自源 hash 校验，不是 SET NULL")


def test_kernel_plan_source_invalidates_completion_read(rehearsal, upgraded):
    """计划源被删 ⇒ 计划本身失权 ⇒ 完成读为 False（计划源/完成源两条边界都覆盖）。"""
    async def _delete_plan(factory, plan_id, done_id):
        from app.models.chat import ChatMessage as _CM
        async with factory() as db:
            msg = await db.get(_CM, plan_id)
            await db.delete(msg)
            await db.commit()

    async def case():
        return await _kernel_flow(rehearsal["factory"], "P4", mutate=_delete_plan)
    state = asyncio.run(case())
    assert state["completed_before"] is True
    assert state["completed_after"] is False, "计划源失权 ⇒ 完成读必须失效"


def test_global_session_factory_not_polluted_on_failure(rehearsal, monkeypatch):
    """中途故意失败后，全局 ``async_session_factory`` 必须恢复原值（防 129 例泄漏复发）。

    REWORK2：本文件**不再**做全局覆盖（内核 API 显式接收 db）；本用例把「若有人再加
    覆盖」的失败路径钉死——异常抛出后原值必须回来。
    """
    import app.db.database as db_mod
    original = db_mod.async_session_factory
    with pytest.raises(RuntimeError):
        with monkeypatch.context() as mp:
            mp.setattr(db_mod, "async_session_factory", rehearsal["factory"])
            raise RuntimeError("synthetic mid-way failure")
    assert db_mod.async_session_factory is original, "失败路径必须恢复全局工厂"


def test_global_session_factory_untouched_by_kernel_cases(rehearsal, upgraded):
    """跑完真实内核用例后，全局工厂仍是原值（本文件不污染进程级状态）。"""
    import app.db.database as db_mod
    original = db_mod.async_session_factory
    asyncio.run(_kernel_flow(rehearsal["factory"], "P5"))
    assert db_mod.async_session_factory is original, "内核用例不得改动全局会话工厂"

