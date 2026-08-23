"""用户免打扰设置模型（后端感知，供状态触发等主动行为联动）"""
from datetime import datetime
from sqlalchemy import Integer, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class UserDndSettings(Base):
    """用户免打扰配置（与前端 SharedPreferences 同步，upsert）"""
    __tablename__ = "user_dnd_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    dnd_enabled: Mapped[bool] = mapped_column(Boolean, default=False)            # 免打扰时段总开关
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)   # 通知总开关（弹窗用，前端语义）
    start_hour: Mapped[int] = mapped_column(Integer, default=22)
    start_minute: Mapped[int] = mapped_column(Integer, default=0)
    end_hour: Mapped[int] = mapped_column(Integer, default=8)
    end_minute: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
