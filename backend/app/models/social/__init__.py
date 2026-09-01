# -*- coding: utf-8 -*-
"""社交域：平台档案/社交记忆/抖音账号与内容（F6 聚合，2026-08-31）。

原 social/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.social.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ── social.py ──
# 社交交互层 v2 数据模型（2026-08-10 拍板实施）：平台档案 + 社交记忆。
#
# 与私人记忆库严格隔离：social_memories 绝不写入 memories / stage_memories，
# 延续 2026-08-09 抖音数据单向隔离铁律。
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

# ── douyin.py ──
# 抖音 MCP 数据模型（AI 专属账号；与用户记忆库严格隔离，source=plugin:douyin）
class DouyinAccount(Base):
    """抖音账号绑定/登录状态（全局主账号 user_id=1）"""
    __tablename__ = "douyin_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, default=1)
    account_name: Mapped[str] = mapped_column(String(100), default="")
    bound: Mapped[bool] = mapped_column(Boolean, default=False)
    logged_in: Mapped[bool] = mapped_column(Boolean, default=False)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
class DouyinPost(Base):
    """AI 账号发布记录 + 数据（图文先行；stats_json 存播放/点赞/评论）"""
    __tablename__ = "douyin_posts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, default=1)
    douyin_post_id: Mapped[str] = mapped_column(String(100), default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    post_type: Mapped[str] = mapped_column(String(20), default="image")  # image / video
    stats_json: Mapped[str] = mapped_column(Text, default="{}")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="manual")  # auto=AI 自主发布 / manual=手动或轮询抓取（AI 图文日额度只统计 auto）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
class DouyinComment(Base):
    """AI 账号收到的评论（增量去重；replied=是否已回复，Phase 2 回评用）"""
    __tablename__ = "douyin_comments"
    __table_args__ = (UniqueConstraint("user_id", "douyin_post_id", "content", name="uq_douyin_comment"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, default=1)
    douyin_post_id: Mapped[str] = mapped_column(String(100), default="")
    commenter: Mapped[str] = mapped_column(String(100), default="")
    content: Mapped[str] = mapped_column(String(1000), default="")
    commented_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_fan: Mapped[bool] = mapped_column(Boolean, default=False)  # 粉丝评论标记（评论管理页标签提取；False=非粉丝）
    is_author: Mapped[bool] = mapped_column(Boolean, default=False)  # 作者评论（账号自己发的：AI 或账号主人）
    author_role: Mapped[str] = mapped_column(String(10), default="")  # ai=AI 发的 / user=账号主人发的
    mentioned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 主动提及时间（防重复提及，2026-08-15）
    aweme_id: Mapped[str] = mapped_column(String(64), default="")  # 评论所属作品真实 aweme_id（API 拦截获取，2026-08-27）
    comment_id: Mapped[str] = mapped_column(String(64), default="")  # 评论真实 comment_id（API 拦截获取，2026-08-27）
    replied: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_content: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
class DouyinPending(Base):
    """AI 抖音写操作待确认任务（默认人工确认：图文发布 / 评论回复）"""
    __tablename__ = "douyin_pending"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(20), default="")  # image_post / reply_comment
    title: Mapped[str] = mapped_column(String(300), default="")  # 图文标题 或 作品标题
    content: Mapped[str] = mapped_column(String(2000), default="")  # 描述 或 回复文本
    image_paths_json: Mapped[str] = mapped_column(Text, default="[]")
    post_key: Mapped[str] = mapped_column(String(50), default="")
    commenter: Mapped[str] = mapped_column(String(100), default="")
    is_fan: Mapped[bool] = mapped_column(Boolean, default=False)  # 目标评论是否为粉丝（额度拆分：粉丝 60% / 非粉丝 40%）
    # #67（2026-08-27）：音乐情绪 / 视频路径 / 发布类型（image|video），AI 选情绪关键词配 BGM + 视频发布
    music_mood: Mapped[str] = mapped_column(String(20), default="")
    video_path: Mapped[str] = mapped_column(String(500), default="")
    post_type: Mapped[str] = mapped_column(String(10), default="image")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/confirmed/running/rejected/executed/failed
    execute_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 随机执行队列：到达时间（naive UTC）
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
class DouyinViewedNote(Base):
    """AI 看过的抖音图文（VLM 理解结果；仅作短期感知，不进用户记忆库；2026-08-10 计划 15）"""
    __tablename__ = "douyin_viewed_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, default=1)
    aweme_id: Mapped[str] = mapped_column(String(64), unique=True, default="")
    author: Mapped[str] = mapped_column(String(100), default="")
    desc: Mapped[str] = mapped_column(String(1000), default="")  # 作品文案（标题+正文+标签）
    images_urls_json: Mapped[str] = mapped_column(Text, default="[]")
    image_descs_json: Mapped[str] = mapped_column(Text, default="[]")  # VLM 图片描述（≤3 张）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
__all__ = [
    "PlatformProfile",
    "SocialMemory",
    "DouyinAccount",
    "DouyinPost",
    "DouyinComment",
    "DouyinPending",
    "DouyinViewedNote",
]
