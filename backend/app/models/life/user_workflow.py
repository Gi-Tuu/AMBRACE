"""用户自建手机操作工作流（2026-08-14 P1）：AI 触发执行的多步动作序列"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserWorkflow(Base):
    """用户编排的手机操作工作流（步骤 JSON 存 steps 字段）"""
    __tablename__ = "user_workflows"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    steps: Mapped[str] = mapped_column(Text, nullable=False)  # 线性步骤 JSON 数组（旧格式/无分支时）
    graph: Mapped[str | None] = mapped_column(Text, nullable=True)  # 画布 JSON：{"nodes":[{id,action,...}], "edges":[{from,to,type,target?}]}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
