"""角色八维状态历史快照：每次聊天评估后存 1 行（Phase 2：情绪曲线/蛛网对比数据源）"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class CharacterStateHistory(Base):
    __tablename__ = "character_state_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    mood: Mapped[int] = mapped_column(Integer, default=50)
    body_temp: Mapped[int] = mapped_column(Integer, default=50)
    desire: Mapped[int] = mapped_column(Integer, default=50)
    possessiveness: Mapped[int] = mapped_column(Integer, default=50)
    fatigue: Mapped[int] = mapped_column(Integer, default=50)
    sensitivity: Mapped[int] = mapped_column(Integer, default=50)
    comfort: Mapped[int] = mapped_column(Integer, default=50)
    anger: Mapped[int] = mapped_column(Integer, default=50)
    source: Mapped[str] = mapped_column(String(20), default="eval")  # eval=聊天评估 / drift=漂移结算
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
