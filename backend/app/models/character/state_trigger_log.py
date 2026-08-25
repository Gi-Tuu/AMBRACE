"""状态触发日志：维度触发事件防抖与恢复检测（v1 状态触发机制）"""
from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class StateTriggerLog(Base):
    __tablename__ = "state_trigger_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    trigger_key: Mapped[str] = mapped_column(String(40), nullable=False)  # 如 anger_high / anger_mood_low
    value: Mapped[str] = mapped_column(String(200), default="")           # 触发时八维快照
    recovered: Mapped[bool] = mapped_column(Boolean, default=False)       # 是否已回落（回落后才可再触发）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # 冷战细化（2026-08-15）：触发时怒气 / 最近哄好分级(0无 1轻哄或找台阶 2真诚 3敷衍) / 软化次数 / 别扭期标志
    anger_at_trigger: Mapped[int] = mapped_column(Integer, default=0)
    soothe_level: Mapped[int] = mapped_column(Integer, default=0)
    soothe_count: Mapped[int] = mapped_column(Integer, default=0)
    stubborn: Mapped[int] = mapped_column(Integer, default=0)
