# -*- coding: utf-8 -*-
"""A2 后续批次：``browser_snapshots`` 唯一键 (user_id, url) 落库回归（2026-09-20）。

覆盖：
① 物理层：迁移后 **PRAGMA index_list/index_info 与 SQLAlchemy inspect 一致显示**唯一键是
   (user_id, url)，且**不存在** url 单列唯一（旧口径会让多账号同网址互斥）；
② 数据层：两条不同 user_id、同一 url 的行可共存（同 user 同 url 仍互斥，唯一性没被放宽成无约束）；
   插件侧同一 user 二次写同一 url 走更新而非新增；老库既有行在整表重建后不丢；
③ 模型层（单一事实源）：ORM 声明联合唯一 + url 无单列唯一 + user_id 无 default=1；
④ 幂等：版本退回 down_revision 后连续 upgrade（迁移体真正重跑），第二次起守卫命中「0 操作」，
   约束与数据不变；新库（create_all 已是联合唯一）同一路径 0 操作；版本链仍为单头且
   ``c0d1e2f3a4b5``（本迁移 down_revision）在新 head 祖先链上；整链重放亦落位。

老库基线怎么造：整链 ``upgrade`` 到交接点 ``c0d1e2f3a4b5``（物理 schema 齐平、本表仍是建表迁移
04dd1d6c5544 的 ``UNIQUE (url)``，链上后续迁移从未再动过本表），再 ``upgrade head`` 即只跑本迁移
一步。不能「upgrade 到 04dd 后 stamp 到交接点」——那样物理库落后 40 个迁移，本迁移末尾的
``ensure_indexes`` 会去补不存在的列（``no such column: users.parent_id``）。

只用 pytest ``tmp_path`` 建临时库，绝不触碰 backend/data/sqlite/ai_companion.db。
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

NEW_REV = "d1e2f3a4b5c6"
PREV_HEAD = "c0d1e2f3a4b5"  # 本迁移 down_revision（交接前的版本链头）= 老库基线
TABLE_CREATED_AT = "04dd1d6c5544"  # browser_snapshots 建表迁移，此后本表未被任何迁移改过
SAME_URL = "https://x/same"

# 快测档：每例半链/整链重放，重量级（同 test_game_session_user_fk 口径）
pytestmark = pytest.mark.slow


# ── 公共辅助 ──

def _cfg():
    from app.db.migrate import _alembic_config

    return _alembic_config()


def _point_at(db, monkeypatch) -> None:
    """把 settings.database_url 指到临时库（env.py 每次调用现读，派生出同步 URL）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db.as_posix()}")


def _create_all(db) -> None:
    import app.models  # noqa: F401
    from app.models._all import Base

    eng = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        Base.metadata.create_all(eng)
    finally:
        eng.dispose()


def _migrate_old_to_head(db, monkeypatch) -> None:
    """交接点存量库（本表仍 url 单列 UNIQUE）→ upgrade head（只有本迁移执行）。"""
    _point_at(db, monkeypatch)
    command.upgrade(_cfg(), PREV_HEAD)
    command.upgrade(_cfg(), "head")


def _unique_sets_via_pragma(db) -> list[frozenset]:
    """PRAGMA index_list + index_info 读**物理**唯一索引的列集合（只看 origin='u' 的约束型）。"""
    con = sqlite3.connect(str(db))
    try:
        sets = []
        for _seq, name, unique, origin, _partial in con.execute(
            "PRAGMA index_list(browser_snapshots)"
        ).fetchall():
            if not unique or origin != "u":
                continue  # 'u'=UNIQUE 约束的自动索引；'c'=CREATE INDEX
            cols = frozenset(r[2] for r in con.execute(f'PRAGMA index_info("{name}")').fetchall())
            sets.append(cols)
        return sets
    finally:
        con.close()


def _unique_sets_via_inspect(db) -> set[frozenset]:
    eng = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        insp = sa.inspect(eng)
        got = {
            frozenset(uc["column_names"])
            for uc in insp.get_unique_constraints("browser_snapshots")
        }
        got |= {
            frozenset(ix["column_names"])
            for ix in insp.get_indexes("browser_snapshots")
            if ix["unique"]
        }
        return got
    finally:
        eng.dispose()


def _assert_joint_unique(db, why: str) -> None:
    pragma = _unique_sets_via_pragma(db)
    insp = _unique_sets_via_inspect(db)
    want = frozenset({"user_id", "url"})
    assert want in pragma, f"{why}：PRAGMA 未见 UNIQUE(user_id,url)，实际={sorted(map(sorted, pragma))}"
    assert frozenset({"url"}) not in pragma, f"{why}：PRAGMA 仍有 url 单列 UNIQUE，实际={sorted(map(sorted, pragma))}"
    assert want in insp, f"{why}：inspect 未见 UNIQUE(user_id,url)，实际={sorted(map(sorted, insp))}"
    assert frozenset({"url"}) not in insp, f"{why}：inspect 仍有 url 单列 UNIQUE，实际={sorted(map(sorted, insp))}"


def _insert(db, user_id, url, title) -> int:
    con = sqlite3.connect(str(db))
    try:
        cur = con.execute(
            "INSERT INTO browser_snapshots (user_id, url, domain, title, text, image_urls_json)"
            " VALUES (?,?,?,?,?,?)",
            (user_id, url, "x.com", title, "正文", "[]"),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _rows(db) -> list[tuple]:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT user_id, url, title FROM browser_snapshots ORDER BY id ASC"
        ).fetchall()
    finally:
        con.close()


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


# ── 夹具：迁移后的库 + 指向它的 browser_mcp 插件 ──

@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    db = tmp_path / "bs_migrated.db"
    _migrate_old_to_head(db, monkeypatch)
    _assert_joint_unique(db, "夹具：迁移后")
    return db


@pytest.fixture()
def browser_mod(migrated_db, monkeypatch):
    """装载 browser_mcp 取模块引用（同 test_plugin_tenant_scope_m0 口径），会话指向已迁移库。"""
    from app.plugins import registry

    registry.load_plugin_dir(registry.EXAMPLE_DIR / "browser_mcp")
    mod = sys.modules.get("ai_plugin_browser_mcp")
    assert mod is not None, "browser_mcp 应可加载"
    mod._ensure_done = True  # 表由迁移建好，跳过插件内联 DDL

    engine = create_async_engine(f"sqlite+aiosqlite:///{migrated_db.as_posix()}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.db.database.async_session_factory", factory)
    yield mod
    asyncio.run(engine.dispose())


# ── ③ 模型层（单一事实源）+ ④ 版本链 ──

def test_head_single_and_prev_head_is_ancestor():
    sd = ScriptDirectory.from_config(_cfg())
    heads = sd.get_heads()
    assert len(heads) == 1, f"版本链必须单头，实际 heads={heads}"
    head = heads[0]

    anc = _ancestors(sd, head)
    assert PREV_HEAD in anc, f"{PREV_HEAD} 不在 head={head} 的祖先链上"
    assert NEW_REV in anc | {head}, f"新迁移 {NEW_REV} 未挂进版本链"
    assert sd.get_revision(NEW_REV).down_revision == PREV_HEAD, "新迁移 down_revision 应为交接前的 head"


def test_orm_declares_user_url_unique():
    import app.models  # noqa: F401  确保全部模型注册
    from app.models.user import BrowserSnapshot

    t = BrowserSnapshot.__table__
    uq = next(c for c in t.constraints if isinstance(c, sa.UniqueConstraint))
    assert [col.name for col in uq.columns] == ["user_id", "url"]
    assert uq.name == "uq_browser_snapshots_user_url"
    assert not t.c.url.unique, "url 不应再声明单列 unique"
    # default=1 已去掉：漏传 user_id 时报错，而不是静默记到 user 1
    assert t.c.user_id.default is None, "user_id 不应再带 Python 端 default=1"
    assert not t.c.user_id.nullable, "user_id 仍须 NOT NULL"


# ── ①② 老库补齐 + 数据不丢 + 幂等 ──

def test_old_db_upgrade_lands_joint_unique_and_twice_idempotent(tmp_path, monkeypatch, capsys):
    db = tmp_path / "bs_old.db"
    _point_at(db, monkeypatch)
    command.upgrade(_cfg(), PREV_HEAD)  # 交接点存量库（整链重放，物理 schema 齐平）

    pre = _unique_sets_via_pragma(db)
    assert frozenset({"url"}) in pre, f"前提：老库应为 url 单列 UNIQUE，实际={sorted(map(sorted, pre))}"
    assert frozenset({"user_id", "url"}) not in pre, "前提：老库不应已有联合唯一"
    _insert(db, 1, SAME_URL, "T1")
    _insert(db, 2, "https://other/1", "T2")

    command.upgrade(_cfg(), "head")  # 只有 d1e2f3a4b5c6 这一步执行
    _assert_joint_unique(db, "老库首次补齐")
    assert _rows(db) == [(1, SAME_URL, "T1"), (2, "https://other/1", "T2")], "整表重建丢数据"

    # 幂等：版本退回 down_revision，让迁移体真正再跑两遍（守卫命中 0 操作）
    for n in (1, 2):
        command.stamp(_cfg(), PREV_HEAD)
        capsys.readouterr()
        command.upgrade(_cfg(), "head")
        out = capsys.readouterr()
        assert "0 操作" in out.out + out.err, f"第 {n} 次重跑未命中幂等守卫：{out.out}{out.err}"
        _assert_joint_unique(db, f"第 {n} 次重跑")
        assert _rows(db) == [(1, SAME_URL, "T1"), (2, "https://other/1", "T2")], "重跑改动了数据"


# ── ② 迁移后的库上验证共存 / 互斥 / 更新 ──

def test_two_users_same_url_coexist_and_same_user_still_unique(migrated_db):
    a = _insert(migrated_db, 1, SAME_URL, "A")
    b = _insert(migrated_db, 2, SAME_URL, "B")  # 旧口径（url 全局唯一）在此必撞
    assert a != b
    assert _rows(migrated_db) == [(1, SAME_URL, "A"), (2, SAME_URL, "B")]
    with pytest.raises(sqlite3.IntegrityError):  # 唯一性仍在，只是从全局收窄到账号内
        _insert(migrated_db, 1, SAME_URL, "dup")
    assert len(_rows(migrated_db)) == 2


def test_plugin_same_user_updates_other_user_keeps_own_row(migrated_db, browser_mod):
    async def _go():
        await browser_mod._save_snapshot(SAME_URL, "x.com", "A1", "正文1", [], 1)
        await browser_mod._save_snapshot(SAME_URL, "x.com", "A2", "正文2", [], 1)  # 同账号 → 更新
        await browser_mod._save_snapshot(SAME_URL, "x.com", "B1", "正文B", [], 2)  # 另一账号 → 新行
        mine = await browser_mod._recent_snapshots(5, 1)
        theirs = await browser_mod._recent_snapshots(5, 2)
        return mine, theirs

    mine, theirs = asyncio.run(_go())
    assert _rows(migrated_db) == [(1, SAME_URL, "A2"), (2, SAME_URL, "B1")]
    assert [s["title"] for s in mine] == ["A2"]  # 读侧各看各的
    assert [s["title"] for s in theirs] == ["B1"]


# ── ④ 新库 0 操作 + 整链重放 ──

def test_new_db_zero_ops(tmp_path, monkeypatch, capsys):
    """新库 create_all 已是联合唯一 → 迁移体守卫命中 0 操作，数据不动。"""
    db = tmp_path / "bs_new.db"
    _create_all(db)
    _point_at(db, monkeypatch)

    _insert(db, 1, SAME_URL, "T1")
    _insert(db, 2, SAME_URL, "T2")  # 新库口径：各账号各存一行

    command.stamp(_cfg(), PREV_HEAD)  # 模拟 init_db 建库后 stamp 的「当前 schema 库」
    capsys.readouterr()
    command.upgrade(_cfg(), "head")
    command.upgrade(_cfg(), "head")  # 第二次是 alembic 层 no-op
    out = capsys.readouterr()
    assert "0 操作" in out.out + out.err, f"新库应 0 操作：{out.out}{out.err}"
    _assert_joint_unique(db, "新库 0 操作路径")
    assert _rows(db) == [(1, SAME_URL, "T1"), (2, SAME_URL, "T2")]


def test_full_chain_replay_lands_joint_unique(tmp_path, monkeypatch):
    """整链重放（app/db/migrate.py 的「非空老库 upgrade head」路径）：head 落位且唯一键为联合。"""
    db = tmp_path / "bs_chain.db"
    _point_at(db, monkeypatch)
    command.upgrade(_cfg(), "head")

    _assert_joint_unique(db, "整链重放")
    con = sqlite3.connect(str(db))
    try:
        ver = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()
    assert ver == NEW_REV, f"整链重放后版本应为 {NEW_REV}，实际 {ver}"
