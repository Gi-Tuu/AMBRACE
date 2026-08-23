"""Agent Task Trace 表（Phase A，2026-08-16）：轻量任务追踪，先只写不读"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, Float, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class AgentTaskLog(Base):
    """单次 Agent 任务 trace（聊天/搜索/生图等调用处写入；成本归因第三批可复用）"""

    __tablename__ = "agent_task_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    character_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger: Mapped[str | None] = mapped_column(String(20), nullable=True)  # chat / chunked / image_gen / scheduler / plugin
    route: Mapped[str | None] = mapped_column(String(30), nullable=True)  # direct / search_loop / chunked
    steps_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 各步动作（AgentAction.to_step 列表）
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)  # 近似成本（本期留空，由 llm_usage 聚合）
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok / degraded / error
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
