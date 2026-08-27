"""add user_llm_configs + ai_characters.user_llm_config_id + users.parent_id (#68 P0)

Revision ID: c8d9e0f1a2b3
Revises: a1b2c3d4e5f6
Create Date: 2026-08-28 00:00:00.000000

#68 账号体系 × API 配置整合 P0：
- 新增 user_llm_configs 表（用户多 LLM 配置：name/base_url/api_key/model/provider/enabled/
  is_default/shared_with_subs，UNIQUE(user_id,name)）；
- ai_characters 加 user_llm_config_id（角色绑定 LLM 配置，可空）；
- users 加 parent_id（主账号关联，NULL=独立主账号；P3 受邀码关联完整启用，P0-P2 仅用于共享配置判定）。

使用 has_table / 列存在守卫：存量库若已被 init_db() create_all 补建则跳过，避免重名冲突。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    try:
        return column in {c["name"] for c in inspector.get_columns(table)}
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('user_llm_configs'):
        op.create_table(
            'user_llm_configs',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('base_url', sa.String(length=255), nullable=True),
            sa.Column('api_key', sa.String(length=500), nullable=True),
            sa.Column('model', sa.String(length=80), nullable=True),
            sa.Column('provider', sa.String(length=30), nullable=True),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')),
            sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.text('0')),
            sa.Column('shared_with_subs', sa.Boolean(), nullable=False, server_default=sa.text('0')),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.UniqueConstraint('user_id', 'name', name='uq_user_llm_user_name'),
        )
        op.create_index('ix_user_llm_configs_user_id', 'user_llm_configs', ['user_id'])

    if not _has_column(bind, 'ai_characters', 'user_llm_config_id'):
        with op.batch_alter_table('ai_characters', schema=None) as batch_op:
            batch_op.add_column(sa.Column('user_llm_config_id', sa.Integer(), nullable=True))

    if not _has_column(bind, 'users', 'parent_id'):
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.add_column(sa.Column('parent_id', sa.Integer(), nullable=True))
            batch_op.create_index('ix_users_parent_id', ['parent_id'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_column(bind, 'users', 'parent_id'):
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.drop_index('ix_users_parent_id')
            batch_op.drop_column('parent_id')
    if _has_column(bind, 'ai_characters', 'user_llm_config_id'):
        with op.batch_alter_table('ai_characters', schema=None) as batch_op:
            batch_op.drop_column('user_llm_config_id')
    if inspector.has_table('user_llm_configs'):
        op.drop_table('user_llm_configs')
