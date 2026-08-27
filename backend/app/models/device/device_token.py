"""设备推送 token（FCM 离线推送，2026-08-28）：user_device_tokens 表。

- 每行代表一个用户设备的推送通道（当前仅 FCM；push_provider 预留 apns）。
- 同一用户多设备：每设备一行，推送时逐 token 发送。
- 唯一约束 (user_id, device_id, push_provider)：同一设备同一通道只保留最新 token。
- last_seen_at 由 App 前台心跳更新；90 天未活跃的 token 可定期清理。
"""
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserDeviceToken(Base):
    """用户设备推送 token（FCM / 预留 APNs）。"""

    __tablename__ = "user_device_tokens"
    __table_args__ = (
        UniqueConstraint("user_id", "device_id", "push_provider", name="uq_device_token_user_device_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)  # App 生成的设备 UUID
    platform: Mapped[str] = mapped_column(String(16), nullable=False)  # android | ios
    push_provider: Mapped[str] = mapped_column(String(16), nullable=False)  # fcm | apns
    push_token: Mapped[str] = mapped_column(String(512), nullable=False)  # FCM registration token
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
