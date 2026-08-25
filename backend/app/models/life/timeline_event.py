"""时光页大事记：离线 LLM 精选的重要时刻（每角色生成一次，可强制重生成）"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    event_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    title: Mapped[str] = mapped_column(String(50), nullable=False)
    desc: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(20), default="milestone")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
