# -*- coding: utf-8 -*-
"""add console admin P2 schema（账号独立 P2 · 控制台管理面）

Revision ID: c5d6e7f8a9b0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-19 18:00:00.000000

契约 §2 数据层（只加列 / 只加表，幂等，默认值让现有行为不变）：

- ``users.disabled_at`` DATETIME NULL：非空 = 控制台禁用该账号；
- ``users.llm_mode`` VARCHAR(20) NOT NULL DEFAULT 'default_allowed'：
  ``own`` / ``default_allowed``（默认，现状行为） / ``blocked``；
- ``admin_audit_log``（新表）：控制台写动作审计流水；
- ``flag_settings``（新表）：开关策略元数据（缺行 = self_service=1 / server_locked=0，与现状一致）；
- ``server_settings``（新表）：字符串型服务器配置 KV（注册策略等；禁止塞进 AGENT_FLAGS）。

幂等：``has_table`` / ``has_column`` 双层守卫 —— create_all 路径（本机默认：init_db 先建到当前
模型，再由启动链 upgrade head 记账）与远古库整链重放都不会重复建列/建表。

存量数据：**不动**。disabled_at 全 NULL、llm_mode 全默认、三张新表为空——上线即与改动前行为等价。

downgrade：仅回退本迁移新增的列/表（数据语义不可逆，注释说明）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    """表在位守卫（异常一律 False → 跳过，不阻塞链）。"""
    try:
        return sa.inspect(bind).has_table(table)
    except Exception:
        return False


def _has_column(bind, table: str, column: str) -> bool:
    """列在位守卫（表不存在/异常一律 False → 跳过，不阻塞链）。"""
    try:
        inspector = sa.inspect(bind)
        if not inspector.has_table(table):
            return False
        return column in {c["name"] for c in inspector.get_columns(table)}
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()

    # 1) users：账号门禁两列（只加列，不重建表）
    if not _has_column(bind, "users", "disabled_at"):
        op.add_column("users", sa.Column("disabled_at", sa.DateTime(), nullable=True))
    if not _has_column(bind, "users", "llm_mode"):
        op.add_column(
            "users",
            sa.Column(
                "llm_mode", sa.String(length=20), nullable=False,
                server_default=sa.text("'default_allowed'"),
            ),
        )

    # 2) admin_audit_log：控制台写动作审计（append-only）
    if not _has_table(bind, "admin_audit_log"):
        op.create_table(
            "admin_audit_log",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("actor_user_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("target", sa.String(length=128), nullable=True),
            sa.Column("before_json", sa.Text(), nullable=True),
            sa.Column("after_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        )

    # 3) flag_settings：开关策略元数据（缺行＝自助开、未锁定）
    if not _has_table(bind, "flag_settings"):
        op.create_table(
            "flag_settings",
            sa.Column("key", sa.String(length=64), primary_key=True),
            sa.Column("self_service", sa.Boolean(), server_default=sa.text("1"), nullable=True),
            sa.Column("server_locked", sa.Boolean(), server_default=sa.text("0"), nullable=True),
            sa.Column("title", sa.String(length=64), nullable=True),
            sa.Column("desc", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        )

    # 4) server_settings：字符串型服务器配置 KV（注册策略等）
    if not _has_table(bind, "server_settings"):
        op.create_table(
            "server_settings",
            sa.Column("key", sa.String(length=64), primary_key=True),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("server_settings", "flag_settings", "admin_audit_log"):
        if _has_table(bind, table):
            op.drop_table(table)
    if _has_column(bind, "users", "llm_mode"):
        op.drop_column("users", "llm_mode")
    if _has_column(bind, "users", "disabled_at"):
        op.drop_column("users", "disabled_at")
    # 账号门禁/策略数据随列与表一并消失（语义不可逆，无数据回滚）。
