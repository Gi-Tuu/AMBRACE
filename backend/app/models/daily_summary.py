"""每日对话概要模型"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class DailySummary(Base):
    __tablename__ = "daily_summaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    summary_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    summary_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_points: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("session_id", "summary_date", name="uq_session_date"),
    )