"""AI 隐私上锁：查看申请记录（日记 diary / 手机感知快照 phone）"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class PrivacyRequest(Base):
    __tablename__ = "privacy_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(10), nullable=False)  # diary / phone
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="applied")  # applied / approved / rejected
    ai_reply: Mapped[str] = mapped_column(String(500), nullable=False, default="")  # AI 回复（提示框展示）
    mood_label: Mapped[str] = mapped_column(String(20), nullable=False, default="无所谓")  # 开心/无所谓/烦躁等
    unlock_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 通过后的解锁截止（UTC naive）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
