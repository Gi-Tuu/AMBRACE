# -*- coding: utf-8 -*-
"""通用领域事件追加流水 domain_events（3.10 chat/moment 事件流水 P0）

Revision ID: c1e2f3a4b5c6
Revises: b8c9d0e1f2a3
Create Date: 2026-09-08

- 新增 append-only 表 domain_events：按聚合(aggregate_type,aggregate_id)记录 chat.*/moment.* 事件；
- UQ(idempotency_key) 兜底重复写入；两条检索索引（按聚合重放 / 按类型时间）；
- 幂等可重放；全新库由 Base.metadata.create_all 直建，upgrade 检测到表已存在则跳过；
- downgrade 仅 drop 本表，不动任何业务表/数据。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1e2f3a4b5c6"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    if _has_table(bind, "domain_events"):
        return
    op.create_table(
        "domain_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        sa.Column("aggregate_type", sa.String(length=32), nullable=False),
        sa.Column("aggregate_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=12), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False, server_default="system_event"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_domain_event_idem"),
    )
    idxs = _index_names(bind, "domain_events")
    if "ix_domain_event_agg" not in idxs:
        op.create_index("ix_domain_event_agg", "domain_events",
                        ["aggregate_type", "aggregate_id", "created_at"])
    if "ix_domain_event_type_time" not in idxs:
        op.create_index("ix_domain_event_type_time", "domain_events",
                        ["event_type", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "domain_events"):
        return
    for name in ("ix_domain_event_agg", "ix_domain_event_type_time"):
        if name in _index_names(bind, "domain_events"):
            op.drop_index(name, table_name="domain_events")
    op.drop_table("domain_events")
