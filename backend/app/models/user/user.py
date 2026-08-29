"""用户模型"""
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    nickname: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    birthday: Mapped[str | None] = mapped_column(String(10), nullable=True)  # MM-DD
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)  # male / female / other
    height: Mapped[float | None] = mapped_column(nullable=True)  # cm
    weight: Mapped[float | None] = mapped_column(nullable=True)  # kg
    bio: Mapped[str | None] = mapped_column(nullable=True)  # 个人简介
    lang: Mapped[str] = mapped_column(String(10), nullable=False, server_default="'zh'", default="zh")  # 界面语言 zh/en（i18n）
    ai_social_enabled: Mapped[bool] = mapped_column(Boolean, server_default="1", default=True)  # AI 间私聊开关（arbiter ai_social 采样时校验）
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default="0", default=False)  # 主账号（#46：可勾选的账号集合，优先于 settings.admin_user_ids）
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None, index=True)  # 主账号关联（#68：NULL=独立主账号，非NULL=子账号；P3 受邀码关联用，P0-P2 只用于共享配置判定）
    # 位置信息（2026-08-08）：location_enabled 总开关；location_gps_enabled=获取地理位置（开启后用户位置不可自定义）；
    # location_follow=位置跟随（开启后 AI 位置与用户相同、不可自定义）；timezone_offset_minutes=用户本地时区（分钟，如 480=UTC+8）
    location_enabled: Mapped[bool] = mapped_column(Boolean, server_default="0", default=False)
    location_gps_enabled: Mapped[bool] = mapped_column(Boolean, server_default="0", default=False)
    user_location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location_follow: Mapped[bool] = mapped_column(Boolean, server_default="0", default=False)
    timezone_offset_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_lat: Mapped[float | None] = mapped_column(nullable=True)  # GPS 定位纬度（获取地理位置开启时上报）
    location_lng: Mapped[float | None] = mapped_column(nullable=True)  # GPS 定位经度
    location_city: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 坐标反查城市名（Nominatim，失败留空）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # 关系
    characters = relationship("AICharacter", back_populates="user")
    chat_sessions = relationship("ChatSession", back_populates="user")
    memories = relationship("Memory", back_populates="user")
