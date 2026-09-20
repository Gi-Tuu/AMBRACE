# -*- coding: utf-8 -*-
"""add user_llm_limits（A8 LLM 额度按账号）

Revision ID: b5c6d7e8f9a0
Revises: a5b6c7d8e9f0
Create Date: 2026-09-20 10:00:00.000000

A8（2026-09-20）：把「LLM 免费额度总量」从单行全局（``llm_usage_limits.id=1``）扩成
**按账号可覆盖**：

- ``user_llm_limits``（新表）：每个账号至多一行覆盖（``user_id`` 主键）；
- 缺行 = 该账号无覆盖 = 回落全局 ``llm_usage_limits.id=1``：**存量账号行为与改动前
  逐字节一致**（本机全局行有效时，所有账号仍按全局值生效）；
- 生效口径（覆盖 > 全局 > 未设置）与读写入口在 ``app/application/llm_quota.py``，
  本迁移只管存储。

幂等：``has_table`` 守卫 —— create_all 路径（init_db 先建到当前模型）与远古库整链重放都不会
重复建表。``user_id`` 不挂 users FK：与 ``api_configs.user_id`` 等既有「自由整型归属」一致，
且 0/-1 服务器哨兵对 FK 违约。

downgrade 可逆（drop_table）；回退即失去账号级覆盖行（数据语义不可逆，注释说明）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, None] = "a5b6c7d8e9f0"
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
    if not _has_table(bind, "user_llm_limits"):
        op.create_table(
            "user_llm_limits",
            sa.Column("user_id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("total_limit", sa.Integer(), nullable=False),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "user_llm_limits"):
        op.drop_table("user_llm_limits")
    # 账号级额度覆盖随表一并消失（语义不可逆，无数据回滚）：回落全局行，行为等同改动前。
