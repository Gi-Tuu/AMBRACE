# -*- coding: utf-8 -*-
"""add user_runtime_flags（A5 用户级开关覆盖）

Revision ID: a5b6c7d8e9f0
Revises: f7a8b9c0d1e2
Create Date: 2026-09-19 20:00:00.000000

A5（2026-09-19）：把 App 里的「用户语义开关」从进程级全局改成按账号生效，消除多账号串扰。

- ``user_runtime_flags``（新表）：每个账号对每个「用户语义 key」的覆盖行；
  联合主键 ``(user_id, key)`` —— 一个账号一个键至多一行。
- 缺行 = 该账号无覆盖 = 回落全局 ``runtime_flags`` / ``AGENT_FLAGS`` 现值：**存量账号行为与
  改动前逐字节一致**（本机 runtime_flags 里 user_fact_relationship / user_fact_health 显式开着，
  全局值照旧生效）。
- 只有 ``application/flag_service.USER_SCOPED_FLAG_KEYS``（本批 5 个隐私细槽族键）会写本表；
  其余开关保持服务器级。

幂等：``has_table`` 守卫 —— create_all 路径（init_db 先建到当前模型）与远古库整链重放都不会
重复建表。``user_id`` 不挂 users FK：与 ``api_configs.user_id`` 等既有「自由整型归属」一致，
且 0/-1 服务器哨兵对 FK 违约。

downgrade 可逆（drop_table）；回退即失去用户级覆盖行（数据语义不可逆，注释说明）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    """表在位守卫（异常一律 False → 跳过，不阻塞链）。"""
    try:
        return sa.inspect(bind).has_table(table)
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "user_runtime_flags"):
        op.create_table(
            "user_runtime_flags",
            sa.Column("user_id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("key", sa.String(length=40), primary_key=True, nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "user_runtime_flags"):
        op.drop_table("user_runtime_flags")
    # 用户级覆盖数据随表一并消失（语义不可逆，无数据回滚）。
