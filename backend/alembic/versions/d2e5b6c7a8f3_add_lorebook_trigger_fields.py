"""add lorebook trigger fields (AMBRACE Lorebook 触发式注入进阶 L2 核心版)

Revision ID: d2e5b6c7a8f3
Revises: b4de26e0a171
Create Date: 2026-08-24 09:00:00.000000

L2 核心版：lorebook_entries 触发式注入引擎新增字段（只加列、不删列、不改既有列、
不新建表——BASE.metadata 的 91 张表不变）：
- is_regex（Boolean 默认 0）：关键词按正则解析（/pattern/flags 或裸 pattern），False=子串（向后兼容）；
- probability（Integer 默认 100）：0-100 命中后注入概率，100=必注入；
- inclusion_group（String(50) 默认 ''）：同组条目同轮只注入一条（取 updated_at 最新）；
- sticky_rounds（Integer 默认 0）：触发后持续注入 N 轮；
- cooldown_rounds（Integer 默认 0）：触发后 N 轮内不再注入。

默认值与 ORM（app/models/memory/lorebook.py）保持一致，保证 is_regex=False /
probability=100 / inclusion_group='' / sticky_rounds=0 / cooldown_rounds=0 时行为与现状
完全一致（向后兼容）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e5b6c7a8f3'
down_revision: Union[str, None] = 'b4de26e0a171'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 只加列：新字段全部给非空 + server_default，存量行回填为默认值（向后兼容）。
    with op.batch_alter_table('lorebook_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_regex', sa.Boolean(), nullable=False, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('probability', sa.Integer(), nullable=False, server_default=sa.text('100')))
        batch_op.add_column(sa.Column('inclusion_group', sa.String(length=50), nullable=False, server_default=sa.text("''")))
        batch_op.add_column(sa.Column('sticky_rounds', sa.Integer(), nullable=False, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('cooldown_rounds', sa.Integer(), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    with op.batch_alter_table('lorebook_entries', schema=None) as batch_op:
        batch_op.drop_column('cooldown_rounds')
        batch_op.drop_column('sticky_rounds')
        batch_op.drop_column('inclusion_group')
        batch_op.drop_column('probability')
        batch_op.drop_column('is_regex')
