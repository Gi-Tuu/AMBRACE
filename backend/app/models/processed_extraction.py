"""记忆提取去重：记录已处理的消息对（按用户消息 id）"""
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class ProcessedExtraction(Base):
    __tablename__ = "processed_extractions"

    user_message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
