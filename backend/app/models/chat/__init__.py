# -*- coding: utf-8 -*-
"""会话域：聊天会话/消息/家庭群聊（群-成员-消息）/AI 间私聊（F6 聚合，2026-08-31）。

原 chat/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.chat.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
import sqlalchemy as sa

from app.models.base import Base

# ── session.py ──
# 聊天会话模型
class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    last_read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 关系
    user = relationship("User", back_populates="chat_sessions")
    character = relationship("AICharacter", back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="session", order_by="ChatMessage.created_at")

    __table_args__ = (
        Index("idx_chat_sessions_user", "user_id"),
        Index("idx_chat_sessions_character", "character_id"),
    )

# ── message.py ──
# 聊天消息模型
class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    sender_type: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # "user" | "ai" | "system"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 图片消息：/uploads/... 相对路径
    extra_meta: Mapped[str | None] = mapped_column("extra_meta", Text, nullable=True)  # JSON
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 关系
    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (
        sa.Index("idx_chat_messages_session_created", "session_id", "created_at"),
        sa.Index("idx_chat_messages_created", "created_at"),
    )

# ── group.py ──
# 家庭群聊（Phase 2）：多角色同群聊天 + 用户插话
#
# - chat_groups：用户级群（成员 = 该用户的多个 AI 角色）
# - chat_group_members：群成员
# - chat_group_messages：群消息（user / ai；ai 消息带 character_id）
# - 群聊不推送（用户主动进群查看）；用户发言后单次 LLM 调用生成多角色 JSON 回应
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

# ── group_memory.py（#72，2026-09-04）：群共享长期记忆子库 ──
# 与 games.game_memories 同范式：群"共同经历"的提炼只在此存一份（按 group_id），
# 不按成员冗余复制、不进角色私有 memories 的向量检索；角色主记忆只留 group_summary 摘要指针。
class GroupMemory(Base):
    __tablename__ = "group_memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_groups.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # 一轮聚合的归属（可空）：同一轮群聊的多条公开发言汇成 1 条群事件时共享 round_id
    round_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    speaker_type: Mapped[str] = mapped_column(String(10), default="system")  # user / ai / system
    speaker_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # user_id 或 character_id；系统摘要为 None
    content: Mapped[str] = mapped_column(Text, nullable=False)              # 本地聚合或异步摘要后的群事件文本
    epistemic_status: Mapped[str] = mapped_column(String(12), default="FACT")
    importance: Mapped[float] = mapped_column(Float, default=40)  # 与 memories 量纲一致（0-120，同 memories.importance=Float）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("idx_group_mem_group_created", "group_id", "created_at"),
    )

# ── ai_chat.py ──
# AI 间私聊记录（Phase 1）：同用户两个 AI 角色私下对话，落库供只读展示
class AIChat(Base):
    __tablename__ = "ai_chats"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    character_a_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    character_b_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    speaker_id: Mapped[int] = mapped_column(Integer, nullable=False)  # 发言角色 id（a 或 b）
    round_seq: Mapped[int] = mapped_column(Integer, default=0)  # 同事件内轮次（0 起，0=事件首条）
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
__all__ = [
    "ChatSession",
    "ChatMessage",
    "ChatGroup",
    "ChatGroupMember",
    "ChatGroupMessage",
    "GroupMemory",
    "AIChat",
]
