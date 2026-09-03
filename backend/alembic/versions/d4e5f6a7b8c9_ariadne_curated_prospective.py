# -*- coding: utf-8 -*-
"""Ariadne 模块F（Curated Knowledge，world_facts 加 5 列）+ 模块G（prospective_intents 建表）

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-04 00:00:00.000000

- 模块F：world_facts 是既有「权威事实层」，本迁移不建平行事实源，只在同表加 5 列，
  用 kind 把「瞬时状态事实(status/activity/...)」与「长期编纂知识(curated)」分治。
  kind 存量行 server_default='status'，语义=现有的瞬时状态，保证旧数据行为不变。
- 模块G：prospective_intents 承接「未来某时间/某线索才兑现」的意图，独立状态机，
  与 memories / #70 supersede 正交，不参与记忆检索/衰减/查重。
- 完全幂等：列/表已存在则跳过；downgrade 可逆。全新库由 create_all 直建，upgrade 应零操作。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector(bind):
    return sa.inspect(bind)


def _has_table(bind, table: str) -> bool:
    try:
        return _inspector(bind).has_table(table)
    except Exception:
        return False


def _has_column(bind, table: str, column: str) -> bool:
    try:
        return any(c["name"] == column for c in _inspector(bind).get_columns(table))
    except Exception:
        return False


# module-F：要给 world_facts 增加的 5 列（名字 -> (类型, 服务端默认)；default=None 即可空列）
_CURATED_COLUMNS = [
    ("kind", sa.String(20), "status"),               # status/fact/constraint/preference_profile/relationship_baseline
    ("verify_state", sa.String(12), "unverified"),   # unverified/machine-confirmed/human-reviewed
    ("sources_json", sa.Text(), "[]"),               # 溯源数组
    ("stale_after", sa.DateTime(), None),            # 到期转「待复核」，NULL=不复核（区别于 expires_at 的物理过期）
    ("links_json", sa.Text(), "[]"),                 # 概念链接（一跳展开）
]


def upgrade() -> None:
    bind = op.get_bind()

    # ── 模块F：world_facts 加 5 列（SQLite 走 batch 表重建，幂等）──
    if _has_table(bind, "world_facts"):
        with op.batch_alter_table("world_facts", schema=None) as batch_op:
            existing = {c["name"] for c in _inspector(bind).get_columns("world_facts")}
            for name, col_type, default in _CURATED_COLUMNS:
                if name in existing:
                    continue
                if default is None:
                    batch_op.add_column(sa.Column(name, col_type, nullable=True))
                else:
                    batch_op.add_column(sa.Column(
                        name, col_type, nullable=False, server_default=default,
                    ))

    # kind 列对应模型 index=True：upgrade 必须同步建索引，否则全新库 create_all 与链结构不一致（三类回归红）
    if _has_table(bind, "world_facts"):
        idxs = {i["name"] for i in _inspector(bind).get_indexes("world_facts")}
        if "ix_world_facts_kind" not in idxs:
            op.create_index("ix_world_facts_kind", "world_facts", ["kind"])

    # ── 模块G：prospective_intents 建表（幂等）──
    if not _has_table(bind, "prospective_intents"):
        op.create_table(
            "prospective_intents",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("character_id", sa.Integer(), nullable=False),
            sa.Column("content", sa.String(length=500), nullable=False),
            sa.Column("kind", sa.String(length=20), nullable=False, server_default="promise"),  # promise/cue
            sa.Column("cue_terms_json", sa.Text(), server_default="[]"),
            sa.Column("due_start", sa.DateTime(), nullable=True),
            sa.Column("due_end", sa.DateTime(), nullable=True),    # NULL=纯线索型
            sa.Column("status", sa.String(length=12), nullable=False, server_default="pending"),
            sa.Column("source_message_id", sa.Integer(), nullable=True),
            sa.Column("chat_session_id", sa.Integer(), nullable=True),
            sa.Column("discharged_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("idx_pis_scan", "prospective_intents", ["status", "due_end"])
        op.create_index("idx_pis_char", "prospective_intents", ["character_id", "status"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "prospective_intents"):
        op.drop_index("idx_pis_char", table_name="prospective_intents")
        op.drop_index("idx_pis_scan", table_name="prospective_intents")
        op.drop_table("prospective_intents")
    if _has_table(bind, "world_facts"):
        idxs = {i["name"] for i in _inspector(bind).get_indexes("world_facts")}
        if "ix_world_facts_kind" in idxs:
            op.drop_index("ix_world_facts_kind", table_name="world_facts")
        with op.batch_alter_table("world_facts", schema=None) as batch_op:
            existing = {c["name"] for c in _inspector(bind).get_columns("world_facts")}
            for name, _, _ in _CURATED_COLUMNS:
                if name in existing:
                    batch_op.drop_column(name)
