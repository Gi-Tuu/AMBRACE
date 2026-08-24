"""用户作息学习（#28 ②，2026-08-24）：持久化用户活跃时段（user_rhythm 表）。

- user_id 主键（每用户一行）；active_hours 为 JSON 字符串（list[list[int]]，如 [[8,11],[20,23]]）。
- 新表由 Base.metadata.create_all（init_db）幂等创建，无需 ALTER 迁移。
"""
from datetime import datetime

from sqlalchemy import Integer, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserRhythm(Base):
    """用户活跃时段（按小时推断；active_hours = list[list[int]] 的 JSON 字符串）。"""

    __tablename__ = "user_rhythm"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    active_hours: Mapped[str] = mapped_column(Text, default="[]")  # JSON 活跃时段 [[start,end),...]
    learned_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
