"""add mcp_call_logs table (AMBRACE MCP 接入 Phase 4)

Revision ID: 3a6d4e8f2c91
Revises: 7c9e1a2b3d40
Create Date: 2026-08-28 00:00:00.000000

AMBRACE MCP 接入（Phase 4）：新增 mcp_call_logs 表，承载每次 MCP 工具调用日志
（server/tool/参数摘要/ok/耗时），供扩展页「最近调用」列表。
- 使用 has_table 守卫：存量库若已被 init_db() 的 create_all 幂等补建，则跳过，避免重名创建失败。
- 当前 head 为 7c9e1a2b3d40（add_mcp_servers）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a6d4e8f2c91'
down_revision: Union[str, None] = '7c9e1a2b3d40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('mcp_call_logs'):
        op.create_table('mcp_call_logs',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('server_id', sa.Integer(), nullable=True),
            sa.Column('server_name', sa.String(length=64), nullable=True),
            sa.Column('tool', sa.String(length=255), nullable=False),
            sa.Column('arguments_summary', sa.Text(), nullable=True),
            sa.Column('ok', sa.Boolean(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('error', sa.String(length=500), nullable=True),
            sa.Column('latency_ms', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_mcp_call_logs_user_id', 'mcp_call_logs', ['user_id'])
        op.create_index('ix_mcp_call_logs_server_id', 'mcp_call_logs', ['server_id'])
        op.create_index('ix_mcp_call_logs_created_at', 'mcp_call_logs', ['created_at'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('mcp_call_logs'):
        op.drop_table('mcp_call_logs')
