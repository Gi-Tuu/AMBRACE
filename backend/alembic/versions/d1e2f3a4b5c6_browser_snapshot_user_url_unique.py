# -*- coding: utf-8 -*-
"""A2 后续批次：``browser_snapshots`` 唯一键由「url 全局唯一」改为「(user_id, url) 联合唯一」。

背景：``models/user/__init__.py`` 的 ``BrowserSnapshot.url`` 原为 ``unique=True``（全局唯一），
两个账号浏览同一网址时只能存下一行 —— 插件侧只能靠「该 url 已属他人 → 跳过不覆盖」兜底
（A2 M0-1 刻意不动 schema，本迁移收掉这笔账）。改为联合唯一后，同一网址各账号各存一行。

口径（沿用先例 c9d0e1f2a3b4 / d0a1b2c3d4e5 / c0d1e2f3a4b5）：
  * SQLite **不支持** ALTER / DROP 约束（``ALTER TABLE ... DROP CONSTRAINT`` 无实现），
    改唯一键只能「整表重建」：``op.batch_alter_table(..., recreate="always", copy_from=当前 ORM Table)``
    —— 建新表 → 按列 INSERT SELECT 复制 → 删旧表 → 改名回原表名。
  * copy_from 用 ``_rebuild_copy_table`` 只保留『DB 已存在』的列（先例 c9d0/d0a 的原函数），
    防止从基线整链重放时提前 SELECT 到不存在的列。
  * 重建前 ``PRAGMA foreign_keys=OFF``：本表无外键（既不被引用也不引用），DROP 瞬间不牵连子表；
    沿用先例口径统一关-开，避免重建期间任何触发式外键检查干扰 INSERT SELECT。
  * 方向安全：唯一键由「url 全局」放宽到「(user_id,url)」，旧数据本就满足新约束，
    无需去重、不可能因重建产生冲突行。
  * 凡 batch recreate，末尾必须 ``ensure_indexes`` 兜底（alembic/_helpers.py 的迁移规范：
    batch 重建会丢 ``mapped_column(index=True)`` 的隐式单列索引）。

幂等：升级前读实际唯一约束，已是 (user_id,url) 且 url 单列唯一已消失则直接返回（新库 0 操作）；
老库一次补齐；重复执行 0 操作。
"""
import importlib.util as _ilu
import os as _os

from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None

TABLE = "browser_snapshots"
TARGET_COLUMNS = frozenset({"user_id", "url"})
LEGACY_COLUMNS = frozenset({"url"})

# alembic/_helpers.py 的绝对路径（version 模块由 alembic 按文件加载，没有包上下文）。
_HELPERS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "_helpers.py"
)


def _ensure_indexes_fn():
    """按绝对路径加载 alembic/_helpers.py（照 f2b3c4d5e6f7 先例，不能 ``from alembic import
    _helpers``——会撞已安装的 alembic 库）。"""
    spec = _ilu.spec_from_file_location("ambrace_alembic_helpers", _HELPERS_PATH)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ensure_indexes


def _rebuild_copy_table(meta, conn, tname):
    """batch recreate 的 copy_from（逐字沿用先例 c9d0e1f2a3b4 / d0a1b2c3d4e5）：以当前 ORM
    metadata 为单一事实源（to_metadata 深拷贝列/列级外键/表级约束/索引，含 ondelete），但只保留
    『DB 已存在』的列——避免『未来迁移新增的列』被提前 SELECT，导致从基线 alembic upgrade head 失败。
    """
    from sqlalchemy import (
        ForeignKeyConstraint as _FK,
        Index as _Idx,
        MetaData as _MD,
        Table as _Tbl,
    )

    model_table = meta.tables[tname]
    existing = {c["name"] for c in sa_inspect(conn).get_columns(tname)}
    # 深拷贝，避免直接改全局 Base.metadata；逐列 append（列级外键随列保留）再补回表级约束/索引
    src = model_table.to_metadata(_MD())
    new_t = _Tbl(src.name, _MD(), **src.kwargs)
    for col in src.columns:
        if col.name in existing:
            new_t.append_column(col._copy())
    for const in src.constraints:
        if isinstance(const, _FK):
            if set(c.name for c in const.columns) <= existing:
                new_t.append_constraint(const._copy(target_table=new_t))
        elif not getattr(const, "_column_flag", False):
            if set(c.name for c in const.columns) <= existing:
                new_t.append_constraint(const._copy(target_table=new_t))
    for idx in src.indexes:
        if set(c.name for c in idx.columns) <= existing:
            _Idx(
                idx.name,
                unique=idx.unique,
                *[new_t.c[c.name] for c in idx.columns],
                **idx.kwargs,
            )
    return new_t


def _unique_sets(conn) -> set[frozenset]:
    """库里 ``browser_snapshots`` 实际存在的 UNIQUE 约束列集合（SQLite autoindex 亦可读）。"""
    insp = sa_inspect(conn)
    return {frozenset(uc["column_names"]) for uc in insp.get_unique_constraints(TABLE)}


def _has(conn, cols: frozenset) -> bool:
    return cols in _unique_sets(conn)


def upgrade() -> None:
    import app.models  # noqa: F401  确保全部模型注册到 Base.metadata
    from app.models._all import Base

    meta = Base.metadata
    conn = op.get_bind()
    if TABLE not in meta.tables:
        raise RuntimeError(f"ORM metadata 缺表 {TABLE}——中止，防止重建丢列")
    if not sa_inspect(conn).has_table(TABLE):
        print(f"[{revision}] 库中无 {TABLE}（交由建表路径按当前模型建），0 操作")
        return
    if _has(conn, TARGET_COLUMNS) and not _has(conn, LEGACY_COLUMNS):
        print(f"[{revision}] {TABLE} 已是 UNIQUE(user_id,url) 且无 url 单列唯一，0 操作")
        return

    copy_t = _rebuild_copy_table(meta, conn, TABLE)
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table(TABLE, recreate="always", copy_from=copy_t):
            pass
    finally:
        op.execute("PRAGMA foreign_keys=ON")

    sets_after = _unique_sets(conn)
    if TARGET_COLUMNS not in sets_after:
        raise RuntimeError(
            f"重建后 {TABLE} 缺 UNIQUE(user_id,url)，实际={sorted(sorted(s) for s in sets_after)}"
            "——中止，防止静默漂移"
        )
    if LEGACY_COLUMNS in sets_after:
        raise RuntimeError(
            f"重建后 {TABLE} 仍存在 url 单列 UNIQUE（会让多账号同网址再次互斥），"
            f"实际={sorted(sorted(s) for s in sets_after)}——中止"
        )
    # 迁移规范（alembic/_helpers.py）：凡 batch recreate，末尾必须 ensure_indexes 兜底
    created = _ensure_indexes_fn()(op, meta)
    if created:
        print(f"[{revision}] backfill indexes after rebuild: {created}")


def downgrade() -> None:
    # 与先例一致做 no-op：唯一键放宽（url 全局唯一 ↔ (user_id,url) 联合唯一）不可逆——一旦两个账号
    # 已各存同一网址，回滚成 url 单列唯一必然撞约束，只能靠「按时间保留一行」删数据，属破坏性回滚。
    # 如需回到迁移前状态，请从迁移前备份恢复库文件。
    # 注：不回滚模型声明（单一事实源），也不删除先例已建的索引。
    pass
