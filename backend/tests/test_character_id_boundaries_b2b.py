# -*- coding: utf-8 -*-
"""B2b 返工 R1/R2 边界：条件引用分类、未来 head 锚点、客户端容量、恢复边界。

Rework1 §2 补充：migrate.py 新增 ``(memories, write_key)`` 哨兵之后，
「无版本 + 缺 write_key」的老库不再是「schema 已确认」，只能走整链
``upgraded:->head``；未知 revision 若同样缺列必须 fail-closed 拒绝，
不能强盖版本。为了保留「schema 已确认时的 orphan 恢复」正例，
用真实 c4 增量迁移（``Operations.add_column`` + ``Operations.create_index``）
补齐 ``write_key`` 列与唯一索引，同时保持 ai_characters 无 AUTOINCREMENT
——那才是 re-stamped 分支真正要覆盖的形态。禁止用 create_all 或 mock
schema 判断绕过。
"""
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from app.config import settings
from app.db import migrate
from app.db.character_id_guard import CHECK_NAME

from _memory_migration_fixture import append_future, prune_excluding, read_chain

BACKEND = Path(__file__).resolve().parents[1]
OLD_HEAD = "e2f4a6b8c0d2"
CANDIDATE = "b2a0c1d2e3f4"
FUTURE = "c0ffee123456"
CLIENT_SAFE_MAX_ID = (1 << 53) - 1
ABOVE_CLIENT_SAFE = CLIENT_SAFE_MAX_ID + 2  # 2^53+1，Web 不可精确表示


def _heads_of(versions_dir: Path) -> set[str]:
    """versions 图的 head 集合：任何不被别的 revision 当父的 id（含 CANDIDATE 后代判定）。"""
    chain = read_chain(versions_dir)
    parents = {down for (_p, down) in chain.values() if down is not None}
    return {rev for rev in chain if rev not in parents}


_repo_heads = _heads_of(BACKEND / "alembic" / "versions")
assert len(_repo_heads) == 1, f"仓库迁移图应单头，实得 {sorted(_repo_heads)}"
REAL_HEAD = next(iter(_repo_heads))
# CANDIDATE 是约束锚点（AUTOINCREMENT/CHECK 装在这一步），但**已不再是 head**——
# 本批的真 head 是它的写侧后代 c4e6f8a0b2d4（memories.write_key）。
assert REAL_HEAD != CANDIDATE, (
    "本批语义假设 CANDIDATE 已不是 head；若这条断了说明迁移链被截短，"
    "_tree / 断言需要重审"
)


def _cfg(tree: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(tree.resolve()))
    cfg.set_main_option("prepend_sys_path", str(BACKEND.resolve()))
    cfg.set_main_option("sqlalchemy.url", "")
    return cfg


def _tree(tmp_path: Path, name: str, *, candidate: bool = True,
          future: bool = False) -> Path:
    """按 **迁移图** 组树，而不是硬删某几个文件。

    - ``candidate=False``（旧形态库）：CANDIDATE **及其全部后代**（现在是 c4e6f8a0b2d4）
      一并剪除。旧脚本只 unlink 候选、留后代，alembic 加载 ScriptDirectory 会 KeyError——
      本批 43 条失败的直接成因。
    - ``future=True``（伪未来 head）：**只在真实 head 之上追加**合成 revision，
      不删既有迁移。历史上把 FUTURE 挂在 CANDIDATE 下会形成双头（CANDIDATE 已有
      后代 c4；再加个 c0ffee 兄弟 ⇒ get_current_head 不稳定）。
    """
    tree = tmp_path / name
    shutil.copytree(BACKEND / "alembic", tree, ignore=shutil.ignore_patterns("__pycache__"))
    versions = tree / "versions"
    if not candidate:
        removed = prune_excluding(versions, CANDIDATE)
        assert CANDIDATE in removed, f"剪枝未覆盖 CANDIDATE：{removed}"
    if future:
        # 追加锚定在**当前真 head**（本批为 c4e6f8a0b2d4），保持单头。
        # append_future 会拒绝非 head 锚点，因此这里不需要额外兜底。
        anchor = next(iter(_heads_of(versions)))
        append_future(versions, new_rev=FUTURE, on_head=anchor,
                      name="c0ffee123456_future_noop.py")
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


def _apply_real_c4_increment(db: Path) -> None:
    """用真实 c4 增量迁移（Operations.add_column/create_index）补 write_key + 唯一索引。

    刻意**不跑整链 upgrade**：b2a 会把 ai_characters 变成 AUTOINCREMENT，
    那样 startup 会走 stamped 分支而不是我们要覆盖的 re-stamped。
    本 helper 只补 migrate.py 新加的 ``(memories, write_key)`` 哨兵，
    其余形态（含无 AUTOINCREMENT）保持 legacy。禁止 create_all 或 mock schema。
    """
    engine = create_engine("sqlite:///" + Path(db).resolve().as_posix())
    try:
        with engine.begin() as conn:
            ctx = MigrationContext.configure(connection=conn)
            ops = Operations(ctx)
            insp = sa.inspect(conn)
            cols = {c["name"] for c in insp.get_columns("memories")}
            if "write_key" not in cols:
                ops.add_column(
                    "memories", sa.Column("write_key", sa.String(64), nullable=True)
                )
            idxs = set(conn.execute(sa.text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='memories'"
            )).scalars())
            if "ix_memories_write_key" not in idxs:
                ops.create_index(
                    "ix_memories_write_key", "memories", ["write_key"], unique=True
                )
    finally:
        engine.dispose()


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
    """无版本 + 缺 write_key 的老库：合法走整链 ``upgraded:->FUTURE``，不再假盖 stamped-旧父。

    返工 §2：migrate.py 加了 ``(memories, write_key)`` 哨兵后，老库 schema 不"当前"，
    走 F572 的 upgrade-head 分支；b2a 装 AUTOINCREMENT/CHECK、c4 装 write_key 列与唯一索引，
    都跑到位；行数据与 FK 必须原样保留，二次调用幂等落到 already_at_head。
    """
    db = _build_legacy(tmp_path, monkeypatch)
    _drop_version(db)
    future = _tree(tmp_path, "future_tree", future=True)
    _use(monkeypatch, tmp_path, db, future)
    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith(f"upgraded:->{FUTURE}"), action
    assert _version(db) == FUTURE
    assert "AUTOINCREMENT" in _ddl(db).upper()
    assert CHECK_NAME in _ddl(db)
    # write_key 列与唯一索引由真实 c4 迁移引入，整链 upgrade 后应存在。
    con = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(memories)").fetchall()}
        assert "write_key" in cols, "整链 upgrade 必须补上 write_key 列（migrate.py 新哨兵）"
        idxs = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='memories'"
        ).fetchall()}
        assert "ix_memories_write_key" in idxs, "整链 upgrade 必须补上 ix_memories_write_key"
        assert con.execute("SELECT id,user_id,name FROM ai_characters").fetchall() == [(101, 1, "keep")]
        assert con.execute("SELECT content FROM lorebook_entries WHERE character_id=999").fetchone()[0] == "orphan"
    finally:
        con.close()
    assert migrate._ensure_alembic_revision_sync().startswith("already_at_head")


def test_future_head_orphan_missing_write_key_fails_closed(tmp_path, monkeypatch):
    """未知 revision + 缺 write_key：必须 fail-closed，不得强盖版本或重建 AUTOINCREMENT。

    返工 §2：orphan 分支只允许在「schema 已确认是当前」时清旧历史。老库缺 write_key
    ⇒ _schema_is_current=False ⇒ 现有实现抛 'unconfirmed schema' 直接拒绝。
    断言：抛出 RuntimeError 后，版本号、行、DDL、序列都保持原样——静默落库等于掩盖问题。
    """
    db = _build_legacy(tmp_path, monkeypatch)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE alembic_version SET version_num='deadbeefdead'")
    con.commit()
    con.close()
    future = _tree(tmp_path, "future_tree", future=True)
    _use(monkeypatch, tmp_path, db, future)
    before = _state(db)
    with pytest.raises(RuntimeError, match="unconfirmed schema"):
        migrate._ensure_alembic_revision_sync()
    assert _state(db) == before, "拒绝路径不得改版本/行/DDL/序列"
    # 关键：拒绝后 schema 依旧缺 write_key（哨兵未满足）且 ai_characters 未 AUTOINCREMENT。
    assert "AUTOINCREMENT" not in _ddl(db).upper()
    con = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(memories)").fetchall()}
        assert "write_key" not in cols
    finally:
        con.close()


def test_future_head_orphan_schema_confirmed_recovers(tmp_path, monkeypatch):
    """未知 revision + schema 已确认（含 write_key）：走 re-stamped-{OLD_HEAD}->head 恢复。

    返工 §2 明确要求保留这条正例，不能只把旧断言全改成 raises 就删掉恢复覆盖。
    合成手法：用真实 c4 增量迁移（``Operations.add_column/create_index``）补 write_key
    列与唯一索引，其余仍保持 legacy（无 AUTOINCREMENT）；随后把版本改成 'deadbeefdead'，
    startup 应 purge 旧历史、stamp 到约束锚点父 (OLD_HEAD)、再 upgrade 到 FUTURE；
    b2a 装 AUTOINCREMENT/CHECK、c4 幂等跳过（列已存在），FUTURE 是 noop。
    """
    db = _build_legacy(tmp_path, monkeypatch)
    _apply_real_c4_increment(db)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE alembic_version SET version_num='deadbeefdead'")
    con.commit()
    cols_before = {r[1] for r in con.execute("PRAGMA table_info(memories)").fetchall()}
    ddl_before = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
    ).fetchone()[0]
    con.close()
    assert "write_key" in cols_before
    assert "AUTOINCREMENT" not in (ddl_before or "").upper(), (
        "本用例前提：ai_characters 仍是无 AUTOINCREMENT 的旧形态"
    )
    future = _tree(tmp_path, "future_tree", future=True)
    _use(monkeypatch, tmp_path, db, future)
    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith(f"upgraded:re-stamped-{OLD_HEAD}->{FUTURE}"), action
    assert _version(db) == FUTURE
    assert "AUTOINCREMENT" in _ddl(db).upper()
    assert CHECK_NAME in _ddl(db)
    con = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(memories)").fetchall()}
        assert "write_key" in cols, "恢复后 write_key 仍在（c4 幂等不删列）"
        idxs = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='memories'"
        ).fetchall()}
        assert "ix_memories_write_key" in idxs
        assert con.execute("SELECT id,user_id,name FROM ai_characters").fetchall() == [(101, 1, "keep")]
    finally:
        con.close()


def test_current_head_no_version_runs_constraint(tmp_path, monkeypatch):
    """无版本 + 缺 write_key 老库（真 head）：走整链 ``upgraded:->REAL_HEAD``。

    返工 §2：migrate.py 加了 write_key 哨兵 ⇒ _schema_is_current 返回 False ⇒
    走 F570 分支的整链 upgrade；不再是 stamped-{OLD_HEAD}->REAL_HEAD。
    """
    db = _build_legacy(tmp_path, monkeypatch)
    _drop_version(db)
    tree = _tree(tmp_path, "current_tree", future=False)
    _use(monkeypatch, tmp_path, db, tree)
    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith(f"upgraded:->{REAL_HEAD}"), action
    assert _version(db) == REAL_HEAD
    assert "AUTOINCREMENT" in _ddl(db).upper()
    assert CHECK_NAME in _ddl(db)
    con = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(memories)").fetchall()}
        assert "write_key" in cols
    finally:
        con.close()


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
    # 单头：伪未来 head 挂在**真 head 之上**（不是 CANDIDATE），
    # 保证 c4e6f8a0b2d4 这条真实迁移没被删掉来「假装没有别的未来 upgrade」。
    assert script.get_current_head() == FUTURE
    # 真 head 的父是当前链最尾的实际迁移（本批为 c4），而不是约束锚点 CANDIDATE。
    assert migrate._previous_head(script, FUTURE) == REAL_HEAD
    assert REAL_HEAD != CANDIDATE
    # 约束锚点仍是 b2a（其父 = OLD_HEAD），AUTOINCREMENT/CHECK 仍由它装。
    assert migrate.CONSTRAINT_REVISION == CANDIDATE
    assert migrate._constraint_base(script) == OLD_HEAD
    # 传递性：CANDIDATE 是 FUTURE 的祖先（老库仍会经过 b2a 才走到未来）。
    assert migrate._is_ancestor(script, CANDIDATE, FUTURE)
    # 未来 revision 不吞掉既有 c4：真实链上的 c4 文件必须仍在树里。
    assert any(p.name.startswith(f"{REAL_HEAD}_")
               for p in (future / "versions").glob("*.py")), \
        f"伪未来 head 场景不得删除既有迁移；c4 ({REAL_HEAD}) 文件缺失"
