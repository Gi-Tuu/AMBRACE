"""add lorebook trigger fields (AMBRACE Lorebook 触发式注入进阶 L2 核心版)

Revision ID: d2e5b6c7a8f3
Revises: b4de26e0a171
Create Date: 2026-08-24 09:00:00.000000

L2 核心版：lorebook_entries 触发式注入引擎新增字段（只加列、不删列、不改既有列、
不新建表——BASE.metadata 的 91 张表不变）：
- is_regex（Boolean 默认 0）：关键词按正则解析（/pattern/flags 或裸 pattern），False=子串（向后兼容）；
- probability（Integer 默认 100）：0-100 命中后注入概率，100=必注入；
- inclusion_group（String(50) 默认 ''）：同组条目同轮只注入一条（取 updated_at 最新）；
- sticky_rounds（Integer 默认 0）：触发后持续注入 N 轮；
- cooldown_rounds（Integer 默认 0）：触发后 N 轮内不再注入。

默认值与 ORM（app/models/memory/lorebook.py）保持一致，保证 is_regex=False /
probability=100 / inclusion_group='' / sticky_rounds=0 / cooldown_rounds=0 时行为与现状
完全一致（向后兼容）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e5b6c7a8f3'
down_revision: Union[str, None] = 'b4de26e0a171'
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
    # 只加列：新字段全部给非空 + server_default，存量行回填为默认值（向后兼容）。
    # has_column 守卫：存量库若已被 init_db()/create_all 补建该列则跳过，避免重放重名冲突。
    if not _has_table(bind, "lorebook_entries"):
        return
    _to_add = []
    if not _has_column(bind, "lorebook_entries", "is_regex"):
        _to_add.append(sa.Column('is_regex', sa.Boolean(), nullable=False, server_default=sa.text('0')))
    if not _has_column(bind, "lorebook_entries", "probability"):
        _to_add.append(sa.Column('probability', sa.Integer(), nullable=False, server_default=sa.text('100')))
    if not _has_column(bind, "lorebook_entries", "inclusion_group"):
        _to_add.append(sa.Column('inclusion_group', sa.String(length=50), nullable=False, server_default=sa.text("''")))
    if not _has_column(bind, "lorebook_entries", "sticky_rounds"):
        _to_add.append(sa.Column('sticky_rounds', sa.Integer(), nullable=False, server_default=sa.text('0')))
    if not _has_column(bind, "lorebook_entries", "cooldown_rounds"):
        _to_add.append(sa.Column('cooldown_rounds', sa.Integer(), nullable=False, server_default=sa.text('0')))
    if _to_add:
        with op.batch_alter_table('lorebook_entries', schema=None) as batch_op:
            for _c in _to_add:
                batch_op.add_column(_c)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "lorebook_entries"):
        return
    with op.batch_alter_table('lorebook_entries', schema=None) as batch_op:
        for _col in ('cooldown_rounds', 'sticky_rounds', 'inclusion_group', 'probability', 'is_regex'):
            if _has_column(bind, "lorebook_entries", _col):
                batch_op.drop_column(_col)
