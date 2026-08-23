"""舞台记忆模型（2026-08-09）：记录虚假的/角色扮演的记忆，隔离于常规记忆库。

用途：用户与 AI 角色扮演场景中发生的非现实互动（如一起洗澡、亲亲、扮演剧情动作等）
只写入 stage_memories，不进入 memories 常规库，避免污染 AI 的真实记忆与检索结果。
"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class StageMemory(Base):
    __tablename__ = "stage_memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # 舞台事件描述（AI 第一人称）
    stage_kind: Mapped[str] = mapped_column(String(20), default="roleplay")  # roleplay=角色扮演
    importance: Mapped[int] = mapped_column(Integer, default=3)  # 1-5
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 来源用户消息 ID
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
