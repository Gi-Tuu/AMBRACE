"""add llm_usage.config_id + group_owner_id (#68 P6)

Revision ID: a6b7c8d9e0f1
Revises: f1a2b3c4d5e6
Create Date: 2026-08-28 14:00:00.000000

#68 P6 用量组聚合：
- llm_usage 加 config_id INTEGER NULL（落库时透传 user_llm_configs.id）；
- llm_usage 加 group_owner_id INTEGER NULL（家庭根账号，子账号用量归根账号统计）。

使用列存在守卫：存量库若已被 init_db() create_all 补建则跳过，避免重名冲突。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a6b7c8d9e0f1'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
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
    if not inspector.has_table('llm_usage'):
        return
    if not _has_column(bind, 'llm_usage', 'config_id'):
        op.add_column('llm_usage', sa.Column('config_id', sa.Integer(), nullable=True))
    if not _has_column(bind, 'llm_usage', 'group_owner_id'):
        op.add_column('llm_usage', sa.Column('group_owner_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for col in ('group_owner_id', 'config_id'):
        if _has_column(bind, 'llm_usage', col):
            op.drop_column('llm_usage', col)
