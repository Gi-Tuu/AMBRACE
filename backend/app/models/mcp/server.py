"""MCP Server 配置（AMBRACE MCP 接入，Phase 1）：mcp_servers 表。

- 每行代表一个用户配置的标准 MCP Server 连接。
- transport 本期只支持 stdio；sse / streamable_http 字段预留（url/headers_json），Phase 2 启用。
- 工具发现结果缓存到 tools_cache_json（last_error 记录上次连接失败原因）。
- 唯一约束 (user_id, name)，name 为稳定唯一标识（正则 [A-Za-z0-9_-]+）。
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MCPServer(Base):
    """用户可配置的一个 MCP Server（phase 1 仅 stdio 传输）。"""

    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_mcp_server_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # 唯一标识，正则 [A-Za-z0-9_-]+
    transport: Mapped[str] = mapped_column(String(10), nullable=False)  # stdio | sse | streamable_http
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)  # stdio: 可执行文件
    args_json: Mapped[str] = mapped_column(Text, default="[]")  # stdio: 参数列表 JSON
    env_json: Mapped[str] = mapped_column(Text, default="{}")  # stdio: 环境变量 JSON
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # sse/http: 端点（预留）
    headers_json: Mapped[str] = mapped_column(Text, default="{}")  # sse/http: 自定义头（预留）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_connect: Mapped[bool] = mapped_column(Boolean, default=True)  # 启动时自动连接
    tools_cache_json: Mapped[str] = mapped_column(Text, default="[]")  # 上次发现工具缓存
    status: Mapped[str] = mapped_column(String(20), default="disconnected")  # disconnected|connecting|connected|error
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
