# -*- coding: utf-8 -*-
"""跨角色用户事实层（§20）：新增 user_facts 用户级单值事实槽表

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-04 00:00:00.000000

- 「用户的客观当前状态」缺一个跨角色的唯一事实源：新增 user_facts（user 级、跨角色共享），
  只放可变单值槽（location/job/relationship/living/goal_state/health），新值取代旧值并记录
  previous_value（供对旧记忆做失效匹配）。
- 与 world_facts（P1-3 权威层）分治：world_facts 仍按 (user_id, character_id) 隔离、描述
  角色视角的世界状态（本迁移不动它）；user_facts 是用户级唯一事实源，两者正交，不替代 world_facts。
- 与 memories / #70 supersede：旧值失效仍走 memory.status='stale'（复用 #70，不删可追溯），
  本表不参与记忆检索/衰减/查重。
- 完全幂等：表已存在则跳过；downgrade 可逆。全新库由 create_all 直建，upgrade 应零操作。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector(bind):
    return sa.inspect(bind)


def _has_table(bind, table: str) -> bool:
    try:
        return _inspector(bind).has_table(table)
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "user_facts"):
        op.create_table(
            "user_facts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("slot", sa.String(length=30), nullable=False),            # location/job/relationship/living/goal_state/health/...
            sa.Column("value", sa.String(length=200), nullable=False),           # 当前值（归一化短文本）
            sa.Column("previous_value", sa.String(length=200), nullable=True),   # 上一值（供旧记忆失效匹配）
            sa.Column("source", sa.String(length=20), nullable=True),            # chat/gps/manual/global_sync
            sa.Column("epistemic_status", sa.String(length=12), nullable=False, server_default="FACT"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("valid_from", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "slot", name="uq_user_slot"),
        )
    # user_id 索引与模型 index=True 一致（全新库 create_all 也会生成同名索引）
    idxs = {i["name"] for i in _inspector(bind).get_indexes("user_facts")} if _has_table(bind, "user_facts") else set()
    if "ix_user_facts_user_id" not in idxs:
        op.create_index("ix_user_facts_user_id", "user_facts", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "user_facts"):
        idxs = {i["name"] for i in _inspector(bind).get_indexes("user_facts")}
        if "ix_user_facts_user_id" in idxs:
            op.drop_index("ix_user_facts_user_id", table_name="user_facts")
        op.drop_table("user_facts")
