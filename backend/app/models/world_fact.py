"""World Fact 模型（World & Cognition P4，2026-08-15）：世界状态物化视图

事件 → 规则折叠 → 当前世界事实（event-sourced）。
同 subject+predicate 的新事实取代旧事实（supersede），历史事实保留可审计；
查询按 audience 过滤（知识边界：audience 含 viewer 或 public 才可见）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WorldFact(Base):
    __tablename__ = "world_facts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(12), default="character")  # user/character/system
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    predicate: Mapped[str] = mapped_column(String(20), nullable=False)  # status/activity/location/mood
    object_value: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active/superseded/expired
    confidence: Mapped[float] = mapped_column(Float, default=1.0)  # 0-1
    epistemic_status: Mapped[str] = mapped_column(String(12), default="FACT")  # FACT/INFERRED/PLANNED
    audience: Mapped[str] = mapped_column(String(255), default="[]")  # JSON ["user:4","char:11"] 或 ["public"]
    author: Mapped[str] = mapped_column(String(20), default="system")  # user/character/system（P1-3 权威事实层）
    is_authoritative: Mapped[bool] = mapped_column(Boolean, default=False)  # 用户/创作者定义的不可动摇事实（P1-3）
    source: Mapped[str | None] = mapped_column(String(30), nullable=True)  # chat_status/life_activity/user_setting
    source_event_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    superseded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 过期时间（可空=不自动过期）
    asserted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
