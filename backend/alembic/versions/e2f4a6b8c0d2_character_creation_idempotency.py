# -*- coding: utf-8 -*-
"""角色创建幂等收据表（M1，2026-10-19）

新增 ``character_creation_requests``：只存不透明键/摘要/归属/目标 id，
唯一约束 (user_id, idempotency_key) 作为并发去重的数据库最后防线。
不改任何既有表与索引。

Revision ID: e2f4a6b8c0d2
Revises: d1a2b3c4e5f6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2f4a6b8c0d2"
down_revision: Union[str, None] = "d1a2b3c4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # 与既有迁移一致：存量库若已被 init_db() 的 create_all 补建过，跳过（避免重名创建失败）
    if inspector.has_table("character_creation_requests"):
        return
    op.create_table(
        "character_creation_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(
            "users.id", ondelete="CASCADE", name="fk_char_creation_req_user"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("character_id", sa.Integer(), sa.ForeignKey(
            "ai_characters.id", ondelete="SET NULL", name="fk_char_creation_req_character"),
            nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_char_creation_req_user_key"),
    )
    op.create_index("ix_character_creation_requests_user_id",
                    "character_creation_requests", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("character_creation_requests"):
        return
    op.drop_index("ix_character_creation_requests_user_id",
                  table_name="character_creation_requests")
    op.drop_table("character_creation_requests")
