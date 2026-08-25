"""MCP 工具调用日志表（Phase 4，2026-08-28）：每次 MCP 工具调用落一行。

- 与现有观测体系（agent_task_logs / llm_usage）一致：只写不读优先，字段自包含。
- trigger / route 语义与 agent_task_logs 对齐（trigger='mcp_tool'），但独立建表以支持
  「最近调用」列表按 server_id 快速检索，并保留 server/tool/参数摘要/ok/耗时 等结构化字段。
- 参数只存截断摘要（arguments_summary），不存完整敏感数据。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


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
