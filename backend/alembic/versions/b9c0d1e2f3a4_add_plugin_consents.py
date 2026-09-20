# -*- coding: utf-8 -*-
"""add plugin_consents（A2 M6 同意按租户）

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-09-20 18:00:00.000000

A2 批次 B · M6（2026-09-20）：新增 ``plugin_consents`` 表，把插件权限同意从
「服务级一次性」（``plugins.consented_permissions``：任一人同意 → 之后所有人安装/升级都不再弹确认）
拆到「家庭根租户」维度：同一插件在不同家庭各自同意一次；升级新增权限时同理。

表结构（契约）：

- ``plugin_name``  VARCHAR(100) NOT NULL —— 插件名（对应 ``plugins.name``，不挂 FK：
  插件行生命周期由 ``registry`` 管理，与插件表零 FK 的既有口径一致）；
- ``tenant_id``    INTEGER NOT NULL —— 家庭根租户（``family_service.get_family_root_id``，
  与 ``channel_bindings.tenant_id`` 同口径）；
- ``permissions_json`` TEXT —— 该租户已同意权限集（JSON 数组，∪ 历次同意，保序去重）；
- ``consented_at`` DATETIME NULL —— 最近一次同意时间（naive UTC，与 ``plugins.consented_at`` 同口径）；
- ``consented_by`` INTEGER NULL —— 最近一次同意者账号 id；
- **联合主键** ``(plugin_name, tenant_id)``：一个插件一个租户至多一行。

读点权威在新表；``plugins.consented_permissions`` 保留为兼容旧读点的**服务级回落**
（仅对 ``owner_tenant_id IS NULL`` 的内置/存量/服务级插件生效），见 ``registry.consent_state``
调用链（``require_plugin_consent`` → ``get_tenant_consented_permissions``）。

存量一致性回填（一次性、幂等、只在新表为空时）由 ``registry.backfill_plugin_consents_once``
在启动同步路径（``sync_plugins_db``）执行：把 ``plugins.consented_permissions`` 非空且
``owner_tenant_id`` 非 NULL 的行按当时安装租户写入本表；``owner_tenant_id IS NULL``
（内置/存量/服务级）**不写新表行**，保持「内置插件全员放行」的既有语义。

幂等：``has_table`` 守卫 —— create_all 路径（本机默认：init_db 先建到当前模型，再由启动链
upgrade head 记账）与远古库整链重放都不会重复建表。downgrade 可逆（drop_table）；
回退即丢失租户级同意记录（数据语义不可逆，注释说明），旧读点回落到 ``plugins.consented_permissions``。

本迁移同时把 (``plugin_consents``, ``plugin_name``) 登记进 ``app/db/migrate.py`` 的
``_CURRENT_SCHEMA_SENTINELS``：老库（有表无版本号）缺此表必须判「落后」走 upgrade head。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
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
    if _has_table(bind, "plugin_consents"):
        return
    op.create_table(
        "plugin_consents",
        sa.Column("plugin_name", sa.String(length=100), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("permissions_json", sa.Text(), nullable=True),
        sa.Column("consented_at", sa.DateTime(), nullable=True),
        sa.Column("consented_by", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "plugin_consents"):
        op.drop_table("plugin_consents")
    # 租户级同意数据随表一并消失（语义不可逆，无数据回滚；旧读点回落服务级兼容列）。
