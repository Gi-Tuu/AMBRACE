"""插件模型（扩展系统：插件开关/配置持久化，全局不绑定用户）"""
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Plugin(Base):
    __tablename__ = "plugins"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    version: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="")
    author: Mapped[str] = mapped_column(String(100), default="")
    category: Mapped[str] = mapped_column(String(20), default="plugin")  # plugin / mcp
    type: Mapped[str] = mapped_column(String(20), default="http")  # 48c：插件类型 http/prompt/chat/workflow/hybrid
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
