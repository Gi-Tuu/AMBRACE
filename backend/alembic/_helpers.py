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
    - 不重建表、不改数据、无需关闭 ``PRAGMA foreign_keys``。

    返回本次新建的 ``表.索引名`` 清单（便于迁移日志核对）。
    """
    bind = op.get_bind()
    insp = sa.inspect(bind)
    db_tables = set(insp.get_table_names())
    created: list[str] = []
    for tname, table in base_metadata.tables.items():
        if tname not in db_tables:
            continue
        existing = {ix["name"] for ix in insp.get_indexes(tname)}
        for idx in table.indexes:  # 含 mapped_column(index=True) 的隐式 ix_ 与显式 Index
            if idx.name is None or idx.name in existing:
                continue
            op.create_index(
                idx.name, tname, [c.name for c in idx.columns], unique=bool(idx.unique)
            )
            created.append(f"{tname}.{idx.name}")
    return created
