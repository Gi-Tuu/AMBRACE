"""AI 朋友圈模型"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, Boolean, DateTime, ForeignKey, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import UniqueConstraint
from app.models.base import Base


class AIMoment(Base):
    __tablename__ = "ai_moments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sender_type: Mapped[str] = mapped_column(String(10), default="ai")  # ai / user
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    likes_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_moments_user_created", "user_id", "created_at"),
        Index("idx_moments_char_created", "character_id", "created_at"),
    )


class MomentLike(Base):
    __tablename__ = "moment_likes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    moment_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_moments.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("moment_id", "user_id", name="uq_moment_user"),
    )


class MomentAILike(Base):
    """AI 角色点赞（新表，create_all 自动建；与 MomentLike 用户点赞并存）"""
    __tablename__ = "moment_ai_likes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    moment_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_moments.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("moment_id", "character_id", name="uq_moment_ai_char"),
    )


class MomentComment(Base):
    __tablename__ = "moment_comments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    moment_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_moments.id"), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("moment_comments.id"), nullable=True)
    sender_type: Mapped[str] = mapped_column(String(10), nullable=False)  # ai / user
    sender_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sender_name: Mapped[str] = mapped_column(String(50), nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    replies = relationship("MomentComment", backref="parent", remote_side=[id], lazy="select")

    __table_args__ = (
        Index("idx_moment_comments_moment", "moment_id"),
    )

class MomentReadMark(Base):
    """用户朋友圈已读标记（P2-4 回复提醒：last_read_at 之后有 AI 回复我的评论则红点）"""
    __tablename__ = "moment_read_marks"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    last_read_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
