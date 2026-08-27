"""add account_invites (#68 P3 账号关联)

Revision ID: e7f8a9b0c1d2
Revises: c8d9e0f1a2b3
Create Date: 2026-08-29 00:00:00.000000

#68 账号体系 × API 配置整合 P3（账号关联）：
- 新增 account_invites 表（受邀码：code 8 位大写 hex 唯一 / creator_id / expires_at /
  used_by / used_at / created_at），users.parent_id 已在 c8d9e0f1a2b3 添加，不重复加。

使用 has_table 守卫：存量库若已被 init_db() create_all 补建则跳过，避免重名冲突。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'c8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('account_invites'):
        op.create_table(
            'account_invites',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('code', sa.String(length=8), nullable=False),
            sa.Column('creator_id', sa.Integer(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('used_by', sa.Integer(), nullable=True),
            sa.Column('used_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('code', name='uq_account_invites_code'),
            sa.ForeignKeyConstraint(['creator_id'], ['users.id']),
        )
        op.create_index('ix_account_invites_creator_id', 'account_invites', ['creator_id'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('account_invites'):
        op.drop_table('account_invites')
