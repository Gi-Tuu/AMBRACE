# -*- coding: utf-8 -*-
"""P3-1 + P3-2：relational_drives / thought_pool / account_invites 三处子外键补 ON DELETE CASCADE（2026-10-01）。

用途
----
模型层（单一事实源）已声明 `ondelete="CASCADE"`，物理层却还是裸 FK（NO ACTION＝RESTRICT），
因为建表迁移当时没写：
  · relational_drives.character_id → ai_characters.id（a1c4e7f9b2d5 建表）
  · thought_pool.character_id      → ai_characters.id（b2d4f6a8c0e1 建表）
  · account_invites.creator_id     → users.id        （e7f8a9b0c1d2 建表）
应用层级联（``character_cascade.CHARACTER_DELETE_SPECS`` 含前两张、账号 purge 含第三张）是
**唯一**的清理路径，一旦某条路径漏跑就留下孤儿水位/念头/受邀码。本单只补 DB 级保险（纵深），
不改任何业务行为、不动数据。

影响面
------
- SQLite 不能 ALTER 外键 → 对这三张表 batch 重建（``recreate="always"`` + ``copy_from``）。
- 三张表都是**叶子表**（全库无其它表引用它们），重建不波及第四张表。
- 数据零改动：batch 走「建新表 → INSERT … SELECT 整表搬运 → drop 旧表 → rename」，
  行数与列值逐字节不变，唯一变化＝DDL 里的 FK 子句多了 ``ON DELETE CASCADE``。
- **索引不丢**（第四轮教训：batch recreate 会静默丢 ``index=True`` 隐式单列索引，c9d0 一次丢
  31 个）。本迁移靠三层保证，不靠「应该没丢」：
  ① ``__table_args__`` 显式 ``Index`` 随 copy_from 进新表 DDL；
  ② 重建后调 ``alembic/_helpers.ensure_indexes(op, Base.metadata)`` 逐表对比补齐；
  ③ 末尾对本单三张表做**硬回验**（FK ondelete / 索引名 / 唯一约束列组 / 列集合，任一不符
     直接 RuntimeError 中止）——实测证据即该回验跑通，见 tests 与本文件 downgrade 注释。

fresh 形态（空库整链 upgrade head）
-----------------------------------
``copy_from`` 由 ``_rebuild_copy_table()`` 生成：以当前 ORM metadata 为蓝本，但**只保留库里
已存在的列**（沿用先例 c9d0e1f2a3b4 的同款守卫）。本迁移此刻在链尾，但将来若再往链中/链后
插「加列」迁移，整链重放到这里也不会去 SELECT 当时还不存在的列。upgrade 里不 ``add_column``、
不改列、不假设某列一定存在——只重建表。

可逆性
------
downgrade 有意 **no-op**：FK 收紧属纵深增益，回退不还原 NO ACTION（先例 c9d0e1f2a3b4 /
d0a1b2c3d4e5 / e1b2c3d4e5f6 / f2b3c4d5e6f7 同样 no-op）。确需回到迁移前形态，从迁移前备份
恢复库文件。
"""
import importlib.util as _ilu
import os as _os

from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "c8f1a2b3d4e5"
down_revision = "c3d5e7f9a1b2"
branch_labels = None
depends_on = None

# alembic/_helpers.py 的绝对路径（version 模块由 alembic 按文件加载，没有包上下文，
# 不能 `from alembic import _helpers`——会撞已安装的 alembic 库；照 e1b2c3d4e5f6 先例）。
_HELPERS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "_helpers.py"
)

# ── 本单三处外键：(表, 外键列, 期望父表) ──
FK_TARGETS = [
    ("relational_drives", "character_id", "ai_characters"),
    ("thought_pool", "character_id", "ai_characters"),
    ("account_invites", "creator_id", "users"),
]

# ── 重建后必须仍在位的索引（含 __table_args__ 显式 Index 与 index=True 隐式单列索引）──
REQUIRED_INDEXES = {
    "relational_drives": {"ix_relational_drives_char_user_level"},
    "thought_pool": {"ix_thought_pool_char_user_status_salt"},
    "account_invites": {"ix_account_invites_creator_id"},
}

# ── 重建后必须仍在位的唯一约束（按列组建序比对，不依赖 SQLite 自动索引命名）──
REQUIRED_UNIQUE = {
    "relational_drives": ("character_id", "user_id", "drive_key"),
    "thought_pool": ("character_id", "user_id", "source_type", "source_ref", "text_hash"),
    "account_invites": ("code",),
}


def _ensure_indexes_fn():
    """按绝对路径加载 ``alembic/_helpers.py::ensure_indexes``（不改 sys.path、不受 cwd 影响）。"""
    spec = _ilu.spec_from_file_location("ambrace_alembic_helpers", _HELPERS_PATH)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ensure_indexes


def _rebuild_copy_table(meta, bind, tname):
    """batch recreate 的 copy_from：以 ORM metadata 为单一事实源，但只保留「库里已存在」的列。

    直接 ``copy_from=Base.metadata.tables[t]`` 会把「未来迁移才加的列」也提前 SELECT 出来，
    整链重放时该列尚不存在 → upgrade 失败（历史教训见 c9d0e1f2a3b4 同名函数）。逐列
    ``append_column`` 时列上的外键（含本单新增的 ondelete）与 ``unique=True`` 随列保留；
    表级约束与显式索引按列存在性过滤后补回。
    """
    from sqlalchemy import (
        ForeignKeyConstraint as _FK,
        Index as _Idx,
        MetaData as _MD,
        Table as _Tbl,
    )

    model_table = meta.tables[tname]
    existing = {c["name"] for c in sa_inspect(bind).get_columns(tname)}
    # 深拷贝，避免直接改全局 Base.metadata
    src = model_table.to_metadata(_MD())
    new_t = _Tbl(src.name, _MD(), **src.kwargs)
    for col in src.columns:
        if col.name in existing:
            new_t.append_column(col._copy())
    for const in src.constraints:
        if set(c.name for c in const.columns) > existing:
            continue  # 约束涉及的列库里还没有（同上，交由未来迁移负责）
        if isinstance(const, _FK):
            new_t.append_constraint(const._copy(target_table=new_t))
        elif not getattr(const, "_column_flag", False):
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


def _verify(bind, processed: list, cols_before: dict) -> None:
    """重建后硬回验：外键语义 / 列集合 / 索引 / 唯一约束，四项任一不符即中止。

    用裸 PRAGMA 而不是 ORM 反射，避免「反射不回填表级 FK options」这类口径坑
    （test_phone_snapshot_attribution.py 记过）。
    """
    problems: list[str] = []
    for tname, fk_col, parent in processed:
        # PRAGMA foreign_key_list 列序：id, seq, 父表, 本表列, 父表列, on_update, on_delete, match
        fk_rows = [tuple(r) for r in bind.exec_driver_sql(
            f'PRAGMA foreign_key_list("{tname}")').fetchall()]
        hit = [r for r in fk_rows if r[3] == fk_col]
        if not hit:
            problems.append(f"{tname}.{fk_col} 外键在重建后丢失")
        else:
            got_parent, got_action = hit[0][2], (hit[0][6] or "NO ACTION").upper()
            if got_parent != parent:
                problems.append(f"{tname}.{fk_col} 父表变成 {got_parent}（应为 {parent}）")
            if got_action != "CASCADE":
                problems.append(f"{tname}.{fk_col} on_delete={got_action}（应为 CASCADE）")
        cols_after = {r[1] for r in bind.exec_driver_sql(
            f'PRAGMA table_info("{tname}")').fetchall()}
        if cols_after != cols_before[tname]:
            problems.append(
                f"{tname} 列集合变化：少 {sorted(cols_before[tname] - cols_after)}"
                f" 多 {sorted(cols_after - cols_before[tname])}"
            )
        # PRAGMA index_list 行序：seq, name, unique, origin, partial
        idx_rows = [tuple(r) for r in bind.exec_driver_sql(
            f'PRAGMA index_list("{tname}")').fetchall()]
        names = {r[1] for r in idx_rows}
        lost = REQUIRED_INDEXES[tname] - names
        if lost:
            problems.append(f"{tname} 重建后丢索引：{sorted(lost)}（现有 {sorted(names)}）")
        unique_groups = set()
        for r in idx_rows:
            if not r[2]:
                continue
            # PRAGMA index_info 行序：seqno, cid, name
            unique_groups.add(tuple(x[2] for x in bind.exec_driver_sql(
                f'PRAGMA index_info("{r[1]}")').fetchall()))
        want = REQUIRED_UNIQUE[tname]
        if want not in unique_groups:
            problems.append(
                f"{tname} 重建后丢唯一约束：{want}（现有 unique 列组 {sorted(unique_groups)}）"
            )
    if problems:
        raise RuntimeError(
            "FK CASCADE 迁移后回验失败——中止，防止静默漂移：\n  " + "\n  ".join(problems)
        )


def _actual_ondelete(bind, tname: str, fk_col: str) -> str:
    """读该表该列当前的 on_delete（裸 PRAGMA 口径；表/列缺失一律返回空串）。"""
    try:
        rows = [tuple(r) for r in bind.exec_driver_sql(
            f'PRAGMA foreign_key_list("{tname}")').fetchall()]
    except Exception:
        return ""
    hit = [r for r in rows if r[3] == fk_col]
    return (hit[0][6] or "").upper() if hit else ""


def upgrade() -> None:
    import app.models  # noqa: F401  确保全部模型注册进 Base.metadata
    from app.models._all import Base

    meta = Base.metadata
    bind = op.get_bind()
    cols_before: dict = {}
    processed: list = []
    for tname, fk_col, _parent in FK_TARGETS:
        if tname not in meta.tables:
            raise RuntimeError(f"ORM metadata 缺表 {tname}——中止，防止重建丢列")
        # 既有约定（c0d1e2f3a4b5 / d1e2f3a4b5c6 / d7e8f9a0b1c2 同款）：库中无该表
        # ＝该库没走到建表这一步，交由建表路径按当前模型建，本迁移 0 操作。
        # （不 raise：只含部分表的库（如测试里“只有 plugins 表 + 中间版本号”）整链重放不应该炸）
        if not sa_inspect(bind).has_table(tname):
            print(f"[{revision}] 库中无 {tname}（交由建表路径按当前模型建），0 操作")
            continue
        if _actual_ondelete(bind, tname, fk_col) == "CASCADE":
            print(f"[{revision}] {tname}.{fk_col} 已是 ON DELETE CASCADE，0 操作")
            continue
        cols_before[tname] = {c["name"] for c in sa_inspect(bind).get_columns(tname)}
        processed.append((tname, fk_col, _parent))

    if not processed:
        # 全部目标表都不可处理（缺表 / 已是 CASCADE）⇒ 不做任何重建，也不调 ensure_indexes
        return

    op.execute("PRAGMA foreign_keys=OFF")
    try:
        for tname in cols_before:
            copy_t = _rebuild_copy_table(meta, bind, tname)
            with op.batch_alter_table(tname, recreate="always", copy_from=copy_t):
                pass
    finally:
        op.execute("PRAGMA foreign_keys=ON")

    # 凡 recreate="always" + copy_from，末尾必须 ensure_indexes 兜底（迁移规范，_helpers 头部）
    created = _ensure_indexes_fn()(op, meta)
    if created:
        print(f"[fk_ondelete_p3_1_p3_2] 重建后补回索引: {created}")

    _verify(bind, processed, cols_before)


def downgrade() -> None:
    # 纵深增益（DB 级级联保险）不可逆，回退有意 no-op：见文件头「可逆性」。
    pass
