"""add mcp_servers table (AMBRACE MCP 接入 Phase 1)

Revision ID: 7c9e1a2b3d40
Revises: 04dd1d6c5544
Create Date: 2026-08-26 00:00:00.000000

AMBRACE MCP 接入（Phase 1）：新增 mcp_servers 表，承载用户配置的标准 MCP Server 连接。
- 使用 has_table 守卫：存量库若已被 init_db() 的 create_all 幂等补建，则跳过，避免重名创建失败。
- 不动旧 baseline（1d19fa0a34c9），当前 head 为 04dd1d6c5544。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c9e1a2b3d40'
down_revision: Union[str, None] = '04dd1d6c5544'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('mcp_servers'):
        op.create_table('mcp_servers',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=64), nullable=False),
            sa.Column('transport', sa.String(length=10), nullable=False),
            sa.Column('command', sa.String(length=500), nullable=True),
            sa.Column('args_json', sa.Text(), nullable=False),
            sa.Column('env_json', sa.Text(), nullable=False),
            sa.Column('url', sa.String(length=500), nullable=True),
            sa.Column('headers_json', sa.Text(), nullable=False),
            sa.Column('enabled', sa.Boolean(), nullable=False),
            sa.Column('auto_connect', sa.Boolean(), nullable=False),
            sa.Column('tools_cache_json', sa.Text(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'name', name='uq_mcp_server_user_name'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('mcp_servers'):
        op.drop_table('mcp_servers')
