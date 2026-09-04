# -*- coding: utf-8 -*-
"""#72 group_memories 群共享长期记忆子库

Revision ID: f3a4b5c6d7e8
Revises: e5f6a7b8c9d0
Create Date: 2026-09-04

- 新增群共享长期记忆表 group_memories（一份/群，不按成员冗余复制、不进主记忆向量检索）；
- 与 games.game_memories 同范式：群"共同经历"只存一份，角色主 memories 只留 group_summary 摘要指针。
- importance 类型对齐 memories.importance（Float，0-120）；字段/索引与模型一致。
- 完全幂等：表已存在则跳过（索引按需补充）；downgrade 可逆。全新库由 create_all 直建，upgrade 零重复。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector(bind):
    return sa.inspect(bind)


def _has_table(bind, table: str) -> bool:
    try:
        return _inspector(bind).has_table(table)
    except Exception:
        return False


def _index_names(bind, table: str) -> set:
    try:
        return {i["name"] for i in _inspector(bind).get_indexes(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "group_memories"):
        op.create_table(
            "group_memories",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("group_id", sa.Integer(), sa.ForeignKey("chat_groups.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("round_id", sa.String(length=40), nullable=True),
            sa.Column("speaker_type", sa.String(length=10), nullable=False, server_default="system"),
            sa.Column("speaker_id", sa.Integer(), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("epistemic_status", sa.String(length=12), nullable=False, server_default="FACT"),
            sa.Column("importance", sa.Float(), nullable=False, server_default="40"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
    idxs = _index_names(bind, "group_memories") if _has_table(bind, "group_memories") else set()
    for name, cols in (
        ("idx_group_mem_group_created", ["group_id", "created_at"]),
        ("ix_group_memories_group_id", ["group_id"]),
        ("ix_group_memories_user_id", ["user_id"]),
        ("ix_group_memories_round_id", ["round_id"]),
    ):
        if name not in idxs:
            op.create_index(name, "group_memories", cols)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "group_memories"):
        return
    idxs = _index_names(bind, "group_memories")
    for name in (
        "ix_group_memories_round_id", "ix_group_memories_user_id",
        "ix_group_memories_group_id", "idx_group_mem_group_created",
    ):
        if name in idxs:
            op.drop_index(name, table_name="group_memories")
    op.drop_table("group_memories")
