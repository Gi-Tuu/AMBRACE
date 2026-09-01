"""对话话题追踪表（认知架构 v2.1 Conversation State）"""
from datetime import datetime
from sqlalchemy import Integer, String, Float, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class ConversationTopic(Base):
    __tablename__ = "conversation_topics"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="进行中")  # 进行中/搁置/完成
    importance: Mapped[float] = mapped_column(Float, default=0.6)
    last_touched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    follow_up: Mapped[bool] = mapped_column(Boolean, default=True)
    goal: Mapped[bool] = mapped_column(Boolean, default=False)  # 目标记忆（v2.1）：长期目标类话题
    progress: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 目标进度：进行中/有进展/快完成/搁置/完成
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())