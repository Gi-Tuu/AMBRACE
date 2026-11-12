# -*- coding: utf-8 -*-
"""Alembic 迁移公共辅助（v3.4.6 第四轮工程沉淀，2026-09-10）。

背景：SQLite 上 ``op.batch_alter_table(t, recreate="always", copy_from=<ORM Table>)`` 重建表时，
``mapped_column(index=True)`` 产生的**隐式单列索引**不会被自动还原（``__table_args__`` 里的显式
``Index(...)`` 因是 Table 对象的一等成员会随 copy_from 保留）。先例 ``c9d0e1f2a3b4`` 对 48 张表
重建即因此静默丢失 31 个索引；``d0a1b2c3d4e5`` 重建 3 张表后手列补回，又漏了
``group_memories.created_at``。

约定（迁移规范）：**凡 ``batch_alter_table(..., recreate="always", copy_from=...)``，
upgrade 末尾必须调用 ``ensure_indexes(op, Base.metadata)``**。已落库的 c9d0/d0a 不回改，由
``e1b2c3d4e5f6`` 补索引迁移一次性补齐；从此刻起新迁移遵守本约定。
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
