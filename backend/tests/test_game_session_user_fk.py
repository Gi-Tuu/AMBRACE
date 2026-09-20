# -*- coding: utf-8 -*-
"""P3-5：``game_sessions.user_id`` → users 外键 ondelete=CASCADE 落库回归（2026-09-20）。

覆盖：
① 版本链：迁移后仍为**单头**，``b9c0d1e2f3a4``（本迁移 down_revision）在新 head 祖先链上；
② 模型层：ORM 声明 ondelete=CASCADE（单一事实源）；
③ 物理层：**SQLAlchemy inspect 读库中实际外键约束**，老库补齐后为 CASCADE；
④ 幂等：版本退回 down_revision 后连续 upgrade 两次（迁移体真正执行两遍），外键/数据/索引不回退；
   新库（create_all 已是 CASCADE）走同一路径 0 操作；
⑤ 行为金标准：PRAGMA foreign_keys=ON 下删 user → 名下对局级联删除（二级到 game_players）。

老库基线为什么取 c9d0e1f2a3b4：链上 d0a1b2c3d4e5 起会按**当前 ORM metadata** 整表重建
game_sessions（copy_from 口径），因此重放到 ≥ d0a1 的任意版本都已带上本次新声明的 CASCADE——
那是「重放副作用」不是老库。c9d0 是 game_sessions 仍保持 a4b5c6d7e8f9 原始 DDL（user_id 外键
无 ondelete）的最后一个版本，stamp 到交接点后只跑本迁移一步，才等价于真实存量库。

只用 pytest ``tmp_path`` 建临时库，绝不触碰 backend/data/sqlite/ai_companion.db。
"""
from __future__ import annotations

import sqlite3

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

NEW_REV = "c0d1e2f3a4b5"
PREV_HEAD = "b9c0d1e2f3a4"
OLD_REV = "c9d0e1f2a3b4"  # game_sessions 仍为原始 DDL（无 ondelete）的老库基线
SEED_USER_ID = 4242
SEED_SESSION_ID = 777

# 重建父表必须保住的东西（batch recreate 丢隐式索引的历史教训 + 子表外键不被牵连）
MUST_KEEP_INDEXES = {"ix_game_sessions_user_id", "ix_game_sessions_game_type"}
# c9d0 时点已具备 CASCADE 的子表（c9d0 本身重建过 game_players / game_memories）
CHILD_FKS_AT_OLD = [("game_players", "session_id"), ("game_memories", "session_id")]
# 整链/head 时点应有的全部子表（game_events 的 CASCADE 由后续 f2b3c4d5e6f7 落下）
CHILD_FKS_AT_HEAD = CHILD_FKS_AT_OLD + [("game_events", "session_id")]

# 重量级：每例整链重放 30+ 迁移，打 slow 标记（同 test_fk_ondelete_active_parents 口径）
pytestmark = pytest.mark.slow


# ── 公共辅助 ──

def _cfg():
    from app.db.migrate import _alembic_config

    return _alembic_config()


def _point_at(db, monkeypatch) -> None:
    """把 settings.database_url 指到临时库（env.py 每次调用现读，派生出同步 URL）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db.as_posix()}")


def _fk_ondelete(engine, table: str, col: str) -> str:
    """用 SQLAlchemy inspect 读**库中实际**外键的 ondelete；未声明按 "NO ACTION"。"""
    hit = [
        fk
        for fk in sa.inspect(engine).get_foreign_keys(table)
        if list(fk["constrained_columns"]) == [col]
    ]
    assert hit, f"{table}.{col} 上读不到外键约束"
    return (hit[0]["options"].get("ondelete") or "NO ACTION").upper()


def _ancestors(sd: ScriptDirectory, rev_id: str) -> set[str]:
    """沿 down_revision 向上走完祖先集合（兼容分叉/合并的多父）。"""
    seen: set[str] = set()
    stack = [rev_id]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        downs = sd.get_revision(cur).down_revision
        if isinstance(downs, str):
            stack.append(downs)
        elif downs:
            stack.extend(downs)
    seen.discard(rev_id)
    return seen


def _dummy_value(ctype: str):
    t = (ctype or "").upper()
    if "INT" in t or "BOOL" in t:
        return 0
    if any(k in t for k in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL")):
        return 0.0
    if "DATE" in t or "TIME" in t:
        return "2026-01-01 00:00:00"
    return "x"


def _insert_min_row(con: sqlite3.Connection, table: str, **overrides) -> None:
    """插一行最小行：overrides 显式给主键/外键，其余物理 NOT NULL 且无 DDL ``DEFAULT`` 的列补占位值。

    裸 sqlite INSERT 不跑 ORM 的 Python 端 default（game_sessions.status/phase/config_json/... 均是），
    故须逐列补齐（同 test_fk_ondelete_active_parents 的既有做法）。
    """
    cols, vals = [], []
    # PRAGMA table_info 行结构：(cid, name, type, notnull, dflt_value, pk)
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
    con.execute(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals
    )


# ── 落库后的统一断言（本迁移的职责边界：外键 + 数据 + 自身索引 + 子表外键不受牵连）──

def _seed(db) -> None:
    con = sqlite3.connect(str(db))
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        _insert_min_row(con, "game_sessions", id=SEED_SESSION_ID, user_id=SEED_USER_ID)
        _insert_min_row(con, "game_players", session_id=SEED_SESSION_ID, player_type="user")
        con.commit()
    finally:
        con.close()


def _assert_state_ok(db, why: str, child_fks) -> None:
    eng = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        assert _fk_ondelete(eng, "game_sessions", "user_id") == "CASCADE", f"{why}：外键回退了"
        for t, c in child_fks:
            assert _fk_ondelete(eng, t, c) == "CASCADE", f"{why}：子表 {t}.{c} 外键被牵连破坏"
        idx = {i["name"] for i in sa.inspect(eng).get_indexes("game_sessions")}
        assert MUST_KEEP_INDEXES <= idx, f"{why}：索引丢失，缺 {MUST_KEEP_INDEXES - idx}"
        with eng.connect() as conn:
            row = conn.execute(
                sa.text("SELECT id, user_id FROM game_sessions WHERE id=:i"),
                {"i": SEED_SESSION_ID},
            ).fetchone()
            child = conn.execute(
                sa.text("SELECT COUNT(*) FROM game_players WHERE session_id=:i"),
                {"i": SEED_SESSION_ID},
            ).scalar_one()
        assert row == (SEED_SESSION_ID, SEED_USER_ID), f"{why}：父行数据丢失/变形 {row}"
        assert child == 1, f"{why}：子表数据行数异常 {child}"
    finally:
        eng.dispose()


# ── ① 版本链 ──

def test_head_single_and_prev_head_is_ancestor():
    sd = ScriptDirectory.from_config(_cfg())
    heads = sd.get_heads()
    assert len(heads) == 1, f"版本链必须单头，实际 heads={heads}"
    head = heads[0]

    anc = _ancestors(sd, head)
    assert PREV_HEAD in anc, f"{PREV_HEAD} 不在 head={head} 的祖先链上"
    assert NEW_REV in anc | {head}, f"新迁移 {NEW_REV} 未挂进版本链"
    assert sd.get_revision(NEW_REV).down_revision == PREV_HEAD, "新迁移 down_revision 应为交接前的 head"


# ── ② 模型层（单一事实源）──

def test_orm_declares_cascade():
    import app.models  # noqa: F401  确保全部模型注册
    from app.models.game import GameSession

    fk = next(f for f in GameSession.__table__.foreign_keys if f.parent.name == "user_id")
    assert fk.target_fullname == "users.id"
    assert (fk.ondelete or "").upper() == "CASCADE", "GameSession.user_id 应声明 ondelete=CASCADE"


# ── ③④ 老库补齐 + 幂等 ──

def test_old_db_upgrade_lands_cascade_and_twice_idempotent(tmp_path, monkeypatch):
    db = tmp_path / "gs_fk_old.db"
    _point_at(db, monkeypatch)
    command.upgrade(_cfg(), OLD_REV)

    eng = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        assert _fk_ondelete(eng, "game_sessions", "user_id") == "NO ACTION", "前提：老库该外键应无 ondelete"
    finally:
        eng.dispose()

    _seed(db)
    command.stamp(_cfg(), PREV_HEAD)  # 存量库已追平到交接点，后续迁移不再重放
    command.upgrade(_cfg(), "head")  # 只有 c0d1e2f3a4b5 这一步执行
    _assert_state_ok(db, "老库首次补齐", CHILD_FKS_AT_OLD)

    # 幂等：版本退回 down_revision，让迁移体真正再跑两遍
    for n in (1, 2):
        command.stamp(_cfg(), PREV_HEAD)
        command.upgrade(_cfg(), "head")
        _assert_state_ok(db, f"第 {n} 次重跑", CHILD_FKS_AT_OLD)


def test_new_db_zero_ops(tmp_path, monkeypatch):
    """新库 create_all 已是 CASCADE → 守卫命中 0 操作，数据/索引不动。"""
    db = tmp_path / "gs_fk_new.db"
    _create_all(db)
    _point_at(db, monkeypatch)

    _seed(db)
    command.stamp(_cfg(), PREV_HEAD)  # 模拟 init_db 建库后 stamp 的「当前 schema 库」
    command.upgrade(_cfg(), "head")
    command.upgrade(_cfg(), "head")  # 第二次是 alembic 层 no-op
    _assert_state_ok(db, "新库 0 操作路径", CHILD_FKS_AT_HEAD)


def test_full_chain_replay_lands_cascade(tmp_path, monkeypatch):
    """整链重放（app/db/migrate.py 的「非空老库 upgrade head」路径）：head 落位且外键为 CASCADE。"""
    db = tmp_path / "gs_fk_chain.db"
    _point_at(db, monkeypatch)
    command.upgrade(_cfg(), "head")

    eng = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        assert _fk_ondelete(eng, "game_sessions", "user_id") == "CASCADE"
    finally:
        eng.dispose()
    con = sqlite3.connect(str(db))
    try:
        ver = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()
    # 2026-09-20：链头会随后续迁移继续前移（P3-5 之后又落了 browser_snapshots 联合唯一
    # d1e2f3a4b5c6），这里不再硬编码 NEW_REV；本迁移的落位由上面的祖先链用例保证。
    head = ScriptDirectory.from_config(_cfg()).get_heads()[0]
    assert ver == head, f"整链重放后版本应为当前单头 {head}，实际 {ver}"


# ── ⑤ 行为金标准 ──

def _create_all(db) -> None:
    import app.models  # noqa: F401
    from app.models._all import Base

    eng = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        Base.metadata.create_all(eng)
    finally:
        eng.dispose()


def test_delete_user_cascades_game_sessions(tmp_path):
    db = tmp_path / "gs_fk_behavior.db"
    _create_all(db)

    con = sqlite3.connect(str(db))
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        _insert_min_row(con, "users", id=SEED_USER_ID)
        _insert_min_row(con, "game_sessions", id=SEED_SESSION_ID, user_id=SEED_USER_ID)
        _insert_min_row(con, "game_players", session_id=SEED_SESSION_ID, player_type="user")
        con.commit()

        con.execute("PRAGMA foreign_keys=ON")
        con.execute("DELETE FROM users WHERE id=?", (SEED_USER_ID,))
        con.commit()

        assert con.execute("SELECT COUNT(*) FROM game_sessions").fetchone()[0] == 0, "删用户未级联删对局"
        assert con.execute("SELECT COUNT(*) FROM game_players").fetchone()[0] == 0, "二级级联失效"
    finally:
        con.close()
