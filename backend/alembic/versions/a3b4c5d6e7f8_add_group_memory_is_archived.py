# -*- coding: utf-8 -*-
"""#72 PR-C P5 群记忆日终合并收敛：group_memories 加 is_archived 归档列

Revision ID: a3b4c5d6e7f8
Revises: e2f3a4b5c6d7
Create Date: 2026-09-16

- group_memories 新增 is_archived BOOLEAN NOT NULL DEFAULT 0（归档标记）：
  日终合并收敛（compact_group_memories）把 >K 天的旧事件按群拼成 1 条 system 摘要，
  旧事件置 is_archived=1（留痕不物理删，仍可检索/回忆）；摘要行本身 is_archived=0（活跃、可注入）。
  注入侧 recall_group_longterm 过滤 is_archived=0，归档行不再注入。

完全幂等（has_column 守卫，重复 upgrade 不报错）；downgrade 可逆（删该列）。
全新库由 init_db 的 create_all 直建（模型已含该列），本迁移负责存量库补齐。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def _has_column(bind, table: str, column: str) -> bool:
    try:
        return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "group_memories", "is_archived"):
        return  # 幂等：列已存在则跳过
    with op.batch_alter_table("group_memories", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "group_memories", "is_archived"):
        return  # 幂等：列已不存在则跳过
    with op.batch_alter_table("group_memories", schema=None) as batch_op:
        batch_op.drop_column("is_archived")
