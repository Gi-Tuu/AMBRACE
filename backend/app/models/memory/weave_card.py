"""织库卡片模型（2026-08-12 织库·全景记忆 v1）"""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class WeaveCard(Base):
    """织库卡片：一批重要记忆经 LLM 整理编排成的结构化卡片（概要 + 详情 JSON）"""
    __tablename__ = "weave_cards"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)  # 卡片标题（12 字内）
    summary: Mapped[str] = mapped_column(Text, nullable=False)  # 概要（卡片展示）
    detail: Mapped[str] = mapped_column(Text, nullable=False)  # 详情 JSON（time/weather/location/mood/events/details）
    importance: Mapped[float] = mapped_column(Float, default=40.0)  # 参与记忆重要度均值（百分比 0-120）
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # 参与记忆 id 有序集合 sha256（幂等）
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)  # 卡片向量（bge-m3，JSON float 列表）
    domain: Mapped[str] = mapped_column(String(10), default="shared")  # shared=全·织库（共同记忆）/ private=私·织库（AI 生活，2026-08-12）
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)  # 参与记忆被删/改后置脏，generate 时懒重建
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_weave_cards_character", "character_id"),
    )

    memories = relationship(
        "WeaveCardMemory", back_populates="card", cascade="all, delete-orphan"
    )


class WeaveCardMemory(Base):
    """织库卡片 ↔ 记忆 多对多关联"""
    __tablename__ = "weave_card_memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("weave_cards.id"), nullable=False)
    memory_id: Mapped[int] = mapped_column(Integer, ForeignKey("memories.id"), nullable=False)

    card = relationship(
        "WeaveCard", back_populates="memories"
    )


class WeaveCardCharacter(Base):
    """织库卡片 ↔ 角色 多对多（跨角色合并卡片，2026-08-12）"""
    __tablename__ = "weave_card_characters"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("weave_cards.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
