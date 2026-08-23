"""聊天消息模型"""
from datetime import datetime
from sqlalchemy import Boolean, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

import sqlalchemy as sa
from app.models.base import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    sender_type: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # "user" | "ai" | "system"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 图片消息：/uploads/... 相对路径
    extra_meta: Mapped[str | None] = mapped_column("extra_meta", Text, nullable=True)  # JSON
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 关系
    session = relationship("ChatSession", back_populates="messages")
