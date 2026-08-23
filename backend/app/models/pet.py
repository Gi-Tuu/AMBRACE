"""宠物模型（用户养的小动物；owner_type/owner_id 预留未来 AI 角色养宠）"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Pet(Base):
    __tablename__ = "pets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    species: Mapped[str] = mapped_column(String(30), nullable=False)  # cat/dog/parrot/rabbit/hamster/snake/gecko
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    exp: Mapped[int] = mapped_column(Integer, default=0)
    hunger: Mapped[int] = mapped_column(Integer, default=80)      # 0-100
    mood: Mapped[int] = mapped_column(Integer, default=80)
    energy: Mapped[int] = mapped_column(Integer, default=80)
    cleanliness: Mapped[int] = mapped_column(Integer, default=80)
    last_feed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_play_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_clean_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_remind_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 最近一次 AI 提醒照顾（Phase 2）
    status_text: Mapped[str] = mapped_column(String(50), default="精神满满")
    owner_type: Mapped[str | None] = mapped_column(String(10), nullable=True)  # user/ai（预留）
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)       # 预留
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
