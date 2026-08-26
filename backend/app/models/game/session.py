"""游戏对局模型"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GameSession(Base):
    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    group_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("chat_groups.id"), nullable=True)
    # null = 从游戏机直接发起（非群聊场景）
    game_type: Mapped[str] = mapped_column(String(30), index=True)
    # undercover / truth_or_dare / twenty_q / werewolf / liars_bar / turtle_soup
    player_mode: Mapped[str] = mapped_column(String(10))  # single / dual / multi
    status: Mapped[str] = mapped_column(String(12), default="created")
    # created / playing / finished / aborted
    round: Mapped[int] = mapped_column(Integer, default=0)
    phase: Mapped[str] = mapped_column(String(30), default="")
    # 游戏内阶段（如 undercover: describe/vote/result；werewolf: night/day）
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    # 游戏配置（词对、角色分配等，引擎内部使用）
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    # 引擎运行时状态（当前发言顺序、投票计数、牌堆等）
    winner_side: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # civilians / undercover / player_1 / draw / null
    trigger: Mapped[str] = mapped_column(String(20), default="user_initiated")
    # user_initiated / character_suggested / scheduled
    archive_json: Mapped[str] = mapped_column(Text, default="{}")
    # 游乐手札折叠卡片结构化数据（零 LLM，结算时由引擎生成）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)