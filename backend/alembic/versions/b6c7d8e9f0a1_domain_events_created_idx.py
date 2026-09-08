# -*- coding: utf-8 -*-
"""domain_events 补 created_at 单列索引（F-9，v3.4.6 审查）

Revision ID: b6c7d8e9f0a1
Revises: c1e2f3a4b5c6
Create Date: 2026-09-09

- purge_expired_domain_events 按 ``created_at < cutoff`` 单列过滤；原仅有两条复合索引
  （(aggregate_type,aggregate_id,created_at) / (event_type,created_at)），前导列均不含
  created_at → 清理走全表扫；补单列索引 ix_domain_event_created；
- 模型 Base.metadata（create_all 直建路径）已同步该索引（models/domain_event.py）；
- 幂等：upgrade 检测索引已存在则跳过；downgrade 仅 drop 本索引，不动表/数据。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, None] = "c1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = "ix_domain_event_created"


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
    if not _has_table(bind, "domain_events"):
        return
    if _INDEX_NAME not in _index_names(bind, "domain_events"):
        op.create_index(_INDEX_NAME, "domain_events", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "domain_events"):
        return
    if _INDEX_NAME in _index_names(bind, "domain_events"):
        op.drop_index(_INDEX_NAME, table_name="domain_events")
