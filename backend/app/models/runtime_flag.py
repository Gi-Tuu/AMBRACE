# 运行时 Feature Flag 表（2026-08-18）：DB 覆盖硬编码默认，API 热更新无需重启。
from datetime import datetime
from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class RuntimeFlag(Base):
    __tablename__ = 'runtime_flags'

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
