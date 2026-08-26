"""游戏玩家/观战者模型"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GamePlayer(Base):
    __tablename__ = "game_players"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("game_sessions.id"), index=True)
    player_type: Mapped[str] = mapped_column(String(10))  # user / ai
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    character_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=True)
    seat: Mapped[int] = mapped_column(Integer, default=0)  # 座次
    role: Mapped[str] = mapped_column(String(20), default="")  # civilian/undercover/wolf/seer/...
    is_spectator: Mapped[bool] = mapped_column(Boolean, default=False)
    alive: Mapped[bool] = mapped_column(Boolean, default=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    private_json: Mapped[str] = mapped_column(Text, default="{}")
    # 仅本人可见：手牌、词语、夜晚行动等
    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())