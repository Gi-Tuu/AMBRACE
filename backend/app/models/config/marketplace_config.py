"""插件市场远程配置模型（单行：启用/URL 列表/刷新间隔/域名白名单/大小上限）"""
from datetime import datetime
from sqlalchemy import Integer, Text, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class MarketplaceConfig(Base):
    __tablename__ = "marketplace_config"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)          # 远程市场总开关
    urls: Mapped[str] = mapped_column(Text, default="[]")                  # JSON 数组：远程 index URL 列表
    refresh_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    allowed_hosts: Mapped[str] = mapped_column(Text, default="[]")         # JSON 数组：域名白名单（[]=不限 https）
    max_zip_mb: Mapped[int] = mapped_column(Integer, default=10)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
