"""add talkativeness and muted (AMBRACE 群聊调度 L1 + 群控)

Revision ID: b4de26e0a171
Revises: 3a6d4e8f2c91
Create Date: 2026-08-25 19:24:01.693726

L1 群聊调度升级（数据层）：
- ai_characters 增加 talkativeness（Integer 0-100，NULL=未设置按性格推断）
  与 talkativeness_locked（Boolean，1=AI 不可自主调整）；
- chat_group_members 增加 muted（Boolean，静音角色不参与自动选择，被 @ 仍强制回）。

说明：autogenerate 曾因存量库与 ORM 的 schema 漂移输出大量 constraint/type 噪音，本迁移
已手工收敛为「只加列、不删列、不改既有列」——BASE.metadata 的 91 张表不变。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4de26e0a171'
down_revision: Union[str, None] = '3a6d4e8f2c91'
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
    if _has_table(bind, "ai_characters"):
        _add_cols = []
        if not _has_column(bind, "ai_characters", "talkativeness"):
            _add_cols.append(sa.Column('talkativeness', sa.Integer(), nullable=True))
        if not _has_column(bind, "ai_characters", "talkativeness_locked"):
            _add_cols.append(sa.Column('talkativeness_locked', sa.Boolean(), nullable=False, server_default=sa.text('0')))
        if _add_cols:
            with op.batch_alter_table('ai_characters', schema=None) as batch_op:
                for _c in _add_cols:
                    batch_op.add_column(_c)

    if _has_table(bind, "chat_group_members"):
        if not _has_column(bind, "chat_group_members", "muted"):
            with op.batch_alter_table('chat_group_members', schema=None) as batch_op:
                batch_op.add_column(sa.Column('muted', sa.Boolean(), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "ai_characters"):
        with op.batch_alter_table('ai_characters', schema=None) as batch_op:
            if _has_column(bind, "ai_characters", "talkativeness_locked"):
                batch_op.drop_column('talkativeness_locked')
            if _has_column(bind, "ai_characters", "talkativeness"):
                batch_op.drop_column('talkativeness')

    if _has_table(bind, "chat_group_members"):
        if _has_column(bind, "chat_group_members", "muted"):
            with op.batch_alter_table('chat_group_members', schema=None) as batch_op:
                batch_op.drop_column('muted')
