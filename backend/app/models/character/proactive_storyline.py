"""主动事件切片模型 — 一次事件生成一条连贯消息，切成多段按顺序逐条发送"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProactiveStorylineItem(Base):
    """事件切片：同一 group_id 属于一次事件，按 send_at 顺序逐条发送（私信专用，朋友圈不走此表）"""

    __tablename__ = "proactive_storyline_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_characters.id"), nullable=False, index=True
    )
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chat_sessions.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    group_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(String(500), nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)  # 深度思考过程（气泡折叠展示，2026-08-15）
    send_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/sent/expired
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
