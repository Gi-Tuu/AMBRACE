"""AI 间私聊记录（Phase 1）：同用户两个 AI 角色私下对话，落库供只读展示"""
from datetime import datetime

from sqlalchemy import Integer, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AIChat(Base):
    __tablename__ = "ai_chats"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    character_a_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    character_b_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    speaker_id: Mapped[int] = mapped_column(Integer, nullable=False)  # 发言角色 id（a 或 b）
    round_seq: Mapped[int] = mapped_column(Integer, default=0)  # 同事件内轮次（0 起，0=事件首条）
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
