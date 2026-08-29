"""用户八维可视化状态模型（用户主页蛛网图，用户可手动滑动调整，0-100）"""
from datetime import datetime
from sqlalchemy import Integer, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class UserState(Base):
    __tablename__ = "user_states"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    mood: Mapped[int] = mapped_column(Integer, default=50)           # 心情
    body_temp: Mapped[int] = mapped_column(Integer, default=50)      # 体温（指数，50=正常体感）
    desire: Mapped[int] = mapped_column(Integer, default=50)         # 性欲
    possessiveness: Mapped[int] = mapped_column(Integer, default=50) # 占有欲
    fatigue: Mapped[int] = mapped_column(Integer, default=50)        # 疲惫感
    sensitivity: Mapped[int] = mapped_column(Integer, default=50)    # 敏感度
    comfort: Mapped[int] = mapped_column(Integer, default=50)        # 舒适感
    anger: Mapped[int] = mapped_column(Integer, default=50)          # 怒气值
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
