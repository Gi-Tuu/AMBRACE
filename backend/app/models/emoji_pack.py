"""表情包下载记录：用户已下载的表情包（包数据服务端内置，下载=启用记录）"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class UserEmojiPack(Base):
    __tablename__ = "user_emoji_packs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    pack_id: Mapped[str] = mapped_column(String(30), nullable=False)
    pack_name: Mapped[str] = mapped_column(String(50), default="")
    downloaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserCustomEmoji(Base):
    """用户自定义表情（图片），上传后本用户可用；发送时按 image 消息+表情名描述进 AI 上下文"""
    __tablename__ = "user_custom_emojis"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(30), default="表情")
    url: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
