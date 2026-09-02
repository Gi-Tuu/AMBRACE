"""add memories group_id (P3-3: 群聊记忆按群节流)

Revision ID: 1edb666e9762
Revises: d2e5b6c7a8f3
Create Date: 2026-08-26 00:00:00.000000

P3-3（v3.3.3 审查）：群聊记忆节流查询原先不区分群，跨群互相抑制（A 群的 30 分钟记忆会把
B 群的新记忆也一并抑制）。给 memories 表加 group_id（Integer, nullable）：
- 新群聊记忆落库时写入 group_id（见 app.api.chat_groups._save_group_memory）；
- 节流查询按 group_id 过滤（旧数据 group_id IS NULL 时按旧行为，不区分群）；
- 不建外键（群可能被删，记忆保留；仅做归属标记），无 server_default（NULL 为默认）。

说明：只加列、不删列、不改既有列——BASE.metadata 的表集合不变。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1edb666e9762'
down_revision: Union[str, None] = 'd2e5b6c7a8f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    inspector = sa.inspect(bind)
    try:
        return inspector.has_table(table)
    except Exception:
        return False


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    try:
        return column in {c["name"] for c in inspector.get_columns(table)}
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    # has_column 守卫：存量库若已被 init_db()/create_all 补建该列则跳过，避免重放重名冲突。
    if not _has_table(bind, "memories"):
        return
    if not _has_column(bind, "memories", "group_id"):
        with op.batch_alter_table('memories', schema=None) as batch_op:
            batch_op.add_column(sa.Column('group_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "memories") and _has_column(bind, "memories", "group_id"):
        with op.batch_alter_table('memories', schema=None) as batch_op:
            batch_op.drop_column('group_id')
