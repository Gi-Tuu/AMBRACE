# -*- coding: utf-8 -*-
"""P3-5：插件表 schema reconcile（整链重放旧表收敛）回归（2026-09-11）。

覆盖（方案 §2/§4.1 + Codex 追加要求）：
① 整链重放出的旧抖音表（缺 ORM 后加的列 / 缺 tenant 单列索引）经
   ``_ensure_plugin_tables_sync()``（create_all + reconcile）后缺列=0、缺索引=0；
② 新库 create_all 后 reconcile 为 0 操作（且必须留下 info 日志，线上可分辨）；
③ 幂等：连跑两次，第二次 0 操作、schema 不变；
④ 只做加法：收敛前后列集合是超集、共有列类型不变（不 DROP 列 / 不改类型）；
⑤ ``_py_default_literal``：bool 分支先于 int/float、非标量默认返回 None 不抛错；
⑥ 两路收敛金标准：裸库真跑 alembic 整链 ``upgrade head`` 建出 baseline 旧插件表后，经真实
   启动路径 ``_ensure_plugin_tables_sync()`` 收敛，与纯 ORM ``create_all`` 逐表比「列集合 +
   索引签名」完全一致（douyin 5 表 + wechat 2 表）。

旧表伪造手法：先按当前 ORM ``create_all`` 建全量 schema，再 ``DROP COLUMN`` /
``DROP INDEX`` 剥掉「baseline 之后 ORM 新加、当时无迁移」的 5 列 4 索引 —— 其余列与
真实整链重放出的旧表一致（`aweme_id/comment_id`、`music_mood/post_type/video_path`
不参与任何 UNIQUE / 索引，故 SQLite 3.35+ 可原地 DROP）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from app.plugins import registry
from app.plugins.plugin_base import plugin_metadata

# baseline 之后 ORM 新加、当时无迁移补的列（整链重放旧表缺这些 → 运行时 no such column）
LEGACY_MISSING_COLUMNS = [
    ("douyin_comments", "aweme_id"),
    ("douyin_comments", "comment_id"),
    ("douyin_pending", "music_mood"),
    ("douyin_pending", "post_type"),
    ("douyin_pending", "video_path"),
]
# baseline 之后 ORM 新加、当时无迁移补的 tenant 单列索引
LEGACY_MISSING_INDEXES = [
    "ix_douyin_accounts_tenant_id",
    "ix_douyin_posts_tenant_id",
    "ix_douyin_pending_tenant_id",
    "ix_douyin_viewed_notes_tenant_id",
]
DOUYIN_TABLES = [
    "douyin_accounts", "douyin_posts", "douyin_comments",
    "douyin_pending", "douyin_viewed_notes",
]
WECHAT_TABLES = ["wechat_ilink_bindings", "wechat_ilink_messages"]
# ⑥ 金标准逐表比对清单：douyin 5 + wechat 2 = 7 张插件表
PLUGIN_TABLES = DOUYIN_TABLES + WECHAT_TABLES


# 快测档（2026-09-12）：本文件是重量级/集成型用例（每例起一次临时库，约 3s/例），打 slow 标记。
# 全量默认照跑；日常开发用 pytest -m "not slow" 跳过本档（见 docs/engineering-protocol.md 十八）。
pytestmark = pytest.mark.slow

@pytest.fixture()
def plugin_meta(monkeypatch, tmp_path):
    """加载两渠道插件注册 plugin_metadata，并把插件库 URL 指到本次临时库。"""
    registry.load_plugin_dir(registry.EXAMPLE_DIR / "douyin_mcp")
    registry.load_plugin_dir(registry.EXAMPLE_DIR / "wechat_ilink")
    assert plugin_metadata.tables, "插件表未注册，用例前提不成立"
    from app.config import settings
    db = tmp_path / "plugin_reconcile.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db.as_posix()}")
    return plugin_metadata


def _col_names(eng, tname):
    return {c["name"] for c in inspect(eng).get_columns(tname)}


def _col_types(eng, tname):
    return {c["name"]: str(c["type"]) for c in inspect(eng).get_columns(tname)}


def _index_names(eng, tname):
    return {ix["name"] for ix in inspect(eng).get_indexes(tname)}


def _index_signature(eng, tname):
    """索引签名集合 ``{(name, columns, unique)}``（唯一约束一并归一为签名）。

    同时并入 ``get_indexes`` 与 ``get_unique_constraints``：SQLite 反射下，ORM 的
    ``UniqueConstraint`` 落库为表级唯一约束（只出现在 get_unique_constraints，物理名
    ``sqlite_autoindex_*``），而 alembic 迁移用 ``op.create_index(..., unique=True)``
    建同名唯一索引（出现在 get_indexes）。二者 SQL 语义等价，若只取 get_indexes，
    会把「已收敛」误判成差异（如 douyin_viewed_notes 的
    ``uq_douyin_viewed_tenant_aweme``：pathA 是约束、pathB 是索引）。归一后仍能抓到
    真正的缺索引 / 多索引（索引名、列序、唯一性任一不同都会体现）。
    """
    insp = inspect(eng)
    sig = {
        (ix["name"], tuple(ix["column_names"]), bool(ix["unique"]))
        for ix in insp.get_indexes(tname)
    }
    for uq in insp.get_unique_constraints(tname):
        sig.add((uq["name"], tuple(uq.get("column_names") or []), True))
    return sig


def _orm_index_names(tname):
    return {ix.name for ix in plugin_metadata.tables[tname].indexes if ix.name}


def _make_legacy_plugin_db(sync_url: str) -> None:
    """建全量插件表后剥掉 5 列 4 索引，模拟「整链 upgrade head 重放」出的旧抖音表。"""
    eng = create_engine(sync_url)
    try:
        plugin_metadata.create_all(eng, checkfirst=True)
        with eng.begin() as conn:
            for ix_name in LEGACY_MISSING_INDEXES:
                conn.execute(text(f"DROP INDEX IF EXISTS {ix_name}"))
            for tname, col in LEGACY_MISSING_COLUMNS:
                conn.execute(text(f"ALTER TABLE {tname} DROP COLUMN {col}"))
    finally:
        eng.dispose()


class _RecordingLogger:
    """记录 info/warning 调用的极简 logger 替身（断言「0 操作也留痕」）。"""

    def __init__(self):
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def info(self, msg, *args, **kwargs):
        self.infos.append(msg % args if args else str(msg))

    def warning(self, msg, *args, **kwargs):
        self.warnings.append(msg % args if args else str(msg))


def test_reconcile_heals_legacy_chain_replay(plugin_meta):
    """① 旧抖音表经 create_all+reconcile 后：缺列=0、缺索引=0。"""
    from app.db.migrate import _sync_url
    from app.plugins.registry import _ensure_plugin_tables_sync

    _make_legacy_plugin_db(_sync_url())

    # 先确认伪造成功（缺列/缺索引确实缺）
    eng = create_engine(_sync_url())
    assert "aweme_id" not in _col_names(eng, "douyin_comments")
    assert "ix_douyin_accounts_tenant_id" not in _index_names(eng, "douyin_accounts")
    eng.dispose()

    names = _ensure_plugin_tables_sync()  # create_all(checkfirst) + reconcile
    assert set(DOUYIN_TABLES) <= set(names)

    eng = create_engine(_sync_url())
    try:
        for tname, col in LEGACY_MISSING_COLUMNS:
            assert col in _col_names(eng, tname), f"{tname}.{col} 未补齐"
        for tname in DOUYIN_TABLES:
            missing_cols = {c.name for c in plugin_meta.tables[tname].columns} - _col_names(eng, tname)
            missing_idx = _orm_index_names(tname) - _index_names(eng, tname)
            assert missing_cols == set(), f"{tname} 缺列 {missing_cols}"
            assert missing_idx == set(), f"{tname} 缺索引 {missing_idx}"
            # 补回的 NOT NULL 后加列必须带 DEFAULT（SQLite ADD COLUMN 硬要求），且原数据不丢
    finally:
        eng.dispose()


def test_reconcile_noop_on_fresh_create_all(plugin_meta, monkeypatch):
    """② 新库 create_all 后 reconcile 为 0 操作；且必须留 info 日志。"""
    from app.db.migrate import _sync_url
    from app.plugins.registry import _ensure_plugin_tables_sync

    eng = create_engine(_sync_url())
    plugin_meta.create_all(eng, checkfirst=True)
    eng.dispose()

    rec = _RecordingLogger()
    monkeypatch.setattr(registry, "_logger", rec)
    _ensure_plugin_tables_sync()

    assert any("无缺失" in m for m in rec.infos), f"0 操作未留痕：{rec.infos}"
    assert rec.warnings == [], f"新库收敛不应告警：{rec.warnings}"


def test_reconcile_idempotent(plugin_meta, monkeypatch):
    """③ 幂等：连跑两次，第二次 0 操作，schema 前后一致。"""
    from app.db.migrate import _sync_url
    from app.plugins.registry import _ensure_plugin_tables_sync

    _make_legacy_plugin_db(_sync_url())
    _ensure_plugin_tables_sync()

    eng = create_engine(_sync_url())
    try:
        after1_cols = {t: _col_names(eng, t) for t in DOUYIN_TABLES}
        after1_idx = {t: _index_names(eng, t) for t in DOUYIN_TABLES}
    finally:
        eng.dispose()

    rec = _RecordingLogger()
    monkeypatch.setattr(registry, "_logger", rec)
    _ensure_plugin_tables_sync()
    assert any("无缺失" in m for m in rec.infos), f"第二次应为 0 操作：{rec.infos}"

    eng = create_engine(_sync_url())
    try:
        for t in DOUYIN_TABLES:
            assert _col_names(eng, t) == after1_cols[t]
            assert _index_names(eng, t) == after1_idx[t]
    finally:
        eng.dispose()


def test_reconcile_only_additive(plugin_meta):
    """④ 不 DROP 列 / 不改类型：收敛后列集合 ⊇ 旧集合，共有列类型完全一致。"""
    from app.db.migrate import _sync_url
    from app.plugins.registry import _reconcile_plugin_schema

    _make_legacy_plugin_db(_sync_url())

    eng = create_engine(_sync_url())
    try:
        before_cols = {t: _col_names(eng, t) for t in DOUYIN_TABLES}
        before_types = {t: _col_types(eng, t) for t in DOUYIN_TABLES}
        added_cols, added_idx = _reconcile_plugin_schema(eng)
        after_cols = {t: _col_names(eng, t) for t in DOUYIN_TABLES}
        after_types = {t: _col_types(eng, t) for t in DOUYIN_TABLES}
    finally:
        eng.dispose()

    assert added_cols and added_idx, "伪造的旧表应触发补列补索引"
    for t in DOUYIN_TABLES:
        assert before_cols[t] <= after_cols[t], f"{t} 收敛后丢列：{before_cols[t] - after_cols[t]}"
        for col in before_cols[t]:
            assert after_types[t][col] == before_types[t][col], f"{t}.{col} 列类型被改"
    # 旧列一个都没少：值仍在（表未被重建）
    eng = create_engine(_sync_url())
    try:
        with eng.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM douyin_accounts")).scalar() == 0
    finally:
        eng.dispose()


def test_py_default_literal_scalar_only():
    """⑤ bool 先于 int/float；非标量 / 无默认 → None，绝不抛错。"""
    from sqlalchemy import Boolean, Column, Integer, String, text as sa_text

    from app.plugins.registry import _py_default_literal

    assert _py_default_literal(Column("a", String, default="image")) == "'image'"
    assert _py_default_literal(Column("b", String, default="it's")) == "'it''s'"
    assert _py_default_literal(Column("c", Boolean, default=False)) == "0"
    assert _py_default_literal(Column("d", Boolean, default=True)) == "1"
    assert _py_default_literal(Column("e", Integer, default=7)) == "7"
    assert _py_default_literal(Column("f", Integer, default=None)) is None
    assert _py_default_literal(Column("g", Integer, default=sa_text("1+1"))) is None  # 非标量
    assert _py_default_literal(object()) is None  # 取不到默认不抛错


def test_plugin_schema_converges_after_full_chain_replay(monkeypatch, tmp_path):
    """⑥ 两路收敛金标准：裸库整链重放 + 启动期 ensure ≡ 纯 ORM create_all。

    pathA：``plugin_metadata.create_all()`` 直接建出的当前 ORM schema；
    pathB：**裸库真跑** ``alembic upgrade head`` 建出 baseline 旧插件表（douyin 表由版本链
           建、wechat 表不存在），再走**真实启动路径** ``_ensure_plugin_tables_sync()``
           （create_all 建缺表 + reconcile 补列补索引）收敛；
    然后对 douyin 5 + wechat 2 逐表比对「列集合」与「索引签名 ``(name, columns, unique)``」。

    白名单（已接受的差异，本用例**不**比）：``douyin_accounts.bot_account_id /
    bot_label`` 在旧链上为 nullable + server_default、ORM 为 NOT NULL —— 按 P3-1 §6
    拍板③「不重建表收紧 NOT NULL」，属已接受取舍。NOT NULL / server_default 的松紧
    需要 batch 重建表才能改，且不影响读写列与查询索引，故本用例只比结构骨架
    （列集合 + 索引签名）；若未来这两列参与索引或查询列变化，仍会被列集合/索引签名捕获。
    """
    from alembic import command

    from app.config import settings
    from app.db.migrate import _alembic_config

    # 前置：加载两渠道插件注册 plugin_metadata；重复加载必须幂等（表集合不变）
    registry.load_plugin_dir(registry.EXAMPLE_DIR / "douyin_mcp")
    registry.load_plugin_dir(registry.EXAMPLE_DIR / "wechat_ilink")
    loaded_once = sorted(plugin_metadata.tables.keys())
    registry.load_plugin_dir(registry.EXAMPLE_DIR / "douyin_mcp")
    registry.load_plugin_dir(registry.EXAMPLE_DIR / "wechat_ilink")
    assert sorted(plugin_metadata.tables.keys()) == loaded_once, "插件重复加载不幂等"
    assert set(PLUGIN_TABLES) <= set(plugin_metadata.tables.keys()), (
        f"插件表注册不全，缺 {set(PLUGIN_TABLES) - set(plugin_metadata.tables)}"
    )

    # pathA：纯 ORM create_all（当前 schema 基准）
    db_a = tmp_path / "plugin_path_a.db"
    eng_a = create_engine(f"sqlite:///{db_a.as_posix()}")
    try:
        plugin_metadata.create_all(eng_a, checkfirst=True)
    finally:
        eng_a.dispose()

    # pathB：裸库真跑 alembic 整链 → 真实启动路径 ensure（create_all + reconcile）
    db_b = tmp_path / "plugin_path_b.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_b.as_posix()}")
    command.upgrade(_alembic_config(), "head")
    registry._ensure_plugin_tables_sync()

    eng_a = create_engine(f"sqlite:///{db_a.as_posix()}")
    eng_b = create_engine(f"sqlite:///{db_b.as_posix()}")
    try:
        problems: list[str] = []
        for tname in PLUGIN_TABLES:
            cols_a, cols_b = _col_names(eng_a, tname), _col_names(eng_b, tname)
            if cols_a != cols_b:
                problems.append(
                    f"{tname} 列集合两路不一致：仅 pathA={sorted(cols_a - cols_b)}，"
                    f"仅 pathB={sorted(cols_b - cols_a)}"
                )
            idx_a, idx_b = _index_signature(eng_a, tname), _index_signature(eng_b, tname)
            if idx_a != idx_b:
                problems.append(
                    f"{tname} 索引签名两路不一致：仅 pathA={sorted(idx_a - idx_b)}，"
                    f"仅 pathB={sorted(idx_b - idx_a)}"
                )
        assert not problems, "整链重放 + 启动期 ensure 未收敛到纯 create_all：\n" + "\n".join(problems)
    finally:
        eng_a.dispose()
        eng_b.dispose()
