"""AI 角色模型"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, Boolean, DateTime, Float, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AICharacter(Base):
    __tablename__ = "ai_characters"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    weight: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True, default=None)
    birthday: Mapped[str | None] = mapped_column(String(10), nullable=True, default=None)  # YYYY-MM-DD
    voice: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)  # 自定义声色：音色 key（NULL=按性别默认）
    voice_rate: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)  # 语速倍率（1.0=正常；仅 edge-tts 兜底生效）
    voice_pitch: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)  # 语调 Hz 偏移（0=正常；仅 edge-tts 兜底生效）
    timezone_offset: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)  # 所在时区（UTC 偏移小时，NULL=北京时间 UTC+8；朋友圈时间按作者地区显示）
    appearance: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    personality: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)  # 背景信息（用户提供，AI 不覆盖）
    self_statement: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)  # 自述（AI 对话中形成的自我认知）
    relationship_summary: Mapped[str | None] = mapped_column(Text, nullable=True, default="普通朋友")
    # 关系网：该角色与用户的关系类型（对象/闺蜜/兄弟/朋友…）与是否用户对象
    relation_type: Mapped[str | None] = mapped_column(String(30), nullable=True, default="朋友")
    is_partner: Mapped[bool] = mapped_column(Boolean, default=False)
    current_status: Mapped[str | None] = mapped_column(Text, nullable=True, default="你们正在聊天")
    greeting_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # 认知循环开关（v2.1，默认关，灰度用）
    cognitive_loop_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    memory_v2_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 记忆架构 v2.1 开关（意义/目标/情境复习，默认关灰度）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # 关系
    user = relationship("User", back_populates="characters")
    chat_sessions = relationship("ChatSession", back_populates="character")
    memories = relationship("Memory", back_populates="character")
