# -*- coding: utf-8 -*-
"""fix memory_archive.created_at model-migration drift to NOT NULL (3.8 收尾第二批 B)

Revision ID: c3d4e5f6a7b8
Revises: 6d39454c2517
Create Date: 2026-09-03 00:00:00.000000

背景（3.8 审计发现的既有漂移，非本批引入）：
- 模型 ``MemoryArchive.created_at`` 为 ``Mapped[datetime]``（非 Optional → create_all 建为 NOT NULL），
  而 ``b1c2d3e4f5a6`` 建表处未声明 ``nullable=False`` → SQLite 实际建为可空列，PRAGMA 对比出现差异。
- 本迁移以模型为准把该列收敛为 NOT NULL：先防御性回填 NULL 行（表 2026-08-30 新建、存量极少，
  回填 CURRENT_TIMESTAMP），再 batch_alter_table 改非空（SQLite 走表重建，索引由 alembic 自动照搬）。
- 完全幂等：已是 NOT NULL（全新库 create_all 直建）→ 跳过；downgrade 可逆（改回可空，数据不动）。
- 禁改 init_db（3.8 纪律）：本迁移是唯一落点。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "6d39454c2517"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    inspector = sa.inspect(bind)
    try:
        return inspector.has_table(table)
    except Exception:
        return False


def _column_nullable(bind, table: str, column: str) -> bool | None:
    """返回列当前是否可空；表/列不存在返回 None。"""
    inspector = sa.inspect(bind)
    try:
        for col in inspector.get_columns(table):
            if col["name"] == column:
                return bool(col.get("nullable", True))
        return None
    except Exception:
        return None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "memory_archive"):
        return
    if _column_nullable(bind, "memory_archive", "created_at") is False:
        return  # 已是 NOT NULL（全新库 create_all 直建）→ 幂等跳过
    # 防御性回填：历史遗留 NULL 行先补时间戳（表 2026-08-30 新建、存量极少）
    bind.execute(sa.text(
        "UPDATE memory_archive SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
    ))
    with op.batch_alter_table("memory_archive", schema=None) as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(),
            nullable=False,
            existing_server_default=sa.func.now(),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "memory_archive"):
        return
    if _column_nullable(bind, "memory_archive", "created_at") is not False:
        return  # 已是可空 / 列不存在 → 幂等跳过
    with op.batch_alter_table("memory_archive", schema=None) as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(),
            nullable=True,
            existing_server_default=sa.func.now(),
        )
