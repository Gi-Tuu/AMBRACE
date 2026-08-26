"""家庭群聊（Phase 2）：多角色同群聊天 + 用户插话

- chat_groups：用户级群（成员 = 该用户的多个 AI 角色）
- chat_group_members：群成员
- chat_group_messages：群消息（user / ai；ai 消息带 character_id）
- 群聊不推送（用户主动进群查看）；用户发言后单次 LLM 调用生成多角色 JSON 回应
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChatGroup(Base):
    __tablename__ = "chat_groups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), default="家庭群聊")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatGroupMember(Base):
    __tablename__ = "chat_group_members"
    __table_args__ = (UniqueConstraint("group_id", "character_id", name="uq_group_character"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_groups.id"), nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    # 静音（群聊调度 L1，2026-08-25）：静音角色不参与自动选择（被 @ 仍强制回）
    muted: Mapped[bool] = mapped_column(Boolean, default=False)


class ChatGroupMessage(Base):
    __tablename__ = "chat_group_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_groups.id"), nullable=False, index=True)
    sender_type: Mapped[str] = mapped_column(String(10), default="ai")  # user / ai
    character_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    # 1=用户 @ 该角色后的回应，需弹通知（@我的才弹，2026-08-15）
    notify_user: Mapped[int] = mapped_column(Integer, default=0)
    # 游戏消息标记（群聊游戏 Phase 1，2026-08-26）：normal=普通群聊；game_event/game_say=游戏消息（不进群记忆/上下文）
    msg_type: Mapped[str] = mapped_column(String(12), default="normal")
    game_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
