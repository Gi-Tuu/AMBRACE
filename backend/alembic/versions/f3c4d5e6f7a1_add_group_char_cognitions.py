# -*- coding: utf-8 -*-
"""#72 PR-C group_cognition_v2：群聊认知升级（P1）数据层

Revision ID: f3c4d5e6f7a1
Revises: f2b3c4d5e6f7
Create Date: 2026-09-15

- 新增 group_char_cognitions 表（逐角色 × 群 × 话题窗口的认知，P3 生成写入，本迁移只建表）：
  与 group_memories 并列但本质不同——group_memories 是群共享客观事件（一份/群、谁都能看），
  本表是某角色对个人认知/立场/小结（owner=该角色、主观、仅本人可见，P3 注入私有上下文）；
  group_id 对 chat_groups 为 ondelete CASCADE（群删→认知连带清）。
- chat_groups 新增 cognition_enabled BOOLEAN NOT NULL DEFAULT 0（群级灰度二级闸，默认关=零行为变化）。

完全幂等（has_table / has_column 守卫）；downgrade 可逆（删列 + 删表）。全新库由 create_all 直建，
upgrade 零重复；存量库 group_id FK 已在上游 f2b3c4d5e6f7 落 ondelete，本表新建直接带 ondelete CASCADE。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3c4d5e6f7a1"
down_revision: Union[str, None] = "f2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def _has_table(bind, table: str) -> bool:
    try:
        return sa.inspect(bind).has_table(table)
    except Exception:
        return False


def _has_column(bind, table: str, column: str) -> bool:
    try:
        return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}
    except Exception:
        return False


def _index_names(bind, table: str) -> set:
    try:
        return {i["name"] for i in sa.inspect(bind).get_indexes(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    # ── 1) 新建 group_char_cognitions 表（幂等：表已存在则跳过）──
    if not _has_table(bind, "group_char_cognitions"):
        op.create_table(
            "group_char_cognitions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("group_id", sa.Integer(),
                      sa.ForeignKey("chat_groups.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer(),
                      sa.ForeignKey("users.id"), nullable=False),
            sa.Column("character_id", sa.Integer(),
                      sa.ForeignKey("ai_characters.id", ondelete="CASCADE"), nullable=False),
            sa.Column("round_id", sa.String(length=40), nullable=True),
            sa.Column("topic_key", sa.String(length=64), nullable=True),
            sa.Column("cognition_type", sa.String(length=12), nullable=False, server_default="stance"),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("importance", sa.Float(), nullable=False, server_default="40"),
            sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
    idxs = _index_names(bind, "group_char_cognitions") if _has_table(bind, "group_char_cognitions") else set()
    for name, cols in (
        ("idx_gcc_group_char", ["group_id", "character_id", "created_at"]),
        ("idx_gcc_round", ["round_id"]),
    ):
        if name not in idxs:
            op.create_index(name, "group_char_cognitions", cols)

    # ── 2) chat_groups 加 cognition_enabled（NOT NULL + 默认值，SQLite 友好）──
    if _has_table(bind, "chat_groups"):
        if not _has_column(bind, "chat_groups", "cognition_enabled"):
            with op.batch_alter_table("chat_groups", schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column("cognition_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0"))
                )


def downgrade() -> None:
    bind = op.get_bind()
    # ── 2) 撤 chat_groups.cognition_enabled ──
    if _has_table(bind, "chat_groups"):
        if _has_column(bind, "chat_groups", "cognition_enabled"):
            with op.batch_alter_table("chat_groups", schema=None) as batch_op:
                batch_op.drop_column("cognition_enabled")
    # ── 1) 撤 group_char_cognitions 表 ──
    if _has_table(bind, "group_char_cognitions"):
        idxs = _index_names(bind, "group_char_cognitions")
        for name in ("idx_gcc_round", "idx_gcc_group_char"):
            if name in idxs:
                op.drop_index(name, table_name="group_char_cognitions")
        op.drop_table("group_char_cognitions")
