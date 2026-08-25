"""认知循环反思日志表（v2.1）：记录反思触发原因与自查结果，供评测"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class ReflectionLog(Base):
    __tablename__ = "reflection_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # AI 回复消息 id
    triggers: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 触发原因
    checks: Mapped[str | None] = mapped_column(Text, nullable=True)    # JSON 自查结果
    pass_or_fail: Mapped[str | None] = mapped_column(String(10), nullable=True, default="PASS")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())