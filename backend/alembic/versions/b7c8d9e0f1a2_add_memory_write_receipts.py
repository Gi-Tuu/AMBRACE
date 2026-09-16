# -*- coding: utf-8 -*-
"""#70 附录 C 可选 M3：记忆写入回执 memory_write_receipts

Revision ID: b7c8d9e0f1a2
Revises: f3c4d5e6f7a1
Create Date: 2026-09-15

- 新建 memory_write_receipts（终态追踪「这条记忆为什么在/不在」）：action 属于
  create/update/merge/supersede/stale/reject/downgrade；
- character_id / memory_id 均可空（全局记忆、拒绝落库等没有具体 id 的场景）；
- 两个索引：(character_id, created_at) 回看 + memory_id 反查；
- 仅当 flag memory_write_receipt 开时由写入点异步写入（默认关 = 零写入、零行为变化）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "f3c4d5e6f7a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None

TABLE = "memory_write_receipts"
INDEXES = ("idx_mwr_char_created", "idx_mwr_memory")


def _has_table(bind, table: str) -> bool:
    try:
        return sa.inspect(bind).has_table(table)
    except Exception:
        return False


def _index_names(bind, table: str) -> set:
    try:
        return {i["name"] for i in sa.inspect(bind).get_indexes(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("character_id", sa.Integer(), nullable=True),
            sa.Column("memory_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(length=20), nullable=False),
            sa.Column("reason", sa.String(length=255), nullable=True),
            sa.Column("detail_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
    have = _index_names(bind, TABLE)
    if "idx_mwr_char_created" not in have:
        op.create_index("idx_mwr_char_created", TABLE, ["character_id", "created_at"])
    if "idx_mwr_memory" not in have:
        op.create_index("idx_mwr_memory", TABLE, ["memory_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, TABLE):
        return
    have = _index_names(bind, TABLE)
    for name in INDEXES:
        if name in have:
            op.drop_index(name, table_name=TABLE)
    op.drop_table(TABLE)
