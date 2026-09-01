"""宠物互动活动日志：互动展示区数据源（用户/角色对宠物做的事，短时去重）"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class PetActivity(Base):
    """宠物活动记录：feed/play/clean/adopt/abandon/remind；同宠物同动作 30 分钟内视为同一件事（更新时间不新增）"""
    __tablename__ = "pet_activities"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pet_id: Mapped[int] = mapped_column(Integer, ForeignKey("pets.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # feed/play/clean/adopt/abandon/remind
    actor: Mapped[str] = mapped_column(String(10), default="user")  # user=用户（含拜访）/ ai=角色自己照顾
    content: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
