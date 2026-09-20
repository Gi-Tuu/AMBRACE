# -*- coding: utf-8 -*-
"""群聊游戏域：会话/玩家/事件/游戏记忆（F6 聚合，2026-08-31）。

原 game/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.game.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ── session.py ──
# 游戏对局模型
class GameSession(Base):
    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("chat_groups.id", ondelete="SET NULL"), nullable=True)
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
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True)
    player_type: Mapped[str] = mapped_column(String(10))  # user / ai
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    character_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("ai_characters.id", ondelete="CASCADE"), nullable=True)
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
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True)
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
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id", ondelete="CASCADE"), index=True)
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


# ── content.py（#62 Phase 3：自定义词库/题库外置）──
# 用户自定义内容覆盖表（user_id + game_type + key）；运行时优先级最高。
class GameContentOverride(Base):
    """某用户对某游戏某内容 key（word_pool/word_pairs/puzzles/...）的自定义覆盖。

    解析顺序「用户自定义 > 插件内容包 > 内置常量」中的最高优先级来源；
    values_json 为 JSON 数组，结构由 app.games.content_store.validate_content_values 校验。
    """

    __tablename__ = "game_content_overrides"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    game_type: Mapped[str] = mapped_column(String(30), index=True)
    content_key: Mapped[str] = mapped_column(String(40))
    values_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ux_game_content_user_key", "user_id", "game_type", "content_key", unique=True),
    )


# ── stats.py（#62 Phase 3：游戏成就与统计，纯数据不改关系）──
# 按 user（character_id=NULL）/ character / game_type 累计的战绩。
class GameStats(Base):
    """游戏统计累计（每个 user×character×game_type 一行）。

    - character_id IS NULL：用户本人（真人参局）维度的累计；
    - character_id 非空：某 AI 角色在该用户名下的累计；
    - games_played 只统计完整结算（finished，含平局）的局数；无胜负终止（aborted）
      单列 aborted，绝不进胜场；total_rounds 累计所有终局（含 aborted）的回合数。
    """

    __tablename__ = "game_stats"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ai_characters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    game_type: Mapped[str] = mapped_column(String(30), index=True)
    games_played: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    draws: Mapped[int] = mapped_column(Integer, default=0)
    aborted: Mapped[int] = mapped_column(Integer, default=0)
    total_rounds: Mapped[int] = mapped_column(Integer, default=0)
    last_played_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # 用户行与角色行分别唯一（SQLite/PG 的 NULL 在唯一索引里互不相等，故用部分索引）
    __table_args__ = (
        Index("ux_game_stats_user", "user_id", "game_type", unique=True,
              sqlite_where=text("character_id IS NULL"),
              postgresql_where=text("character_id IS NULL")),
        Index("ux_game_stats_char", "user_id", "game_type", "character_id", unique=True,
              sqlite_where=text("character_id IS NOT NULL"),
              postgresql_where=text("character_id IS NOT NULL")),
    )


# 成就解锁记录（同 (user, character, game_type, key) 只解锁一次；含定义快照）。
class GameAchievement(Base):
    """成就解锁记录：达成时插入一行，唯一索引保证「同一成就只解锁一次」。

    game_type="*" 表示跨游戏聚合成就；否则为单游戏成就。进度未达成的成就不落库，
    查询时按统计实时计算 progress（见 app.games.achievements.list_achievements）。
    """

    __tablename__ = "game_achievements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ai_characters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    game_type: Mapped[str] = mapped_column(String(30), default="*")
    achievement_key: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(String(200), default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    target: Mapped[int] = mapped_column(Integer, default=1)
    unlocked: Mapped[bool] = mapped_column(Boolean, default=True)
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ux_game_ach_user", "user_id", "game_type", "achievement_key", unique=True,
              sqlite_where=text("character_id IS NULL"),
              postgresql_where=text("character_id IS NULL")),
        Index("ux_game_ach_char", "user_id", "game_type", "character_id", "achievement_key", unique=True,
              sqlite_where=text("character_id IS NOT NULL"),
              postgresql_where=text("character_id IS NOT NULL")),
    )


__all__ = [
    "GameSession",
    "GamePlayer",
    "GameEvent",
    "GameMemory",
    "GameContentOverride",
    "GameStats",
    "GameAchievement",
]
