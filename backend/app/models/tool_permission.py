"""AI 能力权限模型：用户级三档权限（allow/ask/forbid）+ 待确认动作表（2026-08-12）

设计参考 Operit 工具权限模型：全局默认档位 + 每能力例外，实际权限 = 例外优先。
"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, Text, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ToolPermission(Base):
    """用户级 AI 能力权限（scope="__global__" 表示全局默认档位）"""
    __tablename__ = "tool_permissions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)  # image_gen / image_understand / tts / asr / browser / douyin / extension / __global__
    level: Mapped[str] = mapped_column(String(10), nullable=False, default="allow")  # allow / ask / forbid
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "scope", name="uq_user_scope"),
    )


class PendingPermissionAction(Base):
    """AI 主动调用能力的待确认动作（权限=ask 时挂起，用户确认后执行）"""
    __tablename__ = "pending_permission_actions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)  # JSON 动作载荷（如生图 prompt）
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")  # pending / approved / denied / expired
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
