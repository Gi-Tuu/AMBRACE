"""add game tables + chat_group_messages game fields (群聊游戏 Phase 1)

Revision ID: a4b5c6d7e8f9
Revises: 1edb666e9762
Create Date: 2026-08-26 00:00:00.000000

群聊游戏 Phase 1（docs/group-chat-games-plan.md v1.1）：
- 新增 game_sessions / game_players / game_events / game_memories 四张表（游戏记忆逻辑隔离，
  不进主记忆向量检索；只能通过 game_session_id 显式查询）；
- chat_group_messages 加 msg_type（normal/game_event/game_say）+ game_session_id，
  群聊记忆/群聊上下文注入跳过非 normal 消息。
- 使用 has_table / 列存在守卫：存量库若已被 init_db() create_all 补建则跳过，避免重名冲突。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, None] = '1edb666e9762'
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

    if not inspector.has_table('game_sessions'):
        op.create_table('game_sessions',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('group_id', sa.Integer(), nullable=True),
            sa.Column('game_type', sa.String(length=30), nullable=False),
            sa.Column('player_mode', sa.String(length=10), nullable=False),
            sa.Column('status', sa.String(length=12), nullable=False),
            sa.Column('round', sa.Integer(), nullable=False),
            sa.Column('phase', sa.String(length=30), nullable=False),
            sa.Column('config_json', sa.Text(), nullable=False),
            sa.Column('state_json', sa.Text(), nullable=False),
            sa.Column('winner_side', sa.String(length=20), nullable=True),
            sa.Column('trigger', sa.String(length=20), nullable=False),
            sa.Column('archive_json', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.Column('started_at', sa.DateTime(), nullable=True),
            sa.Column('finished_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.ForeignKeyConstraint(['group_id'], ['chat_groups.id']),
        )
        op.create_index('ix_game_sessions_user_id', 'game_sessions', ['user_id'])
        op.create_index('ix_game_sessions_game_type', 'game_sessions', ['game_type'])

    if not inspector.has_table('game_players'):
        op.create_table('game_players',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('session_id', sa.Integer(), nullable=False),
            sa.Column('player_type', sa.String(length=10), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('character_id', sa.Integer(), nullable=True),
            sa.Column('seat', sa.Integer(), nullable=False),
            sa.Column('role', sa.String(length=20), nullable=False),
            sa.Column('is_spectator', sa.Boolean(), nullable=False),
            sa.Column('alive', sa.Boolean(), nullable=False),
            sa.Column('score', sa.Integer(), nullable=False),
            sa.Column('private_json', sa.Text(), nullable=False),
            sa.Column('joined_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['session_id'], ['game_sessions.id']),
            sa.ForeignKeyConstraint(['character_id'], ['ai_characters.id']),
        )
        op.create_index('ix_game_players_session_id', 'game_players', ['session_id'])

    if not inspector.has_table('game_events'):
        op.create_table('game_events',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('session_id', sa.Integer(), nullable=False),
            sa.Column('round', sa.Integer(), nullable=False),
            sa.Column('phase', sa.String(length=30), nullable=False),
            sa.Column('event_type', sa.String(length=30), nullable=False),
            sa.Column('actor_seat', sa.Integer(), nullable=True),
            sa.Column('target_seat', sa.Integer(), nullable=True),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('payload_json', sa.Text(), nullable=False),
            sa.Column('visibility', sa.String(length=10), nullable=False),
            sa.Column('private_to_seat', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['session_id'], ['game_sessions.id']),
        )
        op.create_index('ix_game_events_session_id', 'game_events', ['session_id'])
        op.create_index('ix_game_events_created_at', 'game_events', ['created_at'])

    if not inspector.has_table('game_memories'):
        op.create_table('game_memories',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('session_id', sa.Integer(), nullable=False),
            sa.Column('character_id', sa.Integer(), nullable=False),
            sa.Column('my_role', sa.String(length=20), nullable=False),
            sa.Column('my_word', sa.String(length=40), nullable=False),
            sa.Column('result', sa.String(length=10), nullable=False),
            sa.Column('survived_rounds', sa.Integer(), nullable=False),
            sa.Column('public_events_json', sa.Text(), nullable=False),
            sa.Column('my_actions_json', sa.Text(), nullable=False),
            sa.Column('summary', sa.String(length=300), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['session_id'], ['game_sessions.id']),
            sa.ForeignKeyConstraint(['character_id'], ['ai_characters.id']),
        )
        op.create_index('ix_game_memories_session_id', 'game_memories', ['session_id'])
        op.create_index('ix_game_memories_character_id', 'game_memories', ['character_id'])

    if not _has_column(bind, 'chat_group_messages', 'msg_type'):
        with op.batch_alter_table('chat_group_messages', schema=None) as batch_op:
            batch_op.add_column(sa.Column('msg_type', sa.String(length=12), nullable=False, server_default='normal'))
    if not _has_column(bind, 'chat_group_messages', 'game_session_id'):
        with op.batch_alter_table('chat_group_messages', schema=None) as batch_op:
            batch_op.add_column(sa.Column('game_session_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ('game_memories', 'game_events', 'game_players', 'game_sessions'):
        if inspector.has_table(table):
            op.drop_table(table)
    for col in ('game_session_id', 'msg_type'):
        if _has_column(bind, 'chat_group_messages', col):
            with op.batch_alter_table('chat_group_messages', schema=None) as batch_op:
                batch_op.drop_column(col)