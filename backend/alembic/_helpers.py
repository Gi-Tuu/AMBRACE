# -*- coding: utf-8 -*-
"""Alembic 迁移公共辅助（v3.4.6 第四轮工程沉淀，2026-09-10）。

背景：SQLite 上 ``op.batch_alter_table(t, recreate="always", copy_from=<ORM Table>)`` 重建表时，
``mapped_column(index=True)`` 产生的**隐式单列索引**不会被自动还原（``__table_args__`` 里的显式
``Index(...)`` 因是 Table 对象的一等成员会随 copy_from 保留）。先例 ``c9d0e1f2a3b4`` 对 48 张表
重建即因此静默丢失 31 个索引；``d0a1b2c3d4e5`` 重建 3 张表后手列补回，又漏了
``group_memories.created_at``。

约定一（索引）：**凡 ``batch_alter_table(..., recreate="always", copy_from=...)``，
upgrade 末尾必须调用 ``ensure_indexes(op, Base.metadata)``**。已落库的 c9d0/d0a 不回改，由
``e1b2c3d4e5f6`` 补索引迁移一次性补齐；从此刻起新迁移遵守本约定。

约定二（copy_from，2026-11-23）：凡 ``copy_from=...`` **必须**用
``orm_copy_intersecting(bind, Base.metadata, 表名)`` 构造副本，禁止直接传
``Base.metadata.tables[表名]``——链尾迁移新增的列会让「从空库整链重放」在链中段
``no such column`` 失败（2026-11-22 ``chat_group_messages.file_url`` 事故）。
"""
from __future__ import annotations

import sqlalchemy as sa


def ensure_indexes(op, base_metadata) -> list[str]:
    """对比 ORM metadata 与实际库，补齐所有缺失索引（含隐式列索引），幂等。

    - 遍历 ``base_metadata.tables``：库里不存在的表直接跳过（该表交由建表迁移负责，
      此处 ``op.create_index`` 会因表缺失报错）；
    - 每张表用 ``sa.inspect`` 的实际索引名集合做差集，按 metadata 里的
      索引名 / 列 / unique 重建，缺啥补啥、重复执行 0 操作；
    - **历史 metadata 是前瞻的**：ORM 已可能声明尚未加入真实表的列及其索引
      （例如 ``e1b2c3d4e5f6_backfill_missing_indexes`` 运行时 ``memories`` 尚无
      ``write_key`` 列，但最新 metadata 已声明 ``ix_memories_write_key``；真正加列
      发生在后续的 ``c4e6f8a0b2d4_memory_evidence_write_key``）。只补建所有列已真实
      存在于库表的索引；列尚未存在的未来索引在此跳过，不报错、不尝试
      ``CREATE INDEX``，交由对应后续迁移创建；
    - 不重建表、不改数据、无需关闭 ``PRAGMA foreign_keys``。
    - 真正的 ``op.create_index`` DDL 失败（如列存在但 DDL 本身被拒绝）不静默吞掉，
      异常自然透传。

    返回本次新建的 ``表.索引名`` 清单（便于迁移日志核对）。
    """
    bind = op.get_bind()
    insp = sa.inspect(bind)
    db_tables = set(insp.get_table_names())
    created: list[str] = []
    for tname, table in base_metadata.tables.items():
        if tname not in db_tables:
            continue
        # 真实 schema 的实际列集：用于判断索引引用的列是否已存在于库表。
        db_columns = {col["name"] for col in insp.get_columns(tname)}
        existing = {ix["name"] for ix in insp.get_indexes(tname)}
        for idx in table.indexes:  # 含 mapped_column(index=True) 的隐式 ix_ 与显式 Index
            if idx.name is None or idx.name in existing:
                continue
            # 索引引用的全部列必须已在真实表中存在，否则跳过（未来列索引由后续迁移负责）。
            if not all(c.name in db_columns for c in idx.columns):
                continue
            op.create_index(
                idx.name, tname, [c.name for c in idx.columns], unique=bool(idx.unique)
            )
            created.append(f"{tname}.{idx.name}")
    return created


def orm_copy_intersecting(bind, base_metadata, table_name: str):
    """构造 ``batch_alter_table(copy_from=...)`` 用的表副本：只保留「该链点 DB 表已存在」的列。

    背景（2026-11-23 整链重放事故）：``copy_from=Base.metadata.tables[t]`` 取的是**当前**
    ORM 元数据；从空库整链重放走到链中段的该迁移时，DB 表还没有链尾迁移才添加的列，
    alembic 生成的 ``INSERT INTO _alembic_tmp_x (…, 新列) SELECT …, x.新列 FROM x``
    会直接 ``no such column`` 失败（2026-11-22 的 ``chat_group_messages.file_url`` 即此）。

    - 列：按 DB 实际列集过滤；列上的外键 / server_default / 主键标记随列保留；
    - 表级约束（组合 FK / UNIQUE / CHECK）：仅当涉及列全部存在时保留；
    - 索引：同上（列不齐的索引交由后续迁移创建，本次不建）。

    对「已升级到该迁移之后」的存量库无行为变化——未来列此时并不存在于 DB，
    过滤结果就是该链点的全列集，重建语义与原先一致。
    """
    model_table = base_metadata.tables[table_name]
    existing = {c["name"] for c in sa.inspect(bind).get_columns(table_name)}
    src = model_table.to_metadata(sa.MetaData())
    new_t = sa.Table(src.name, sa.MetaData(), **src.kwargs)
    for col in src.columns:
        if col.name in existing:
            new_t.append_column(col._copy())
    for const in src.constraints:
        if not {c.name for c in const.columns} <= existing:
            continue
        if isinstance(const, sa.ForeignKeyConstraint):
            new_t.append_constraint(const._copy(target_table=new_t))
        elif not getattr(const, "_column_flag", False):
            new_t.append_constraint(const._copy(target_table=new_t))
    for idx in src.indexes:
        if {c.name for c in idx.columns} <= existing:
            sa.Index(
                idx.name,
                *[new_t.c[c.name] for c in idx.columns],
                unique=idx.unique,
                **idx.kwargs,
            )
    return new_t
