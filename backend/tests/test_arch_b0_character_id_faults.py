# -*- coding: utf-8 -*-
"""B0 收尾补证：启动矩阵 B/D/G/H + 真实迁移连接 trace + 六窗恢复后 `_startup` 幂等。

唯一性约定（收尾单 §2）：本文件每个测试、`_startup`、`_stamp` **各只定义一次**。
证据：默认写 pytest tmp_path；设置环境变量 `B0_OUT` 时导出到交付目录，**不在导入期创建仓库内目录**。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

BACKEND = Path(__file__).resolve().parents[1]
ALEMBIC_TREE = BACKEND / "alembic"
CANDIDATE_FILE = "b2a0c1d2e3f4_ai_characters_autoincrement.py"
OLD_HEAD = "e2f4a6b8c0d2"
CANDIDATE = "b2a0c1d2e3f4"

# 2026-10-23 Qwen 测试专批：
# ``CANDIDATE`` 是 **约束锚点**（AUTOINCREMENT/CHECK/触发器都装在 b2a 这一步），
# 六窗故障注入与「旧形态 → 迁到 b2a」这类断言仍锚定 CANDIDATE；
# 但 b2a 已经有后代 ``c4e6f8a0b2d4``（memories.write_key 迁移），**真 head 是后代**。
# 因此：
#   - 凡「startup 后应落在 head」的断言都改用 REAL_HEAD（由本目录的迁移图现算，不硬编）；
#   - 旧树构造改用 _memory_migration_fixture.prune_excluding(versions, CANDIDATE)——
#     它按 down_revision 反向走把 CANDIDATE 与**其全部后代**一并剪除，
#     旧脚本只 unlink 候选文件会留下后代孤儿 ⇒ alembic 加载 ScriptDirectory 报 KeyError。
from _memory_migration_fixture import prune_excluding, read_chain as _read_chain


def _current_heads(versions_dir: Path) -> set[str]:
    """versions 图里的 head 集合：任何不被别的 revision 当父的 id。"""
    chain = _read_chain(versions_dir)
    parents = {down for (_p, down) in chain.values() if down is not None}
    return {rev for rev in chain if rev not in parents}


_head_set = _current_heads(ALEMBIC_TREE / "versions")
assert len(_head_set) == 1, f"仓库迁移图应单头，实得 {sorted(_head_set)}"
REAL_HEAD = next(iter(_head_set))
assert REAL_HEAD != CANDIDATE, (
    "本批假设 b2a 已不再是 head（其写侧后代 c4e6f8a0b2d4 已接入）；"
    "若这条断言炸了说明迁移链被人为截短，测试夹具语义需重审"
)

STAGES = ["AFTER_CREATE", "AFTER_COPY", "AFTER_DROP",
          "AFTER_RENAME", "AFTER_SEQUENCE", "AFTER_CHECK"]
STAGE_LABEL = {
    "AFTER_CREATE": "创建新表后", "AFTER_COPY": "复制数据后", "AFTER_DROP": "删除旧表后",
    "AFTER_RENAME": "新表更名后", "AFTER_SEQUENCE": "序列更新后", "AFTER_CHECK": "外键校验后",
}
NEW_TABLE = "ai_characters_b2a_new"
_OUT = os.environ.get("B0_OUT")          # 未设置则只写 tmp_path（不污染工作树）


def _dump(tmp_path: Path, name: str, obj: dict) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=1, default=str)
    (tmp_path / name).write_text(text, encoding="utf-8")
    if _OUT:
        Path(_OUT).mkdir(parents=True, exist_ok=True)
        (Path(_OUT) / name).write_text(text, encoding="utf-8")


# ── 夹具 ────────────────────────────────────────────────────────────
def _tree(tmp_path: Path, name: str, *, with_candidate: bool = True,
          stage: str | None = None) -> Path:
    tree = tmp_path / name
    shutil.copytree(ALEMBIC_TREE, tree, ignore=shutil.ignore_patterns("__pycache__"))
    versions = tree / "versions"
    cand = versions / CANDIDATE_FILE
    if not with_candidate:
        # 图遍历剪除：CANDIDATE **及其全部后代**（本批是 c4e6f8a0b2d4）一起删；
        # 只 unlink 候选会让后代成为孤儿，alembic 加载 ScriptDirectory 时 KeyError。
        removed = prune_excluding(versions, CANDIDATE)
        assert CANDIDATE in removed, f"剪枝未覆盖 CANDIDATE：{removed}"
        assert not cand.exists()
    elif stage is not None:
        marker = f"# B2A_FAIL_{stage}"
        lines = cand.read_text(encoding="utf-8").splitlines()
        hit = 0
        for i, line in enumerate(lines):
            if marker in line:
                lines[i] = line[: len(line) - len(line.lstrip())] + \
                    f'raise RuntimeError("injected failure at {stage}")'
                hit += 1
        assert hit == 1, f"marker {marker} 命中 {hit} 次（应为 1）"
        cand.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return tree


def _cfg(tree: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(tree.resolve()))
    cfg.set_main_option("prepend_sys_path", str(BACKEND.resolve()))
    cfg.set_main_option("sqlalchemy.url", "")
    return cfg


def _with_settings(db: Path, fn) -> object:
    """alembic 的 env.py 取 `settings.database_url`（忽略 cfg）⇒ 临时改写并复原。"""
    from app.config import settings
    prev = settings.database_url
    settings.database_url = f"sqlite+aiosqlite:///{db.as_posix()}"
    try:
        return fn()
    finally:
        settings.database_url = prev


def _upgrade(tree: Path, db: Path, target: str = "head") -> None:
    _with_settings(db, lambda: command.upgrade(_cfg(tree), target))


def _stamp(tree: Path, db: Path, rev: str) -> None:
    _with_settings(db, lambda: command.stamp(_cfg(tree), rev))


def _startup(db: Path, *, initialize_only: bool = False) -> str:
    """真实启动链：init_db() → ensure_alembic_revision()。

    `init_db` 模块**自持 engine 引用**，仅改 settings/database.engine 会让它用旧库
    （曾出现"只 stamp 未建表"的假通过）⇒ 必须同时重绑 `app.db.init_db.engine`。
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.db.database as dbmod
    import app.db.init_db as initdb_mod
    from app.config import settings
    from app.db.init_db import init_db
    from app.db.migrate import ensure_alembic_revision

    prev_url = settings.database_url
    prev_engine = dbmod.engine
    prev_init_engine = getattr(initdb_mod, "engine", None)
    settings.database_url = f"sqlite+aiosqlite:///{db.as_posix()}"
    eng = create_async_engine(settings.database_url)
    dbmod.engine = eng
    initdb_mod.engine = eng
    try:
        asyncio.run(init_db())
        if initialize_only:
            return "initialized_only"
        return asyncio.run(ensure_alembic_revision())
    finally:
        dbmod.engine = prev_engine
        if prev_init_engine is not None:
            initdb_mod.engine = prev_init_engine
        settings.database_url = prev_url
        asyncio.run(eng.dispose())


def _legacy(db: Path) -> None:
    """旧形态 ai_characters（来源：既有迁移测试 `_make_legacy_ai` 同款；不读真实库）。"""
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA foreign_keys=OFF")
    ddl = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
                      ).fetchone()[0]
    leg = re.sub(r"id\s+INTEGER\s+NOT\s+NULL\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
                 "id INTEGER NOT NULL", ddl, count=1, flags=re.I).rstrip()
    leg = re.sub(r',\s*CONSTRAINT\s+"?ck_ai_characters_client_safe_id"?\s+CHECK\s*\([^)]*\)',
                 "", leg, count=1, flags=re.I)
    leg = leg[:-1].rstrip().rstrip(",") + ",\n\tPRIMARY KEY (id)\n)"
    cols = [r[1] for r in con.execute("PRAGMA table_info(ai_characters)")]
    leg = leg.replace('CREATE TABLE "ai_characters"', 'CREATE TABLE "ai_characters_legacy"', 1)
    con.execute(leg)
    con.execute(f"INSERT INTO ai_characters_legacy ({', '.join(chr(34)+c+chr(34) for c in cols)}) "
                f"SELECT {', '.join(chr(34)+c+chr(34) for c in cols)} FROM ai_characters")
    con.execute("DROP TABLE ai_characters")
    con.execute("ALTER TABLE ai_characters_legacy RENAME TO ai_characters")
    con.commit()
    con.close()


def _ins(con, table, vals):
    info = con.execute(f'PRAGMA table_info("{table}")').fetchall()
    use = {}
    for r in info:
        col, ctype, notnull, default = r[1], (r[2] or "").upper(), r[3], r[4]
        if col in vals:
            use[col] = vals[col]
        elif notnull and default is None:
            use[col] = "" if any(k in ctype for k in ("CHAR", "TEXT", "CLOB")) else 0
    con.execute(f'INSERT INTO "{table}" ({", ".join(chr(34)+c+chr(34) for c in use)}) '
                f'VALUES ({", ".join("?" for _ in use)})', list(use.values()))


def _apply_c4_increment(db: Path) -> None:
    """用真实 c4 增量迁移（Operations.add_column/create_index）补 write_key + 唯一索引。

    返工 §2：migrate.py 新增 ``(memories, write_key)`` 哨兵 ⇒ legacy 老库不再「schema 当前」。
    orphan 恢复正例需要先补上这一列与索引，但**不能同时把 ai_characters 变成 AUTOINCREMENT**
    （否则退化为 already_at_head 分支，re-stamped 覆盖丢失）。所以只跑 c4 的
    Operations.upgrade，不跑整链。禁止 create_all 预补历史库，禁止 mock schema 判断。
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


def _seed(db: Path) -> None:
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA foreign_keys=ON")
    con.executemany("INSERT INTO users(id,username,nickname) VALUES(?,?,?)",
                    [(1, "b0_a", "A"), (2, "b0_b", "B")])
    con.executemany(
        "INSERT INTO ai_characters(id,user_id,name,is_partner,is_active,"
        "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked,relation_type) "
        "VALUES(?,?,?,0,1,1,1,0,'朋友')",     # 预置 relation_type：排除 init_db 回填噪声
        [(101, 1, "A保留"), (102, 2, "B保留"), (103, 1, "A目标")])
    _ins(con, "chat_sessions", {"id": 11, "user_id": 1, "character_id": 103, "is_active": 1})
    _ins(con, "chat_messages", {"id": 21, "session_id": 11, "character_id": 103,
                                "sender_type": "user", "content": "SENTINEL_HISTORY"})
    con.commit()
    con.close()


def _build_old(tmp_path: Path, name: str) -> Path:
    db = tmp_path / f"{name}.db"
    tree = _tree(tmp_path, f"{name}_tree", with_candidate=False)
    versions = tree / "versions"
    # 旧树里 CANDIDATE 与其**真 head 后代**都已按图剪除；剩余图必须完整且 head 落在 OLD_HEAD。
    assert not (versions / CANDIDATE_FILE).exists()
    old_heads = _current_heads(versions)
    assert old_heads == {OLD_HEAD}, (
        f"剪除 CANDIDATE 及后代后剩余图应以 OLD_HEAD 为唯一 head，实得 {sorted(old_heads)}"
    )
    _upgrade(tree, db, OLD_HEAD)
    _legacy(db)
    _seed(db)
    return db


# ── 状态 ────────────────────────────────────────────────────────────
def _state(db: Path) -> dict:
    con = sqlite3.connect(str(db))
    try:
        tables = sorted(r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        ddl = {t: con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                              (t,)).fetchone()[0] for t in tables}
        idx = {t: sorted(r[0] for r in con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (t,))) for t in tables}
        trg = {t: sorted(r[0] for r in con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (t,)))
            for t in tables}
        rows = {t: sorted(json.dumps(list(v), default=str, ensure_ascii=False)
                          for v in con.execute(f'SELECT * FROM "{t}"').fetchall()) for t in tables}
        try:
            r = con.execute("SELECT seq FROM sqlite_sequence WHERE name='ai_characters'").fetchone()
            seq = r[0] if r else None
        except sqlite3.Error:
            seq = None
        ver = (con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
               if "alembic_version" in tables else None)
        return {"tables": tables, "ddl": ddl, "index_sql": idx, "trigger_sql": trg,
                "rows": rows, "sequence": seq, "version": ver,
                "foreign_key_check": sorted(str(r) for r in con.execute("PRAGMA foreign_key_check")),
                "row_count": sum(len(v) for v in rows.values()),
                "new_table_present": NEW_TABLE in tables}
    finally:
        con.close()


# 角色 ID 与引用相关的关键表：断言"数据未被改绑/清理"只看这些，
# 避免被 init_db 的既有回填（如 relation_type / 服务端管理员）污染（派单已提示不要要求 init_db 不写库）。
KEY_TABLES = ("ai_characters", "lorebook_entries", "chat_messages", "chat_sessions")


def _rows_base(st: dict, base: dict) -> dict:
    """关键表的行值快照（故障后多出的临时表属中间态，不计入）。"""
    keys = [t for t in KEY_TABLES if t in st["rows"] and t in base["rows"]]
    return {t: st["rows"][t] for t in keys}


def _backup(src: Path, dst: Path) -> None:
    s, d = sqlite3.connect(str(src)), sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def _restore(backup: Path, dst: Path) -> None:
    for suffix in ("-wal", "-shm"):
        p = Path(str(dst) + suffix)
        if p.exists():
            p.unlink()
    shutil.copyfile(backup, dst)


# ── 1) 对照 ─────────────────────────────────────────────────────────
def test_对照_旧形态库无注入可升到head且不变量完好(tmp_path):
    db = _build_old(tmp_path, "ctl")
    before = _state(db)
    # 「无注入可升到 head」里的 head 是**当前图的真 head**（CANDIDATE 后代已接入），
    # 不是 CANDIDATE 本身；AUTOINCREMENT/CHECK 等约束仍由 CANDIDATE(b2a) 这一步落地，
    # 断言语义不变，只是版本落点从 CANDIDATE 改为 REAL_HEAD。
    _upgrade(_tree(tmp_path, "ctl_clean"), db, "head")
    after = _state(db)
    _dump(tmp_path, "ctl.json", {"before": before, "after": after})
    assert after["version"] == REAL_HEAD
    assert _rows_base(after, before) == _rows_base(before, before)
    assert after["foreign_key_check"] == []
    assert "AUTOINCREMENT" in (after["ddl"]["ai_characters"] or "").upper()
    from sqlalchemy import create_engine
    from app.db.character_id_guard import verify_client_safe_id_guard
    eng = create_engine(f"sqlite:///{db.as_posix()}")
    con = eng.connect()
    try:
        verify = verify_client_safe_id_guard(con)
    finally:
        con.close()
        eng.dispose()
    assert verify.get("present") is True, verify


# ── 2) 六窗 + 迁移前备份恢复 + 恢复后真实启动幂等 ────────────────────
@pytest.mark.parametrize("stage", STAGES, ids=STAGE_LABEL.values())
def test_六窗_中断_迁移前备份整库恢复_再启动幂等(tmp_path, stage):
    db = _build_old(tmp_path, f"w_{stage}")
    pre = _state(db)
    backup = tmp_path / f"pre_{stage}.db"
    _backup(db, backup)
    pre_b = _state(backup)
    assert _rows_base(pre_b, pre) == _rows_base(pre, pre) and pre_b["version"] == pre["version"]

    with pytest.raises(RuntimeError) as ei:
        _upgrade(_tree(tmp_path, f"inj_{stage}", stage=stage), db, CANDIDATE)
    assert str(ei.value) == f"injected failure at {stage}"

    af = _state(db)
    assert af["new_table_present"], f"{stage}: 预期留下 {NEW_TABLE}"
    assert af["version"] == pre["version"]
    assert _rows_base(af, pre) == _rows_base(pre, pre)

    rerun = {"result": "ok"}
    try:
        _upgrade(_tree(tmp_path, f"rerun_{stage}"), db, CANDIDATE)
        rerun = {"result": "ok", **_state(db)}
    except Exception as e:  # noqa: BLE001
        rerun = {"result": "error", "error": f"{type(e).__name__}: {e}"[:400], **_state(db)}
    if rerun["result"] == "error":
        msg = rerun["error"]
        assert any(k in msg for k in ("already exists", "not recognized", "refusing")), \
            f"{stage}: 拒绝原因不在已知集合：{msg}"

    _restore(backup, db)
    rs = _state(db)
    assert rs["tables"] == pre_b["tables"] and rs["ddl"] == pre_b["ddl"]
    assert rs["index_sql"] == pre_b["index_sql"] and rs["trigger_sql"] == pre_b["trigger_sql"]
    assert rs["rows"] == pre_b["rows"] and rs["version"] == pre_b["version"]
    assert rs["sequence"] == pre_b["sequence"] and not rs["new_table_present"]

    # 恢复后用**真实启动链**（不是再来一次 upgrade）验证幂等
    action = _startup(db)
    fin = _state(db)
    assert "lacks AUTOINCREMENT" not in action
    # 启动走整树（b2a + c4）→ 版本必须落在真 head，而不是 CANDIDATE。
    # CANDIDATE 仍是约束锚点（AUTOINCREMENT/CHECK 由它装），六窗故障注入仍打到 CANDIDATE。
    assert fin["version"] == REAL_HEAD and fin["foreign_key_check"] == []
    assert _rows_base(fin, pre) == _rows_base(pre, pre)
    action2 = _startup(db)
    fin2 = _state(db)
    assert action2.startswith("already_at_head")
    assert fin2["ddl"] == fin["ddl"] and fin2["rows"] == fin["rows"]

    _dump(tmp_path, f"window_{stage}.json",
          {"stage": stage, "pre": pre, "after_fault": af, "rerun": rerun,
           "restored": rs, "startup": action, "final": fin, "startup2": action2})


# ── 3) 启动矩阵 ─────────────────────────────────────────────────────
def test_矩阵A_真正空库新装(tmp_path):
    db = tmp_path / "fresh.db"
    action = _startup(db)
    st = _state(db)
    _dump(tmp_path, "matrix_A.json", {"action": action, **st})
    assert action.startswith(("stamped:", "upgraded:")), action
    # 全新库启动落到当前图的**真 head**（CANDIDATE 已不是 head：其写侧后代 c4 已接入）。
    assert st["version"] == REAL_HEAD and "ai_characters" in st["tables"]
    assert "AUTOINCREMENT" in (st["ddl"]["ai_characters"] or "").upper()
    assert st["foreign_key_check"] == []


def test_矩阵J_成功后重复启动幂等(tmp_path):
    db = tmp_path / "again.db"
    _startup(db)
    first = _state(db)
    action2 = _startup(db)
    second = _state(db)
    _dump(tmp_path, "matrix_J.json", {"action2": action2, "first": first, "second": second})
    assert action2.startswith("already_at_head"), action2
    assert second["ddl"] == first["ddl"] and second["rows"] == first["rows"]


def test_矩阵B_已有AUTOINCREMENT_序列被压低后启动修复(tmp_path):
    """B：迁到 head 后把 sqlite_sequence 压低，并放一条**已知语义**的更高引用 ⇒ 启动应修复序列，
    不重建角色表，二次启动幂等。"""
    db = _build_old(tmp_path, "b")
    _upgrade(_tree(tmp_path, "b_up"), db, CANDIDATE)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE sqlite_sequence SET seq=5 WHERE name='ai_characters'")
    _ins(con, "lorebook_entries", {"id": 1, "character_id": 150, "title": "HIGH_REF"})
    con.commit()
    con.close()
    before = _state(db)
    action = _startup(db)
    after = _state(db)
    _dump(tmp_path, "matrix_B.json", {"action": action, "before": before, "after": after})
    assert after["ddl"]["ai_characters"] == before["ddl"]["ai_characters"], "不得重建角色表"
    assert int(after["sequence"] or 0) >= 150, f"序列应修复到高水位(150)，实际 {after['sequence']}"
    ref = [r for r in after["rows"]["lorebook_entries"] if "HIGH_REF" in r]
    assert ref, "引用不得被清理"
    assert _startup(db).startswith("already_at_head")


def test_矩阵D_head但旧形态_启动链精确拒绝(tmp_path):
    """D：版本已 head、表仍是旧形态 ⇒ 启动链精确拒绝；不得改绑 ID / 前移版本 / 建临时表。
    relation_type 已在种子里预置，排除 init_db 回填噪声。

    2026-10-23 Qwen 批：这里 stamp 的目标必须是**当前图的真 head**——
    ``_ensure_alembic_revision_sync_inner`` 走「版本==head 但表非 AUTOINCREMENT ⇒ 拒绝」这条
    分支，只有 stamp 落在 head 才会命中；若 stamp 到 CANDIDATE(b2a)，version!=head
    会走 `_stamp_then_upgrade` 直接补跑 c4，测试语义就没了。
    """
    db = _build_old(tmp_path, "d")
    _stamp(_tree(tmp_path, "d_stamp"), db, REAL_HEAD)
    before = _state(db)
    assert before["version"] == REAL_HEAD
    assert "AUTOINCREMENT" not in before["ddl"]["ai_characters"].upper()
    refused = None
    try:
        _startup(db)
    except Exception as e:  # noqa: BLE001 - launcher 可能包装成 CommandError
        refused = f"{type(e).__name__}: {e}"
    after = _state(db)
    _dump(tmp_path, "matrix_D.json", {"refused": refused, "before": before, "after": after})
    assert refused and "lacks AUTOINCREMENT" in refused and "refusing to bypass rebuild" in refused, refused
    assert after["version"] == REAL_HEAD, "版本不得前移"
    assert after["ddl"]["ai_characters"] == before["ddl"]["ai_characters"], "不得改结构"
    assert not after["new_table_present"], "拒绝阶段不得建临时表"
    assert _rows_base(after, before) == _rows_base(before, before), "角色 ID/引用不得改绑"


def test_矩阵G_已删角色但存在更高语义引用_启动不回退不复用(tmp_path):
    """G：主表最大 103，但 lorebook_entries 引用已删除的角色 150 ⇒ 启动后高水位必须 ≥150，
    原引用保留、不清理/不重绑定。"""
    db = _build_old(tmp_path, "g")
    con = sqlite3.connect(str(db))
    _ins(con, "lorebook_entries", {"id": 1, "character_id": 150, "title": "ORPHAN_REF"})
    con.commit()
    con.close()
    before = _state(db)
    action = _startup(db)
    after = _state(db)
    _dump(tmp_path, "matrix_G.json", {"action": action, "before": before, "after": after})
    assert after["version"] == REAL_HEAD
    assert int(after["sequence"] or 0) >= 150, f"高水位不得回退（实际 {after['sequence']}）"
    assert [r for r in after["rows"]["lorebook_entries"] if "ORPHAN_REF" in r], "引用不得被清理"
    assert "AUTOINCREMENT" in after["ddl"]["ai_characters"].upper()
    assert after["rows"]["lorebook_entries"] == before["rows"]["lorebook_entries"], "引用行值必须完整保留"


def test_矩阵H_超客户端上限_启动精确拒绝且不动数据(tmp_path):
    """H：合成库里 ai_characters.id 超过 2^53-1 ⇒ 启动必须精确拒绝，且不清理/不改结构。"""
    db = _build_old(tmp_path, "h")
    from app.db.character_id_guard import CLIENT_SAFE_MAX_ID
    con = sqlite3.connect(str(db))
    _ins(con, "ai_characters",
         {"id": CLIENT_SAFE_MAX_ID + 5, "user_id": 1, "name": "OVER_MAX",
          "is_partner": 0, "is_active": 1, "cognitive_loop_enabled": 1,
          "memory_v2_enabled": 1, "talkativeness_locked": 0, "relation_type": "朋友"})
    con.commit()
    con.close()
    before = _state(db)
    refused = None
    try:
        _startup(db)
    except Exception as e:  # noqa: BLE001
        refused = f"{type(e).__name__}: {e}"
    after = _state(db)
    _dump(tmp_path, "matrix_H.json", {"refused": refused, "before": before, "after": after})
    assert refused, "超上限必须拒绝，实际未抛出"
    assert ("client-safe" in refused) or ("sequence exhausted" in refused), refused
    # init_db 的既有回填会改行 ⇒ 只断言「角色与引用的 ID 集合未被删除/改绑」
    def _ids(st):
        import json as _j
        chars = sorted(_j.loads(r)[0] for r in st["rows"].get("ai_characters", []))
        refs = sorted(_j.loads(r)[1] for r in st["rows"].get("lorebook_entries", []))
        return chars, refs
    assert _ids(after) == _ids(before), "拒绝时不得删除/改绑角色或引用 ID"
    assert after["version"] == before["version"] and not after["new_table_present"]


def test_矩阵E_无版本表存量库(tmp_path):
    """无版本 + 缺 write_key 老库：走整链 `upgraded:->REAL_HEAD`。

    返工 §2：migrate.py 加了 ``(memories, write_key)`` 哨兵 ⇒ _schema_is_current=False ⇒
    _ensure_alembic_revision_sync_inner 走 upgrade-head（不是 _stamp_then_upgrade）。
    """
    db = _build_old(tmp_path, "e")
    con = sqlite3.connect(str(db))
    con.execute("DROP TABLE alembic_version")
    con.commit()
    con.close()
    action = _startup(db)
    st = _state(db)
    _dump(tmp_path, "matrix_E.json", {"action": action, **st})
    assert action.startswith(f"upgraded:->{REAL_HEAD}"), action
    assert st["version"] == REAL_HEAD
    # write_key 列与唯一索引必须由真实 c4 迁移补齐（migrate.py 新哨兵要求）。
    mem_cols = sorted(r[1] for r in sqlite3.connect(str(db)).execute(
        "PRAGMA table_info(memories)").fetchall())
    assert "write_key" in mem_cols, mem_cols


def test_矩阵F_未知revision_缺write_key启动拒绝(tmp_path):
    """未知 revision + 缺 write_key：startup 必须 fail-closed，DB 状态一字不改。

    返工 §2：orphan 分支要求 schema 已确认；legacy 老库缺 c4 引入的 write_key
    ⇒ 现有实现直接抛 "unconfirmed schema"。断言版本 / 表 / 行 / DDL / 序列 /
    foreign_key_check 全部与调用前一致；静默落库等于掩盖问题。
    """
    db = _build_old(tmp_path, "f_refuse")
    # 先完成真实init_db的一次性默认值初始化，再设置损坏版本做拒绝分支验证。
    # 不执行ensure/迁移，旧schema仍缺write_key；拒绝前后全库不变断言保持。
    assert _startup(db, initialize_only=True) == "initialized_only"
    assert _state(db)["version"] == OLD_HEAD
    con = sqlite3.connect(str(db))
    con.execute("UPDATE alembic_version SET version_num='deadbeef0000'")
    con.commit()
    con.close()
    before = _state(db)
    mem_cols_before = sorted(r[1] for r in sqlite3.connect(str(db)).execute(
        "PRAGMA table_info(memories)").fetchall())
    assert "write_key" not in mem_cols_before, "本用例前提：老库缺 write_key 列"
    with pytest.raises(RuntimeError, match="unconfirmed schema"):
        _startup(db)
    after = _state(db)
    _dump(tmp_path, "matrix_F_refuse.json", {"before": before, "after": after})
    assert after == before, "拒绝路径不得改版本/行/DDL/序列/FK"
    assert after["version"] == "deadbeef0000"
    mem_cols_after = sorted(r[1] for r in sqlite3.connect(str(db)).execute(
        "PRAGMA table_info(memories)").fetchall())
    assert mem_cols_after == mem_cols_before


def test_矩阵F_未知revision_schema已确认时恢复(tmp_path):
    """未知 revision + schema 已确认（有 write_key）：走 re-stamped-{OLD_HEAD}->head 恢复。

    返工 §2 明确要求保留这条正例——不能只把旧断言全改成 raises 就删掉恢复覆盖。
    合成手法：用真实 c4 增量迁移（``Operations.add_column/create_index``）补 write_key
    列与唯一索引，其余形态（含 ai_characters 无 AUTOINCREMENT）保留 legacy；
    然后 alembic_version='deadbeef0000'，init_db + ensure_alembic_revision 应
    purge 旧历史、stamp 到 OLD_HEAD、再 upgrade head 到 REAL_HEAD。
    禁 create_all 预补历史库、禁 mock schema 判断、禁改产品门禁。
    """
    db = _build_old(tmp_path, "f_recover")
    _apply_c4_increment(db)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE alembic_version SET version_num='deadbeef0000'")
    con.commit()
    con.close()
    cols_before = sorted(r[1] for r in sqlite3.connect(str(db)).execute(
        "PRAGMA table_info(memories)").fetchall())
    assert "write_key" in cols_before, "本用例前提：write_key 已由真实 c4 迁移补齐"
    action = _startup(db)
    st = _state(db)
    _dump(tmp_path, "matrix_F_recover.json", {"action": action, **st})
    assert action.startswith(f"upgraded:re-stamped-{OLD_HEAD}->{REAL_HEAD}"), action
    assert st["version"] == REAL_HEAD
    assert not st["new_table_present"]
    cols_after = sorted(r[1] for r in sqlite3.connect(str(db)).execute(
        "PRAGMA table_info(memories)").fetchall())
    assert "write_key" in cols_after, "恢复不得删列"


# ── 4) 真实迁移连接的 DBAPI trace ───────────────────────────────────


def test_实际迁移连接_正常及复制后中断的事务边界(tmp_path):
    """观察 Alembic 自建引擎的真实 DBAPI 连接，不修改产品迁移。"""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    runs = []
    for fail in (False, True):
        case = tmp_path / ("interrupted" if fail else "normal")
        case.mkdir()
        db = _build_old(case, "trace")
        tree = _tree(case, "observed", stage="AFTER_COPY" if fail else None)
        connections = []

        def observe(dbapi_connection, connection_record):
            if not hasattr(dbapi_connection, "set_trace_callback"):
                return
            filename = dbapi_connection.execute("PRAGMA database_list").fetchone()[2]
            if Path(filename).resolve() != db.resolve():
                return
            statements = []
            connections.append(statements)
            dbapi_connection.set_trace_callback(statements.append)

        event.listen(Engine, "connect", observe)
        try:
            if fail:
                with pytest.raises(RuntimeError, match="^injected failure at AFTER_COPY$"):
                    _upgrade(tree, db, CANDIDATE)
            else:
                _upgrade(tree, db, CANDIDATE)
        finally:
            event.remove(Engine, "connect", observe)

        migration = [sql for sql in connections
                     if any('CREATE TABLE "ai_characters_b2a_new"' in q for q in sql)]
        assert len(migration) == 1, "必须捕获实际执行重建的唯一连接"
        sql = [q.strip() for q in migration[0]]
        create = next(i for i, q in enumerate(sql)
                      if q.startswith('CREATE TABLE "ai_characters_b2a_new"'))
        begin = next(i for i, q in enumerate(sql) if q.upper() == "BEGIN")
        assert any(q.upper() == "PRAGMA FOREIGN_KEYS=OFF" for q in sql[:create])
        assert create < begin, "记录当前实际路径：建表先于驱动事务 BEGIN"
        assert "ROLLBACK" in sql if fail else "COMMIT" in sql
        state = _state(db)
        assert state["version"] == (OLD_HEAD if fail else CANDIDATE)
        assert state["new_table_present"] is fail
        runs.append({"interrupted": fail, "statements": sql,
                     "version": state["version"],
                     "new_table_present": state["new_table_present"]})
    _dump(tmp_path, "actual_migration_transaction_trace.json", {"runs": runs})
