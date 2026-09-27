# -*- coding: utf-8 -*-
"""A4 批 5 / T6 M2（2026-09-27）：llm_usage 加渠道归因列 channel。

Revision ID: b4c5d6e7f8a9
Revises: a9c1e3f5b7d9
Create Date: 2026-09-27

- llm_usage 新增 channel VARCHAR(30) NULL：值域 app / wechat_ilink / server，
  供用量报表按「渠道」摊开（读端 NULL 归 (unknown) 桶）。

只加可空列：不设默认值、不建索引、**不回填历史行**（勘察 §4：回填会把「上线前的历史」与
「将来某处漏传」永久混进同一格，失去排错能力；且迁移跑在启动路径上，对只增表做全表 UPDATE
等于给每次冷启动加时长）。

幂等：has_table + has_column 双守卫（老库整链重放安全，重复 upgrade 不报错；表不存在则跳过）。
可逆：downgrade 带同样守卫后 drop_column。
收尾回验：表在位却仍缺列 → 抛错中止，防静默漂移（同 a9c1e3f5b7d9 口径）。
形态照抄同表同类的 a6b7c8d9e0f1（SQLite ADD COLUMN 原生支持可空列，无需 batch_alter_table）。

配套硬要求（漏了会永久缺列）：app/db/migrate.py 的 _CURRENT_SCHEMA_SENTINELS 必须登记
("llm_usage", "channel")——本列只由版本链引入、init_db 幂等层不补，老库（有表无版本号）
缺列时必须判「落后」走 upgrade head。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, None] = "a9c1e3f5b7d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "llm_usage"
_COLUMN = "channel"


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


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE) and not _has_column(bind, _TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=30), nullable=True))
    # 收尾回验：表存在则列必须在位，否则中止（幂等重跑时列本就在，不会误报）
    if _has_table(bind, _TABLE) and not _has_column(bind, _TABLE, _COLUMN):
        raise RuntimeError(f"升级后 {_TABLE} 仍缺列 {_COLUMN}——中止，防止静默漂移")


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE) and _has_column(bind, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
