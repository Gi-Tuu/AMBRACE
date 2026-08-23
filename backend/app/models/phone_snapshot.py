"""手机感知快照（AI 走出沙箱）：手机端采集的屏幕文字/剪贴板/相册元数据与图片描述"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class PhoneSnapshot(Base):
    __tablename__ = "phone_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # accessibility/clipboard/media
    content: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    image_desc: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CheckInRequest(Base):
    """查岗请求（2026-08-15）：角色想感知用户手机时登记，前端轮询发现后立即采集上报"""
    __tablename__ = "check_in_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending / done / expired
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
