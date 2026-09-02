"""add plugin security gate columns (3.9)

Revision ID: d3e4f5a6b7c8
Revises: b1c2d3e4f5a6
Create Date: 2026-09-02 00:00:00.000000

实现 AMBRACE 3.9 插件安全闸（2026-09-02，第三波安全项）：
- plugins 加 source（builtin/remote/local）/ source_url / sha256：来源校验与记录（轻量）；
- plugins 加 consented_permissions（JSON 数组）/ consented_at：安装前权限确认的持久化同意。

使用 has_column 守卫（参照既有 b1c2d3e4f5a6 风格），可重复执行；downgrade 可逆（删列）。
所有说明见 docs/plugin-development.md 顶部安全模型；真实库 schema 已由 init_db 幂等兼容层兜底，
本迁移只负责「新增列」入版本链（3.8 纪律：新增 schema 变更一律走 Alembic）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    inspector = sa.inspect(bind)
    try:
        return inspector.has_table(table)
    except Exception:
        return False


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    try:
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

    _add("source", sa.Column("source", sa.String(length=16), nullable=False, server_default="builtin"))
    _add("source_url", sa.Column("source_url", sa.String(length=500), nullable=True))
    _add("sha256", sa.Column("sha256", sa.String(length=64), nullable=True))
    _add("consented_permissions", sa.Column("consented_permissions", sa.Text(), nullable=False, server_default="[]"))
    _add("consented_at", sa.Column("consented_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "plugins"):
        for name in ("consented_at", "consented_permissions", "sha256", "source_url", "source"):
            if _has_column(bind, "plugins", name):
                op.drop_column("plugins", name)
