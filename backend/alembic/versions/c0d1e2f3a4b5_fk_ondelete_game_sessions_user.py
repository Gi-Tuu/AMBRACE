# -*- coding: utf-8 -*-
"""P3-5：``game_sessions.user_id`` → users 外键补 ON DELETE CASCADE（2026-09-20）。

背景：``models/game/__init__.py`` 里同域较新表（game_content_overrides / game_stats /
game_achievements 的 user_id）均已声明 ``ondelete="CASCADE"``，只有建表最早的 game_sessions
漏写（=RESTRICT，删用户会被数据库阻塞）。本迁移把物理层与该声明对齐（模型改动为单一事实源）。

口径（沿用先例 d0a1b2c3d4e5 / f2b3c4d5e6f7 / c9d0e1f2a3b4）：
  * SQLite **不支持** ALTER / DROP 外键约束（``ALTER TABLE ... DROP CONSTRAINT`` 无实现），
    改外键只能「整表重建」：``op.batch_alter_table(..., recreate="always", copy_from=当前 ORM Table)``
    —— 建新表 → 按列 INSERT SELECT 复制 → 删旧表 → 改名回原表名。
  * copy_from 用 ``_rebuild_copy_table`` 只保留『DB 已存在』的列（先例 c9d0/d0a 的原函数），
    防止从基线整链重放时提前 SELECT 到不存在的列。
  * 重建前 ``PRAGMA foreign_keys=OFF``：旧表被 DROP 的瞬间，子表（game_players / game_events /
    game_memories / chat_group_messages）指向 game_sessions 的外键会短暂悬空；SQLite 外键按
    **表名**解析，改名回原表名后自动重新指向新表，子表自身的 ON DELETE CASCADE 声明不受影响。
  * 凡 batch recreate，末尾必须 ``ensure_indexes`` 兜底（alembic/_helpers.py 的迁移规范：
    batch 重建会丢 ``mapped_column(index=True)`` 的隐式单列索引）。

幂等：升级前读实际外键，已是 CASCADE 直接返回（新库 0 操作）；老库一次补齐；重复执行 0 操作。
"""
import importlib.util as _ilu
import os as _os

from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None

TABLE = "game_sessions"
FK_COLUMN = "user_id"
REFERRED_TABLE = "users"
TARGET_ONDELETE = "CASCADE"

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


def _actual_ondelete(conn) -> str:
    """读 ``game_sessions.user_id`` 外键的实际 ondelete；未声明返回 ""（SQLite 等价 NO ACTION）。"""
    for fk in sa_inspect(conn).get_foreign_keys(TABLE):
        if list(fk["constrained_columns"]) == [FK_COLUMN] and fk["referred_table"] == REFERRED_TABLE:
            return (fk["options"].get("ondelete") or "").upper()
    return ""


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
    got = _actual_ondelete(conn)
    if got == TARGET_ONDELETE:
        print(f"[{revision}] {TABLE}.{FK_COLUMN}→{REFERRED_TABLE} 已是 ON DELETE {TARGET_ONDELETE}，0 操作")
        return

    copy_t = _rebuild_copy_table(meta, conn, TABLE)
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table(TABLE, recreate="always", copy_from=copy_t):
            pass
    finally:
        op.execute("PRAGMA foreign_keys=ON")

    after = _actual_ondelete(conn)
    if after != TARGET_ONDELETE:
        raise RuntimeError(
            f"重建后 {TABLE}.{FK_COLUMN}→{REFERRED_TABLE} ondelete={after or 'NO ACTION'}，"
            f"未落到 {TARGET_ONDELETE}——中止，防止静默漂移"
        )
    # 迁移规范（alembic/_helpers.py）：凡 batch recreate，末尾必须 ensure_indexes 兜底
    created = _ensure_indexes_fn()(op, meta)
    if created:
        print(f"[{revision}] backfill indexes after rebuild: {created}")


def downgrade() -> None:
    # 与先例一致做 no-op：外键收紧（CASCADE ↔ RESTRICT）不可逆，回滚无法安全区分
    # 「本迁移改的」与「其它迁移改的」同一张表；如需回到迁移前状态，请从迁移前备份恢复库文件。
    # 注：不回滚模型声明（单一事实源），也不删除先例已建的索引。
    pass
