"""Lorebook 条目模型（P1-2，2026-08-16）：用户/角色可自定义的关键词触发设定条目

对话中出现关键词时，对应条目确定性注入上下文（精确匹配，不靠向量检索）；
排除词列表用于防误触发（如关键词「猫」排除「猫屎咖啡」）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LorebookEntry(Base):
    __tablename__ = "lorebook_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[str] = mapped_column(Text, default="[]")       # JSON list[str]，必须 ≥2 字
    exclude_keywords: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[str]，出现则本轮不触发
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
