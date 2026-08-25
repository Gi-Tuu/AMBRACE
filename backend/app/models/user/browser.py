"""浏览器 MCP 数据模型（计划 16：短期快照，30 分钟过期，不进用户记忆库）"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class BrowserSnapshot(Base):
    """网页浏览短期快照（30 分钟过期清理；严格记忆隔离：不写 memories 表）"""
    __tablename__ = "browser_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, default=1)
    url: Mapped[str] = mapped_column(String(500), unique=True, default="")
    domain: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    image_urls_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
