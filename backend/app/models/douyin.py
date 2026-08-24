"""抖音 MCP 数据模型（AI 专属账号；与用户记忆库严格隔离，source=plugin:douyin）"""
from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, Text, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


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
