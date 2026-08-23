"""用户日记模型（用户写给 AI 好友看的日记，供角色聊天阅读）"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class UserDiary(Base):
    __tablename__ = "user_diaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    diary_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "diary_date", name="uq_user_diary_date"),
    )
