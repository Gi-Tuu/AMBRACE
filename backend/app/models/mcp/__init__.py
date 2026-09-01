# -*- coding: utf-8 -*-
"""MCP 域：服务器配置/调用日志（F6 聚合，2026-08-31）。

原 mcp/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.mcp.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ── server.py ──
# MCP Server 配置（AMBRACE MCP 接入，Phase 1）：mcp_servers 表。
#
# - 每行代表一个用户配置的标准 MCP Server 连接。
# - transport 本期只支持 stdio；sse / streamable_http 字段预留（url/headers_json），Phase 2 启用。
# - 工具发现结果缓存到 tools_cache_json（last_error 记录上次连接失败原因）。
# - 唯一约束 (user_id, name)，name 为稳定唯一标识（正则 [A-Za-z0-9_-]+）。
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

# ── call_log.py ──
# MCP 工具调用日志表（Phase 4，2026-08-28）：每次 MCP 工具调用落一行。
#
# - 与现有观测体系（agent_task_logs / llm_usage）一致：只写不读优先，字段自包含。
# - trigger / route 语义与 agent_task_logs 对齐（trigger='mcp_tool'），但独立建表以支持
#   「最近调用」列表按 server_id 快速检索，并保留 server/tool/参数摘要/ok/耗时 等结构化字段。
# - 参数只存截断摘要（arguments_summary），不存完整敏感数据。
class McpCallLog(Base):
    """单次 MCP 工具调用 trace（mcp_manager.call_tool 写入；先只写不读，提供「最近调用」列表）。"""

    __tablename__ = "mcp_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    server_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    server_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool: Mapped[str] = mapped_column(String(255), nullable=False)  # 完整工具名（mcp.{server}.{tool}）
    arguments_summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # 参数 JSON 摘要（截断）
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="error")  # ok / error / timeout / blocked
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
__all__ = [
    "MCPServer",
    "McpCallLog",
]
