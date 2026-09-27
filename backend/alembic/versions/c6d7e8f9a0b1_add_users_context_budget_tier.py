# -*- coding: utf-8 -*-
"""S2 M0（2026-09-27）：users 加账号级上下文预算档位列 context_budget_tier。

Revision ID: c6d7e8f9a0b1
Revises: b4c5d6e7f8a9
Create Date: 2026-09-27

- users 新增 context_budget_tier VARCHAR(20) NULL：值域 standard / extended / max，
  是「上下文注入长度」的账号级偏好（生效点在 app/agent/context_builder.py 的档位表）。

**只加可空列，NULL = 未设置 = 标准档 = 现状硬顶**：老行无需回填，未配置账号的装配/裁剪
行为逐字节不变。列里只存档位名不存 token 数——档位 → token 的映射归代码（单一事实源），
存值会让将来调档时历史数据失真（同 llm_mode 只存策略名不存配置）。

形态照抄同类的 b4c5d6e7f8a9（SQLite ADD COLUMN 原生支持可空列，无需 batch_alter_table
整表重建——users 是被大量外键引用的主表，不做 recreate 更稳）。

幂等：has_table + has_column 双守卫（老库整链重放安全，重复 upgrade 不报错；表不存在则跳过）。
可逆：downgrade 带同样守卫后 drop_column（回退即丢失各账号的档位选择，值本身可空、无索引/
外键/视图依赖，删列不动其它列的数据）。
收尾回验：表在位却仍缺列 → 抛错中止，防静默漂移（缺列会让档位读写与 GET /system/context-budget
直接报错，而不是悄悄退回标准档）。

配套硬要求（漏了会永久缺列）：app/db/migrate.py 的 _CURRENT_SCHEMA_SENTINELS 必须登记
("users", "context_budget_tier")——本列只由版本链引入、init_db 幂等层不补，老库（有表无版本号）
缺列时必须判「落后」走 upgrade head。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "users"
_COLUMN = "context_budget_tier"


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
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=20), nullable=True))
    # 收尾回验：表存在则列必须在位，否则中止（幂等重跑时列本就在，不会误报）
    if _has_table(bind, _TABLE) and not _has_column(bind, _TABLE, _COLUMN):
        raise RuntimeError(f"升级后 {_TABLE} 仍缺列 {_COLUMN}——中止，防止静默漂移")


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE) and _has_column(bind, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
