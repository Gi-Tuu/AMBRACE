# -*- coding: utf-8 -*-
"""批 G（2026-09-26）：小手机日历备注 / 备忘录加「完成状态」列。

Revision ID: a9c1e3f5b7d9
Revises: b3c4d5e6f7a8
Create Date: 2026-09-26

- calendar_notes、memo_notes 各新增 status VARCHAR(10) NOT NULL DEFAULT 'active'（值域 active / done）：
  供小手机「已完成 / 已过期」标记使用（用户侧勾选、AI 侧标记完成/重开、注入侧渲染标记）。
  过期不入库（只在注入渲染时按 note_date 判定），是否完成由人/角色显式置 done。

完全幂等（has_table + has_column 守卫，重复 upgrade 不报错；表不存在则跳过）；
downgrade 可逆（删该列）。全新库由 init_db 的 create_all 直建（模型已含该列），本迁移负责存量库补齐。
SQLite 加列走 op.add_column（与同类迁移 f4a5b6c7d8e9 一致，无需 recreate 整表）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9c1e3f5b7d9"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (表名, 列名)——写字面量，收尾回验两表都有该列
_TARGETS = (("calendar_notes", "status"), ("memo_notes", "status"))


def _has_table(bind, table: str) -> bool:
    try:
        return sa.inspect(bind).has_table(table)
    except Exception:
        return False


def _has_column(bind, table: str, column: str) -> bool:
    try:
        return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    for table, column in _TARGETS:
        if not _has_table(bind, table):
            continue  # 表不存在则跳过（幂等）
        if _has_column(bind, table, column):
            continue  # 列已存在则跳过（幂等）
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(column, sa.String(length=10), nullable=False,
                          server_default=sa.text("'active'"))
            )
    # 收尾回验：两表都必须在位且有该列，否则抛错中止，防止静默漂移
    for table, column in _TARGETS:
        if _has_table(bind, table) and not _has_column(bind, table, column):
            raise RuntimeError(f"升级后 {table} 仍缺列 {column}——中止，防止静默漂移")


def downgrade() -> None:
    bind = op.get_bind()
    for table, column in _TARGETS:
        if not _has_table(bind, table):
            continue  # 幂等：表本就不在
        if not _has_column(bind, table, column):
            continue  # 幂等：列已不存在
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column(column)
