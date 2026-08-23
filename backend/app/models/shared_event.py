"""Shared Event 模型（Phase C，2026-08-14，演进规划 v2）：用户与 AI 共同经历

- 触发：user_marked（用户明确要求记住/第一次/纪念日）/ emotional_peak（情绪高峰）/ milestone（关系里程碑）/ ai_marked（AI 判断重要）
- 纪念日：is_anniversary=1 时在满月/周年由 scheduler 触发回忆消息
- 召回：对话构建时按重要性/新鲜度注入，AI 自然引用（防编造：只从记录检索）
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SharedEvent(Base):
    __tablename__ = "shared_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(30), default="user_marked")  # user_marked/emotional_peak/milestone/ai_marked
    category: Mapped[str] = mapped_column(String(20), default="daily")          # daily/milestone/anniversary
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    emotional_valence: Mapped[float] = mapped_column(Float, default=0.0)        # -1..1
    emotional_arousal: Mapped[float] = mapped_column(Float, default=0.5)        # 0..1
    importance: Mapped[float] = mapped_column(Float, default=0.6)               # 0..1
    is_anniversary: Mapped[bool] = mapped_column(Boolean, default=False)
    related_memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_recalled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 纪念日最近触发时间（防重复）
    event_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
