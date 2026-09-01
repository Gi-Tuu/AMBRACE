# -*- coding: utf-8 -*-
"""群聊游戏域：会话/玩家/事件/游戏记忆（F6 聚合，2026-08-31）。

原 game/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.game.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ── session.py ──
# 游戏对局模型
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

# ── player.py ──
# 游戏玩家/观战者模型
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

# ── event.py ──
# 游戏事件流水（权威记录）模型
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

# ── memory.py ──
# 游戏记忆库模型（每角色可见摘要，不进主记忆向量检索）
class GameMemory(Base):
    """游戏记忆库：每角色在一局游戏中"经历了什么"的结构化记录。
    不进主记忆向量检索；只能通过 game_session_id 显式查询。
    """

    __tablename__ = "game_memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("game_sessions.id"), index=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), index=True)
    # 该记忆属于哪个角色（用户不写，用户视角由前端实时从 events 渲染）
    my_role: Mapped[str] = mapped_column(String(20), default="")
    my_word: Mapped[str] = mapped_column(String(40), default="")
    # 该角色自己的词/身份（从 private_json 快照）
    result: Mapped[str] = mapped_column(String(10), default="")  # won / lost
    survived_rounds: Mapped[int] = mapped_column(Integer, default=0)
    public_events_json: Mapped[str] = mapped_column(Text, default="[]")
    # 该角色可见的公开事件流水（GM 播报 + 所有人发言 + 投票结果）
    my_actions_json: Mapped[str] = mapped_column(Text, default="[]")
    # 该角色自己的行动（描述/投票/选择）
    summary: Mapped[str] = mapped_column(String(300), default="")
    # 一句话角色视角总结（模板生成，零 LLM）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
__all__ = [
    "GameSession",
    "GamePlayer",
    "GameEvent",
    "GameMemory",
]
