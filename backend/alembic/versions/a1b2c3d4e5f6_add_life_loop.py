"""add life loop fields and life_followups + life_chat_intents

Revision ID: a1b2c3d4e5f6
Revises: a4b5c6d7e8f9
Create Date: 2026-08-26 12:00:00.000000

Life Loop v1.1（2026-08-26，docs/life-loop-code-plan.md）：
- life_states 加 3 字段：location / location_updated_at / current_room；
- 新增 life_followups（回聊缓冲）与 life_chat_intents（聊天驱动意图）两张表。
- 使用 has_table / 列存在守卫：存量库若已被 init_db() create_all 补建则跳过，避免重名冲突。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'a4b5c6d7e8f9'
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

    # life_states 补列（列存在守卫：init_db create_all 已建则跳过）
    if not _has_column(bind, 'life_states', 'location'):
        with op.batch_alter_table('life_states', schema=None) as batch_op:
            batch_op.add_column(sa.Column('location', sa.String(length=20), server_default='home', nullable=False))
    if not _has_column(bind, 'life_states', 'location_updated_at'):
        with op.batch_alter_table('life_states', schema=None) as batch_op:
            batch_op.add_column(sa.Column('location_updated_at', sa.DateTime(), nullable=True))
    if not _has_column(bind, 'life_states', 'current_room'):
        with op.batch_alter_table('life_states', schema=None) as batch_op:
            batch_op.add_column(sa.Column('current_room', sa.String(length=20), server_default='living', nullable=False))

    if not inspector.has_table('life_followups'):
        op.create_table('life_followups',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('character_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('summary', sa.String(length=300), nullable=False),
            sa.Column('action', sa.String(length=30), server_default='', nullable=False),
            sa.Column('memory_id', sa.Integer(), nullable=True),
            sa.Column('trigger_window', sa.String(length=20), server_default='next_online', nullable=False),
            sa.Column('not_before', sa.DateTime(), nullable=True),
            sa.Column('status', sa.String(length=12), server_default='pending', nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.Column('used_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['character_id'], ['ai_characters.id']),
        )
        op.create_index('ix_life_followups_character_id', 'life_followups', ['character_id'])
        op.create_index('ix_life_followups_user_id', 'life_followups', ['user_id'])

    if not inspector.has_table('life_chat_intents'):
        op.create_table('life_chat_intents',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('character_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('action_type', sa.String(length=30), nullable=False),
            sa.Column('horizon', sa.String(length=12), server_default='today', nullable=False),
            sa.Column('raw_text', sa.String(length=200), server_default='', nullable=False),
            sa.Column('priority', sa.Integer(), server_default='1', nullable=False),
            sa.Column('status', sa.String(length=12), server_default='pending', nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.Column('consumed_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['character_id'], ['ai_characters.id']),
        )
        op.create_index('ix_life_chat_intents_character_id', 'life_chat_intents', ['character_id'])
        op.create_index('ix_life_chat_intents_user_id', 'life_chat_intents', ['user_id'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ('life_chat_intents', 'life_followups'):
        if inspector.has_table(table):
            op.drop_table(table)
    for col in ('current_room', 'location_updated_at', 'location'):
        if _has_column(bind, 'life_states', col):
            with op.batch_alter_table('life_states', schema=None) as batch_op:
                batch_op.drop_column(col)
