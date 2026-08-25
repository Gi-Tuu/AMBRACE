"""add talkativeness and muted (AMBRACE 群聊调度 L1 + 群控)

Revision ID: b4de26e0a171
Revises: 3a6d4e8f2c91
Create Date: 2026-08-25 19:24:01.693726

L1 群聊调度升级（数据层）：
- ai_characters 增加 talkativeness（Integer 0-100，NULL=未设置按性格推断）
  与 talkativeness_locked（Boolean，1=AI 不可自主调整）；
- chat_group_members 增加 muted（Boolean，静音角色不参与自动选择，被 @ 仍强制回）。

说明：autogenerate 曾因存量库与 ORM 的 schema 漂移输出大量 constraint/type 噪音，本迁移
已手工收敛为「只加列、不删列、不改既有列」——BASE.metadata 的 91 张表不变。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4de26e0a171'
down_revision: Union[str, None] = '3a6d4e8f2c91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 只加列：talkativeness（可空，NULL=推断）；talkativeness_locked / muted（非空，默认 0）
    with op.batch_alter_table('ai_characters', schema=None) as batch_op:
        batch_op.add_column(sa.Column('talkativeness', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('talkativeness_locked', sa.Boolean(), nullable=False, server_default=sa.text('0')))

    with op.batch_alter_table('chat_group_members', schema=None) as batch_op:
        batch_op.add_column(sa.Column('muted', sa.Boolean(), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    with op.batch_alter_table('ai_characters', schema=None) as batch_op:
        batch_op.drop_column('talkativeness_locked')
        batch_op.drop_column('talkativeness')

    with op.batch_alter_table('chat_group_members', schema=None) as batch_op:
        batch_op.drop_column('muted')
