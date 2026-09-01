"""冷归档记忆模型（#70-C2）：superseded 且 valid_to 超过阈值 → 迁入本表，退出热检索/注入。

- 只进不出的历史快照（payload = 原行 JSON），purge 时连本表一起物理删；
- 不进任何检索/注入，只在「翻历史 / 关系报告 / 年度回顾」显式查。
"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class MemoryArchive(Base):
    __tablename__ = "memory_archive"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    memory_id: Mapped[int] = mapped_column(Integer, index=True)  # 原热库记忆 id
    user_id: Mapped[int] = mapped_column(Integer)
    character_id: Mapped[int] = mapped_column(Integer, index=True)
    payload: Mapped[str] = mapped_column(Text)  # 原行 JSON 快照
    archived_reason: Mapped[str] = mapped_column(String(30), default="superseded_cold", server_default="superseded_cold")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
