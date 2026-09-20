# -*- coding: utf-8 -*-
"""add plugins owner columns（A2 M1 插件归户）

Revision ID: a8b9c0d1e2f3
Revises: b5c6d7e8f9a0
Create Date: 2026-09-20 12:00:00.000000

A2 批次 A · M1（2026-09-20）：给 ``plugins`` 表补两列**可空**归属列，只做记录、
**零行为变更**（不做任何可见性过滤——过滤属 M3，另批实施）：

- ``owner_user_id``   INTEGER NULL：安装者账号 id；
- ``owner_tenant_id`` INTEGER NULL：安装者家庭根（``family_service.get_family_root_id``）。

口径：**NULL = 内置/存量/服务级**（全局插件，所有账号同等可见/可用）；
非 NULL = 该安装者账号及其家庭根。写入点见 ``registry.record_install_provenance``
（只在列为 NULL 时落，重复安装/重复同步绝不覆盖已有 owner）；``registry.sync_plugins_db``
的 builtin 同步路径显式留 NULL。

幂等：``has_table`` + ``has_column`` 双层守卫——create_all 路径（init_db 已建到当前模型）
与远古库整链重放都不会重复加列，可重复执行。downgrade 可逆（drop_column）；回退即丢失
归属记录（数据语义不可逆，注释说明）。两列不挂 FK：归属为自由整型裸列，与
``api_configs.user_id`` / ``user_llm_limits.user_id`` 等既有「自由整型归属」一致，
且不作为 ORM 关系参与级联（插件行生命周期由 registry 管理）。

本迁移同时把 (``plugins``, ``owner_user_id``) 登记进 ``app/db/migrate.py`` 的
``_CURRENT_SCHEMA_SENTINELS``：老库（有表无版本号）缺此列必须判「落后」走 upgrade head。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "b5c6d7e8f9a0"
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
    if not _has_table(bind, "plugins"):
        return

    def _add(name: str, col) -> None:
        if not _has_column(bind, "plugins", name):
            op.add_column("plugins", col)

    _add("owner_user_id", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    _add("owner_tenant_id", sa.Column("owner_tenant_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "plugins"):
        for name in ("owner_tenant_id", "owner_user_id"):
            if _has_column(bind, "plugins", name):
                op.drop_column("plugins", name)
    # 归属数据随列一并消失（语义不可逆，无数据回滚）。
