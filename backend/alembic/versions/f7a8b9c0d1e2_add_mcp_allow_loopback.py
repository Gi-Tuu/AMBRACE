# -*- coding: utf-8 -*-
"""add mcp_servers.allow_loopback（P3-4 本地回环细粒度放行）

Revision ID: f7a8b9c0d1e2
Revises: c5d6e7f8a9b0
Create Date: 2026-09-19 12:00:00.000000

P3-4（2026-09-19）：为单个 MCP Server 增加「显式允许本地回环」标记列。
- 默认 0（False）：行为与改动前逐字节一致（loopback 仍被 SSRF 拦截）；
- 置 1 且 URL 解析结果【全部】为 loopback（127.0.0.0/8 / ::1 / localhost）时才放行；其余私网/
  链路本地/云元数据（192.168/10./172.16-31/169.254）即便标记也照旧拒绝
  （判定见 app/mcp/transport.py::_resolve_mcp_ip）；
- 保留既有全局开关 settings.mcp_http_allow_private 语义不变（True 仍全局放行，向后兼容）。

幂等：``has_table`` / ``has_column`` 守卫 —— create_all 路径（init_db 先建到当前模型）与远古库
整链重放都不会重复加列。SQLite 的 ``ALTER TABLE ADD COLUMN`` 要求 NOT NULL 列带默认值，故
``server_default='0'``（与模型侧 server_default="0" 对齐）。downgrade 可逆（删列）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "c5d6e7f8a9b0"
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
    if not _has_column(bind, "mcp_servers", "allow_loopback"):
        op.add_column(
            "mcp_servers",
            sa.Column("allow_loopback", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "mcp_servers", "allow_loopback"):
        op.drop_column("mcp_servers", "allow_loopback")
