"""add user_rhythm and browser_snapshots

Revision ID: 04dd1d6c5544
Revises: 1d19fa0a34c9
Create Date: 2026-08-24 00:00:00.000000

P2-2（v3.2.8 全量审查）：baseline（1d19fa0a34c9）创建 87 张表，但 ORM 现有两张表未入基线：
  - user_rhythm（用户作息学习，v3.2.8 新增）
  - browser_snapshots（浏览器 MCP 快照，v3.2.8 新增）
本迁移按 ORM 模型（app/models/user_rhythm.py / app/models/browser.py）补齐这两张表。
- 使用 has_table 守卫：存量库若已被 init_db() 的 create_all 幂等补建，则跳过，避免重名创建失败。
- 不动旧 baseline。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '04dd1d6c5544'
down_revision: Union[str, None] = '1d19fa0a34c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('user_rhythm'):
        op.create_table('user_rhythm',
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('active_hours', sa.Text(), nullable=False),
            sa.Column('learned_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.PrimaryKeyConstraint('user_id')
        )

    if not inspector.has_table('browser_snapshots'):
        op.create_table('browser_snapshots',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('url', sa.String(length=500), nullable=False),
            sa.Column('domain', sa.String(length=200), nullable=False),
            sa.Column('title', sa.String(length=300), nullable=False),
            sa.Column('text', sa.Text(), nullable=False),
            sa.Column('image_urls_json', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('url')
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('browser_snapshots'):
        op.drop_table('browser_snapshots')
    if inspector.has_table('user_rhythm'):
        op.drop_table('user_rhythm')
