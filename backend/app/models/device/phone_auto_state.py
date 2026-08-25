"""手机感知·AI 主动提及状态：记录上次已提及的通知指纹与触发时间（节流防打扰）"""
from datetime import datetime
from sqlalchemy import Integer, DateTime, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class PhoneAutoState(Base):
    __tablename__ = "phone_auto_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    last_trigger_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fingerprints: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 列表：上次已提及的通知指纹
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
