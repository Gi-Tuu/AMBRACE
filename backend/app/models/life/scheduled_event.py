"""定时承诺事件模型 — AI 在对话中承诺的定时事件（如洗n分钟澡后回来）"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ScheduledEvent(Base):
    """AI 承诺的定时事件：到期触发一条兑现消息"""
    __tablename__ = "scheduled_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    trigger_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), default="back")  # shower/sleep/meal/back
    status: Mapped[str] = mapped_column(String(20), default="pending")   # pending/fired/cancelled/expired
    content_hint: Mapped[str | None] = mapped_column(String(200), nullable=True)
    owner: Mapped[str] = mapped_column(String(10), default="ai")  # ai=AI承诺 / user=用户承诺
    source_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_scheduled_events_status_trigger", "status", "trigger_at"),
    )