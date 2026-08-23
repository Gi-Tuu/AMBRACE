"""社交交互层 v2 数据模型（2026-08-10 拍板实施）：平台档案 + 社交记忆。

与私人记忆库严格隔离：social_memories 绝不写入 memories / stage_memories，
延续 2026-08-09 抖音数据单向隔离铁律。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PlatformProfile(Base):
    """平台表达档案（Module A）：显式管理不同平台的可见性/亲密度/记忆权限/语气。

    默认档位：app=private/full；douyin=public/limited/social/creative。
    platform 枚举预留 wechat/qq（v2 不实现）。
    """
    __tablename__ = "platform_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # app / douyin / 预留 wechat/qq
    visibility: Mapped[str] = mapped_column(String(10), default="private")      # private=App / public=外部平台
    relationship_level: Mapped[str] = mapped_column(String(10), default="general")  # general / familiar / intimate（亲密度表达上限）
    memory_access: Mapped[str] = mapped_column(String(10), default="full")      # full=App / limited=外部平台（可注入记忆范围）
    memory_restrict: Mapped[str] = mapped_column(String(10), default="off")       # 公开记忆收紧：off=现状(排identity+姓名) / relationship=额外排relationship子类型（2026-08-12）
    tone: Mapped[str] = mapped_column(String(10), default="private")            # private / social / creative（表达语气）
    content_style: Mapped[str] = mapped_column(String(20), default="")          # 内容风格偏好（预留）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class SocialMemory(Base):
    """外部平台社交记忆（Module B）：粉丝/常互动用户关系档案。

    UNIQUE(platform, external_user_key)；写入口仅平台 MCP（评论/粉丝互动时 upsert）；
    读入口注入平台 persona 上下文（「你记得的粉丝/常互动用户」）；
    绝不写入常规 memories 库。
    """
    __tablename__ = "social_memories"
    __table_args__ = (UniqueConstraint("platform", "external_user_key", name="uq_social_memory"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(20), default="")                 # douyin / 预留 wechat/qq
    external_user_key: Mapped[str] = mapped_column(String(200), default="")       # 平台侧用户标识（昵称/ID，去重键）
    nickname: Mapped[str] = mapped_column(String(100), default="")                # 展示昵称
    interaction_count: Mapped[int] = mapped_column(Integer, default=0)            # 累计互动次数
    relationship_level: Mapped[str] = mapped_column(String(20), default="stranger")  # stranger / follower / familiar
    topics_json: Mapped[str] = mapped_column(Text, default="[]")                  # 历史话题标签（JSON 数组）
    trust_score: Mapped[int] = mapped_column(Integer, default=50)                 # 信任值 0-100
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
