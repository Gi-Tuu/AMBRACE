# -*- coding: utf-8 -*-
"""add users.server_admin（账号独立 P1 控制台地基）

Revision ID: c4d5e6f7a8b9
Revises: e8f9a0b1c2d3
Create Date: 2026-09-19 10:00:00.000000

账号独立 P1：把「家庭主账号」（users.is_admin，家庭内管理）与「服务器控制台管理员」
（users.server_admin，跨家庭、管服务器级配置）分离，为服务器控制台打地基。

- 只加列：users.server_admin BOOLEAN NOT NULL DEFAULT 0（server_default='0'），
  不重建表、不改动任何既有列；
- 幂等：has_table/has_column 双层守卫，列已存在（create_all 路径 / 重复执行）则跳过 ALTER；
- 存量迁移：is_admin=1 的账号一并置 server_admin=1（条件里带 server_admin=0，重复执行无副作用），
  单家庭部署下与旧行为等价；此后 server_admin 独立演进。

downgrade：仅回退列（数据语义不可逆，注释说明）。
"""
import os
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    if not _has_column(bind, "users", "server_admin"):
        op.add_column(
            "users",
            sa.Column("server_admin", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
    # 存量迁移（幂等）：**只授予 .env ADMIN_USER_IDS 列出的账号**。
    # 09-19 复核修正：本产品「独立账号」默认都是家庭主账号（parent_id IS NULL → is_admin=1），
    # 按 is_admin=1 回填会把每个新注册账号都变成控制台管理员（闸门形同虚设）。
    if _has_column(bind, "users", "server_admin"):
        raw = (os.environ.get("ADMIN_USER_IDS") or "1").strip()
        ids = [int(x) for x in re.findall(r"\d+", raw)] or [1]
        bind.execute(sa.text(
            "UPDATE users SET server_admin = 1 WHERE id IN (%s) AND server_admin = 0"
            % ",".join(str(i) for i in ids)
        ))
        # 引导兜底：一个 server_admin 都没有 → 取最早账号（否则控制台永远进不去）
        row = bind.execute(sa.text("SELECT id FROM users WHERE server_admin = 1 LIMIT 1")).first()
        if row is None:
            bind.execute(sa.text(
                "UPDATE users SET server_admin = 1 WHERE id = (SELECT MIN(id) FROM users)"
            ))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "users", "server_admin"):
        op.drop_column("users", "server_admin")
    # server_admin 的授权数据随列一并消失（语义不可逆，无数据回滚）。
