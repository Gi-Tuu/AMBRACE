"""Lorebook 条目模型（P1-2，2026-08-16）：用户/角色可自定义的关键词触发设定条目

对话中出现关键词时，对应条目确定性注入上下文（精确匹配，不靠向量检索）；
排除词列表用于防误触发（如关键词「猫」排除「猫屎咖啡」）。

L2 触发式注入进阶（核心版，2026-08-24）新增触发字段：
- is_regex：关键词按正则解析（/pattern/flags 或裸 pattern），默认 False（向后兼容子串）；
- probability：0-100 命中后注入概率，100=必注入；默认 100；
- inclusion_group：同组条目同轮只注入一条（取 updated_at 最新）；默认 ''；
- sticky_rounds：触发后持续注入 N 轮；默认 0；
- cooldown_rounds：触发后 N 轮内不注入；默认 0。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LorebookEntry(Base):
    __tablename__ = "lorebook_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[str] = mapped_column(Text, default="[]")       # JSON list[str]，必须 ≥2 字
    exclude_keywords: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[str]，出现则本轮不触发
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # L2 触发式注入进阶（核心版）：默认值保证 is_regex=False / probability=100 /
    # inclusion_group='' / sticky_rounds=0 / cooldown_rounds=0 时行为与现状完全一致。
    is_regex: Mapped[bool] = mapped_column(Boolean, default=False)
    probability: Mapped[int] = mapped_column(Integer, default=100)
    inclusion_group: Mapped[str] = mapped_column(String(50), default="")
    sticky_rounds: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_rounds: Mapped[int] = mapped_column(Integer, default=0)
