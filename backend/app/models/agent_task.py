"""Agent 任务表（Phase H，2026-08-16）：任务级状态（断点续作/进度/结果），与 agent_task_logs（执行流水）分离"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class AgentTask(Base):
    """单次 Agent 任务（复杂请求/主动行为）：goal/status/progress/result，可追踪可续作"""

    __tablename__ = "agent_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)  # 与 agent_task_logs.task_id 同源
    trigger: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)  # chat_task / scheduler
    goal: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 目标（用户消息或行为类型）
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/running/done/failed
    progress_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 已执行步骤（AgentAction.to_step 列表）
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 结果摘要
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    character_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
