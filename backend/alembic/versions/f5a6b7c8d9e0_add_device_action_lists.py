# -*- coding: utf-8 -*-
"""X7-M4c-3 行动名单落库：新增 ``device_action_targets`` / ``device_action_plugins`` 两张表。

Revision ID: f5a6b7c8d9e0
Revises: f4a5b6c7d8e9
Create Date: 2026-09-23

背景（派单 P19）：M4b-1/M4c-1 落地的两份闸门名单只活在**进程内存**
（``app/device/actions.py`` 的 ``_ALLOWLIST`` / ``_GRAYLIST_PLUGINS``）——后端一重启就清零，
运维每次重启都要重加目标白名单。本批把两份名单以库为权威：

- ``device_action_targets``：闸门④ 的目标白名单，一行＝「某家庭根允许对某包名提交行动」。
  联合唯一 ``(tenant_id, target)``（幂等写入的判据）；``tenant_id`` 单列索引（闸门按租户查）。
- ``device_action_plugins``：闸门③a 的逐插件灰度名单（全局、不分租户），``plugin_name`` 唯一。

口径（沿用先例 a5b6c7d8e9f0 / b9c0d1e2f3a4「新建表」）：
  * ``has_table`` 守卫 → **幂等**：全新库由 init_db 的 ``create_all`` 按当前模型直建（两个模型
    已在 ``app/models/device/__init__.py`` 登记），本迁移命中守卫即 0 操作；「非空老库整链重放」
    （scripts/migrate_drill.py 的 02 形态）在此真正建表；
  * 升级后**回验**两张表都在位，缺任一即抛错中止，防止静默漂移（缺表时读库异常会被
    fail-closed 掩盖成「名单为空＝全拒」，运维看不出是没建表还是没配）；
  * downgrade 可逆（带同样守卫后 drop，先删索引再删表）：回退即丢失两份名单数据
    （语义不可逆），行为回退到 M4c-1 的「内存名单、重启清零」。

本迁移同时把两张表各一个列登记进 ``app/db/migrate.py`` 的 ``_CURRENT_SCHEMA_SENTINELS``：
老库（有表但无版本号）缺此表必须判「落后」走 upgrade head，否则会被 stamp 到 head 却永久缺表。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TARGETS = "device_action_targets"
PLUGINS = "device_action_plugins"


def _has_table(bind, table: str) -> bool:
    """表在位守卫（异常一律 False → 跳过，不阻塞链）。"""
    try:
        return sa.inspect(bind).has_table(table)
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, TARGETS):
        op.create_table(
            TARGETS,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("target", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "target",
                                name="uq_device_action_target_tenant_target"),
        )
        op.create_index("ix_device_action_targets_tenant_id", TARGETS, ["tenant_id"])

    if not _has_table(bind, PLUGINS):
        op.create_table(
            PLUGINS,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("plugin_name", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("plugin_name", name="uq_device_action_plugins_name"),
        )

    insp = sa.inspect(bind)
    missing = [t for t in (TARGETS, PLUGINS) if not insp.has_table(t)]
    if missing:
        raise RuntimeError(f"升级后仍缺表 {missing}——中止，防止静默漂移")


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, TARGETS):
        op.drop_index("ix_device_action_targets_tenant_id", table_name=TARGETS)
        op.drop_table(TARGETS)
    if _has_table(bind, PLUGINS):
        op.drop_table(PLUGINS)
    # 两份名单数据随表一并消失（语义不可逆）：回退后闸门④/③a 回到「内存名单、重启清零」。
