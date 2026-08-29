"""游戏事件流水（权威记录）模型"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GameEvent(Base):
    __tablename__ = "game_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("game_sessions.id"), index=True)
    round: Mapped[int] = mapped_column(Integer, default=0)
    phase: Mapped[str] = mapped_column(String(30), default="")
    event_type: Mapped[str] = mapped_column(String(30))
    # deal/describe/vote/eliminate/choose_truth/choose_dare/ask/answer/guess/win/announce/join/leave/timeout
    actor_seat: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 行动者座次；null = 系统/GM
    target_seat: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
    # 公开内容（发言文本、GM 播报等）
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    visibility: Mapped[str] = mapped_column(String(10), default="public")
    # public / private
    private_to_seat: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # visibility=private 时，只有该座次玩家可见
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)