# -*- coding: utf-8 -*-
"""B2b：真实 init_db -> ensure_alembic_revision 启动顺序（子进程隔离）。"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from _memory_migration_fixture import prune_excluding, read_chain

BACKEND = Path(__file__).resolve().parents[1]
OLD_HEAD = "e2f4a6b8c0d2"
CANDIDATE = "b2a0c1d2e3f4"


def _heads_of(versions_dir: Path) -> set[str]:
    chain = read_chain(versions_dir)
    parents = {down for (_p, down) in chain.values() if down is not None}
    return {rev for rev in chain if rev not in parents}


_repo_heads = _heads_of(BACKEND / "alembic" / "versions")
assert len(_repo_heads) == 1, f"仓库迁移图应单头，实得 {sorted(_repo_heads)}"
REAL_HEAD = next(iter(_repo_heads))
# CANDIDATE(b2a) 仍是 AUTOINCREMENT/CHECK 的锚点，但**真 head 已前移**到它的后代
# c4e6f8a0b2d4（memories.write_key）。因此 startup 后版本落点断言从 CANDIDATE 改为
# REAL_HEAD；否则旧脚本只 unlink 候选文件会留下后代孤儿，alembic 加载炸 KeyError——
# 本批 43 条失败的直接成因。
assert REAL_HEAD != CANDIDATE, (
    "本批语义假设 CANDIDATE 已不是 head；若这条断了说明迁移链被截短"
)


def _build_legacy(tmp_path: Path) -> Path:
    tree = tmp_path / "old_tree"
    shutil.copytree(BACKEND / "alembic", tree, ignore=shutil.ignore_patterns("__pycache__"))
    # 按 **迁移图** 剪除 CANDIDATE 及其全部后代；剩余图应以 OLD_HEAD 为唯一 head。
    removed = prune_excluding(tree / "versions", CANDIDATE)
    assert CANDIDATE in removed, f"剪枝未覆盖 CANDIDATE：{removed}"
    assert _heads_of(tree / "versions") == {OLD_HEAD}, "剪除 CANDIDATE 后旧树应单头于 OLD_HEAD"
    db = tmp_path / "old.db"
    env = _env(db, tmp_path)
    code = (
        "import sys\n"
        "from alembic import command\n"
        "from alembic.config import Config\n"
        "cfg=Config()\n"
        "cfg.set_main_option('script_location', sys.argv[1])\n"
        "cfg.set_main_option('prepend_sys_path', sys.argv[2])\n"
        "cfg.set_main_option('sqlalchemy.url','')\n"
        "command.upgrade(cfg,'e2f4a6b8c0d2')\n"
    )
    subprocess.run(
        [sys.executable, "-c", code, str(tree), str(BACKEND)],
        cwd=str(BACKEND), env=env, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    _make_legacy_ai(db)
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


def _apply_c4_increment(db: Path) -> None:
    """用真实 c4 增量迁移（Operations.add_column/create_index）补 write_key 列与唯一索引。

    返工 §2：migrate.py 新增 ``(memories, write_key)`` 哨兵后，orphan 恢复正例需要先
    让 schema 变成「已确认」，同时保持 ai_characters 仍无 AUTOINCREMENT（否则 startup
    会走 stamped 而不是 re-stamped）。不能用 create_all，也不能改产品门禁。
    """
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

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


def _state_snapshot(db: Path) -> dict:
    """拒绝路径专用的最小快照：版本、行、DDL、序列、memories 列——每项都不能因异常而变。"""
    con = sqlite3.connect(str(db))
    try:
        version_row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        return {
            "version": version_row[0] if version_row else None,
            "ai_ddl": con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
            ).fetchone()[0],
            "chars": [tuple(r) for r in con.execute(
                "SELECT id,user_id,name FROM ai_characters ORDER BY id")],
            "memories_cols": sorted(
                r[1] for r in con.execute("PRAGMA table_info(memories)").fetchall()
            ),
            "memories_idx": sorted(
                r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='memories'"
                ).fetchall()
            ),
            "sequences": [tuple(r) for r in con.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name")]
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone()
                else None,
        }
    finally:
        con.close()


def _env(db: Path, tmp_path: Path) -> dict:
    env = os.environ.copy()
    env["DATABASE_URL"] = "sqlite+aiosqlite:///" + Path(db).resolve().as_posix()
    env["CHROMA_PERSIST_DIR"] = str((Path(tmp_path) / "vector").resolve())
    env["PYTHONIOENCODING"] = "utf-8"
    env["AUTH_SECRET_KEY"] = "b2b-startup-only-0123456789abcdef"
    for key in ("AMBRACE_TEST_DB_URL", "AMBRACE_TEST_KEEP_PROD", "AMBRACE_CREDENTIAL_KEY_FILE"):
        env.pop(key, None)
    return env


def _run_startup(db: Path, tmp_path: Path, *, expect_fail: bool = False) -> dict:
    out = Path(tmp_path) / "startup.json"
    if out.exists():
        out.unlink()
    code = (
        "import asyncio, json, sqlite3, sys\n"
        "from pathlib import Path\n"
        "from app.db.init_db import init_db\n"
        "from app.db.migrate import ensure_alembic_revision\n"
        "asyncio.run(init_db())\n"
        "action=asyncio.run(ensure_alembic_revision())\n"
        "db=sys.argv[1]; out=Path(sys.argv[2])\n"
        "con=sqlite3.connect(db)\n"
        "ddl=con.execute(\"SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'\").fetchone()\n"
        "ver=con.execute('SELECT version_num FROM alembic_version').fetchone()\n"
        "seq=con.execute(\"SELECT seq FROM sqlite_sequence WHERE name='ai_characters'\").fetchone()\n"
        "chars=con.execute('SELECT id,user_id,name FROM ai_characters ORDER BY id').fetchall()\n"
        "objs=con.execute(\"SELECT type,name,sql FROM sqlite_master WHERE tbl_name='ai_characters' AND type IN ('index','trigger') ORDER BY type,name\").fetchall()\n"
        "mk=sorted(r[1] for r in con.execute('PRAGMA table_info(memories)').fetchall())\n"
        "mi=sorted(r[0] for r in con.execute(\"SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='memories'\").fetchall())\n"
        "con.close()\n"
        "out.write_text(json.dumps({'action':action,'version':ver[0] if ver else None,"
        "'autoincrement':bool(ddl and 'AUTOINCREMENT' in (ddl[0] or '').upper()),"
        "'seq':seq[0] if seq else None,'chars':chars,'objects':objs,"
        "'memories_cols':mk,'memories_idx':mi},ensure_ascii=False),encoding='utf-8')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(db), str(out)],
        cwd=str(BACKEND), env=_env(db, tmp_path),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
    )
    if expect_fail:
        assert proc.returncode != 0, (
            f"startup 应失败；实际 rc=0 stdout={proc.stdout!r}"
        )
        return {"returncode": proc.returncode,
                "stderr": proc.stderr, "stdout": proc.stdout}
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, [sys.executable, "-c", "<startup>"],
            output=proc.stdout, stderr=proc.stderr,
        )
    return json.loads(out.read_text(encoding="utf-8"))


def _seed(db: Path) -> None:
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("INSERT INTO users(id,username,nickname) VALUES(1,'b2b','B')")
    con.execute(
        "INSERT INTO ai_characters(id,user_id,name,is_partner,is_active,"
        "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) VALUES(101,1,'keep',0,1,1,1,0)"
    )
    con.execute(
        "INSERT INTO lorebook_entries(user_id,character_id,title,content,keywords,"
        "exclude_keywords,active,is_regex,probability,inclusion_group,sticky_rounds,"
        "cooldown_rounds) VALUES(1,999,'orphan','x','[]','[]',1,0,100,'',0,0)"
    )
    con.commit()
    con.close()


def test_fresh_startup_creates_autoincrement_and_stamps(tmp_path):
    db = tmp_path / "fresh.db"
    result = _run_startup(db, tmp_path)
    assert result["action"].startswith("stamped:") or result["action"].startswith("upgraded:")
    assert result["version"] == REAL_HEAD
    assert result["autoincrement"] is True


def test_old_head_startup_preserves_rows_and_upgrades(tmp_path):
    db = _build_legacy(tmp_path)
    _seed(db)
    result = _run_startup(db, tmp_path)
    assert result["version"] == REAL_HEAD
    assert result["autoincrement"] is True
    assert result["chars"] == [[101, 1, "keep"]]
    assert result["seq"] >= 999
    # 整链 upgrade：c4 应补 write_key 列与唯一索引（migrate.py 新哨兵要求）。
    assert "write_key" in result["memories_cols"], result["memories_cols"]
    assert "ix_memories_write_key" in result["memories_idx"], result["memories_idx"]


def test_no_version_legacy_startup_stamps_parent(tmp_path):
    """无版本 + 缺 write_key 老库：合法走整链 upgrade 到 REAL_HEAD，不再假盖 stamped-旧父。

    返工 §2：migrate.py 加了 ``(memories, write_key)`` 哨兵 ⇒ _schema_is_current=False ⇒
    _ensure_alembic_revision_sync_inner 走 upgrade-head 分支（不是 _stamp_then_upgrade）。
    动作字符串前缀断言 `upgraded:->`，不是 `upgraded:stamped-`。
    """
    db = _build_legacy(tmp_path)
    _seed(db)
    con = sqlite3.connect(str(db)); con.execute("DROP TABLE alembic_version"); con.commit(); con.close()
    result = _run_startup(db, tmp_path)
    assert result["version"] == REAL_HEAD
    assert result["autoincrement"] is True
    assert result["seq"] >= 999
    assert result["action"].startswith(f"upgraded:->{REAL_HEAD}"), result["action"]
    assert "write_key" in result["memories_cols"]
    assert "ix_memories_write_key" in result["memories_idx"]


def test_orphan_revision_missing_write_key_startup_refuses(tmp_path):
    """未知 revision + 缺 write_key：startup 必须 fail-closed，DB 状态一字不改。

    返工 §2：orphan 分支只在「schema 已确认」时清旧历史。legacy DB 没有 write_key
    ⇒ _schema_is_current=False ⇒ 抛 "unconfirmed schema"。子进程必须 rc≠0，
    版本 / 行 / ai_characters DDL / memories 列索引 / sqlite_sequence 全部与调用前一致。
    不能吞异常也不能放宽产品门禁。
    """
    db = _build_legacy(tmp_path)
    _seed(db)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE alembic_version SET version_num='deadbeefdead'")
    con.commit()
    con.close()
    before = _state_snapshot(db)
    assert "write_key" not in before["memories_cols"], (
        "本用例前提：老库缺 write_key 列（c4 未跑）"
    )
    assert "AUTOINCREMENT" not in (before["ai_ddl"] or "").upper()
    fail = _run_startup(db, tmp_path, expect_fail=True)
    assert "unconfirmed schema" in fail["stderr"], fail["stderr"][-2000:]
    after = _state_snapshot(db)
    assert after == before, "拒绝路径不得改版本/行/DDL/memories/序列"


def test_orphan_revision_schema_confirmed_restamps_parent(tmp_path):
    """未知 revision + schema 已确认（有 write_key）：走 re-stamped-{OLD_HEAD}->head 恢复。

    返工 §2 明确要求保留这条正例——不能只把旧断言全改成 raises 就删掉恢复覆盖。
    合成手法：用真实 c4 增量迁移（``Operations.add_column/create_index``）先补 write_key
    列与唯一索引，其余形态（含 ai_characters 无 AUTOINCREMENT）保留 legacy；
    随后 alembic_version='deadbeefdead'，init_db + ensure_alembic_revision 应
    purge 旧历史、stamp 到 OLD_HEAD、再 upgrade head 到 REAL_HEAD；
    b2a 装 AUTOINCREMENT/CHECK，c4 幂等不重加列，最终 schema-confirmed。
    禁 create_all 预补历史库、禁 mock schema 判断、禁改产品门禁。
    """
    db = _build_legacy(tmp_path)
    _seed(db)
    _apply_c4_increment(db)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE alembic_version SET version_num='deadbeefdead'")
    con.commit()
    cols_before = sorted(r[1] for r in con.execute("PRAGMA table_info(memories)").fetchall())
    ddl_before = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
    ).fetchone()[0]
    con.close()
    assert "write_key" in cols_before
    assert "AUTOINCREMENT" not in (ddl_before or "").upper(), (
        "本用例前提：ai_characters 仍无 AUTOINCREMENT（否则退化为 already_at_head 分支）"
    )
    result = _run_startup(db, tmp_path)
    assert result["action"].startswith(f"upgraded:re-stamped-{OLD_HEAD}->{REAL_HEAD}"), result["action"]
    assert result["version"] == REAL_HEAD
    assert result["autoincrement"] is True
    assert result["chars"] == [[101, 1, "keep"]]
    assert "write_key" in result["memories_cols"], "恢复路径不得删列"
    assert "ix_memories_write_key" in result["memories_idx"]


def test_repeat_startup_does_not_rebuild_or_lower_sequence(tmp_path):
    db = _build_legacy(tmp_path)
    _seed(db)
    first = _run_startup(db, tmp_path)
    second = _run_startup(db, tmp_path)
    assert first["version"] == second["version"] == REAL_HEAD
    assert second["action"].startswith("already_at_head")
    assert second["seq"] >= first["seq"] >= 999
    assert second["objects"] == first["objects"]
    assert second["chars"] == first["chars"]
