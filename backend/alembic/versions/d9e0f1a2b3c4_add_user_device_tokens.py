"""add user_device_tokens for FCM push

Revision ID: d9e0f1a2b3c4
Revises: e7f8a9b0c1d2
Create Date: 2026-08-28 00:00:00.000000

FCM 离线推送：新增 user_device_tokens 表存储设备推送 token。
- 每用户多设备，每设备一行；UNIQUE(user_id, device_id, push_provider)。
- push_provider 当前仅 fcm，预留 apns。
- 使用 has_table 守卫：存量库若已被 init_db() create_all 补建则跳过。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e0f1a2b3c4'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('user_device_tokens'):
        op.create_table(
            'user_device_tokens',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('device_id', sa.String(length=64), nullable=False),
            sa.Column('platform', sa.String(length=16), nullable=False),
            sa.Column('push_provider', sa.String(length=16), nullable=False),
            sa.Column('push_token', sa.String(length=512), nullable=False),
            sa.Column('app_version', sa.String(length=32), nullable=True),
            sa.Column('last_seen_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.UniqueConstraint('user_id', 'device_id', 'push_provider', name='uq_device_token_user_device_provider'),
        )
        op.create_index('ix_user_device_tokens_user_id', 'user_device_tokens', ['user_id'])
        op.create_index('ix_user_device_tokens_token', 'user_device_tokens', ['push_token'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('user_device_tokens'):
        op.drop_index('ix_user_device_tokens_token', table_name='user_device_tokens')
        op.drop_index('ix_user_device_tokens_user_id', table_name='user_device_tokens')
        op.drop_table('user_device_tokens')
