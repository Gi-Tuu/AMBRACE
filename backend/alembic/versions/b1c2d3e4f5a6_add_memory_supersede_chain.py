"""add memory supersede chain columns (#70 C)

Revision ID: b1c2d3e4f5a6
Revises: a6b7c8d9e0f1
Create Date: 2026-08-30 00:00:00.000000

#70 方案C（M1/M2 取代链 + 轻量级联失效 + 冷归档/purge）：
- memories 加 status active/superseded/stale，默认 active（存量兼容）；
- memories 加 superseded_by（指向新记忆）/ valid_from / valid_to（有效区间）/ derived_from_ids（JSON 数组，M2）；
- 复合索引 idx_memories_char_status(character_id, status)：检索热路径（按角色取 active/stale）；
- 新建 memory_archive 表（C-2 冷归档）：memory_id/user_id/character_id/payload/archived_reason/created_at。

全部 has_table / has_column / has_index 守卫，可重复执行；downgrade 可逆（删索引→删列→删表）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a6b7c8d9e0f1"
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


def _has_index(bind, table: str, name: str) -> bool:
    inspector = sa.inspect(bind)
    try:
        return name in {i["name"] for i in inspector.get_indexes(table)}
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "memories"):
        return

    def _add(name: str, col) -> None:
        if not _has_column(bind, "memories", name):
            op.add_column("memories", col)

    _add("status", sa.Column("status", sa.String(length=12), nullable=False, server_default="active"))
    _add("superseded_by", sa.Column("superseded_by", sa.Integer(), nullable=True))
    _add("valid_from", sa.Column("valid_from", sa.DateTime(), nullable=True))
    _add("valid_to", sa.Column("valid_to", sa.DateTime(), nullable=True))
    _add("derived_from_ids", sa.Column("derived_from_ids", sa.Text(), nullable=False, server_default="[]"))

    # 存量兜底：任何 NULL/空 status 归位 active（正常由 server_default 兜住，此处双保险）
    op.execute("UPDATE memories SET status='active' WHERE status IS NULL OR status=''")

    # 复合索引：检索热路径（按角色取 active/stale）
    if not _has_index(bind, "memories", "idx_memories_char_status"):
        op.create_index("idx_memories_char_status", "memories", ["character_id", "status"])

    # C-2 冷归档表（memory_archive）：superseded 且 valid_to 超阈值 → 迁入；purge 时物理删
    if not _has_table(bind, "memory_archive"):
        op.create_table(
            "memory_archive",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("memory_id", sa.Integer(), nullable=False, index=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("character_id", sa.Integer(), nullable=False, index=True),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("archived_reason", sa.String(length=30), nullable=False, server_default="superseded_cold"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "memory_archive"):
        op.drop_table("memory_archive")
    if _has_table(bind, "memories"):
        if _has_index(bind, "memories", "idx_memories_char_status"):
            op.drop_index("idx_memories_char_status", table_name="memories")
        for name in ("derived_from_ids", "valid_to", "valid_from", "superseded_by", "status"):
            if _has_column(bind, "memories", name):
                op.drop_column("memories", name)
