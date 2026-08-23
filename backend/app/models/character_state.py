"""角色八维可视化状态模型（心情/体温/性欲/占有欲/疲惫感/敏感度/舒适感/怒气值，0-100）"""
from datetime import datetime
from sqlalchemy import Integer, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class CharacterState(Base):
    __tablename__ = "character_states"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), unique=True, nullable=False)
    mood: Mapped[int] = mapped_column(Integer, default=50)           # 心情
    body_temp: Mapped[int] = mapped_column(Integer, default=50)      # 体温（指数，50=正常体感）
    desire: Mapped[int] = mapped_column(Integer, default=50)         # 性欲
    possessiveness: Mapped[int] = mapped_column(Integer, default=50) # 占有欲
    fatigue: Mapped[int] = mapped_column(Integer, default=50)        # 疲惫感
    sensitivity: Mapped[int] = mapped_column(Integer, default=50)    # 敏感度
    comfort: Mapped[int] = mapped_column(Integer, default=50)        # 舒适感
    anger: Mapped[int] = mapped_column(Integer, default=50)         # 怒气值（高=生气，低=平静）
    trust: Mapped[int] = mapped_column(Integer, default=50)         # 信任（v2.1 关系标量，长期不互动衰减）
    attachment: Mapped[int] = mapped_column(Integer, default=50)    # 依恋（v2.1 关系标量，长期不互动衰减）
    curiosity: Mapped[int] = mapped_column(Integer, default=50)     # 好奇（v2.1 关系标量）
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 最近互动（评估）时间；drift 结算不刷新，疲劳休息判定用
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
