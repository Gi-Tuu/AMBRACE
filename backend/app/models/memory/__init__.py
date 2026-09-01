# -*- coding: utf-8 -*-
"""记忆域：记忆主表/冷归档/日摘要/会话话题/阶段记忆/复盘/提取记录/共享事件/织网卡片/世界事实/织库（F6 聚合，2026-08-31）。

原 memory/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.memory.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# ── memory.py ──
# 记忆模型
class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    memory_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "user_info" | "preference" | "event" | "insight"
    sub_type: Mapped[str | None] = mapped_column(String(30), nullable=True)  # 子分类: name/age/location/job/food/hobby/...
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 来源: chat/moment/diary/bio
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 来源消息ID
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 群聊归属（P3-3 按群节流：NULL=旧数据/非群聊，按旧行为不区分群）
    speaker_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 说话人归属（user_id 或 character_id，P0 2026-08-15）
    speaker_type: Mapped[str | None] = mapped_column(String(10), nullable=True)  # user/character/system
    epistemic_status: Mapped[str | None] = mapped_column(String(12), nullable=True)  # FACT/INFERRED/PLANNED/FICTIONAL/UNVERIFIED
    related_memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 关联记忆ID
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(10), default="private")  # global=公开安全 / private=角色私密（默认）
    importance: Mapped[float] = mapped_column(Float, default=40.0)  # 百分比 0-120，显示星级=round(pct/20)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)  # 置顶摘要，不参与衰减与删除
    decay_base_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 衰减基准时间（兼容保留，遗忘起点以 last_reinforce_at 为准）
    delete_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 强度低于阈值后 +3 天删除倒计时
    # 艾宾浩斯遗忘曲线字段（2026-08-05）：保留率 R=exp(-Δt/S)
    strength_days: Mapped[float | None] = mapped_column(Float, nullable=True)  # 记忆强度 S（天），遗忘衰减速率
    last_reinforce_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 上次强化/复习时间（遗忘起点）
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 复习次数（强化累计）
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 主动复习到期时间（P1）
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)  # 手动记忆锁：冻结强度与重要性，不衰减/不删除/不强化（P2）
    ai_rated: Mapped[bool] = mapped_column(Boolean, default=False)  # AI 自主评星标记（P2：已复评过则不再重复评）
    departed_names: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 提及但已离开的角色名（逗号分隔，2026-08-13）
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)  # 意义记忆（v2.1）：这件事对用户/关系为什么重要（AI 提炼）
    # World & Cognition P1（2026-08-15）：核心记忆注入策略
    is_core: Mapped[bool] = mapped_column(Boolean, default=False)  # 核心记忆：对话无条件注入，不靠向量检索
    core_category: Mapped[str | None] = mapped_column(String(20), nullable=True)  # identity/preference/milestone/commitment
    confirmation_count: Mapped[int] = mapped_column(Integer, default=0)  # 用户确认次数（≥2 + 高重要 → 自动晋升核心）
    contradiction_count: Mapped[int] = mapped_column(Integer, default=0)  # 用户纠正次数（P5：矛盾惩罚）
    reliability_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 记忆可靠度 0-1（P5：惰性结算）
    # 记忆链条绑定（2026-08-20）：链级级联软删 + 改内容重算向量与版本
    chain_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 记忆链条 ID
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 父节点记忆 ID（parent_id==id 即直接子链）
    node_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 节点类型：root/branch/leaf/...
    version: Mapped[int] = mapped_column(Integer, default=0)  # 内容版本（改内容时 +1）
    # ── #70-C 取代链（M1/M2）──
    status: Mapped[str] = mapped_column(String(12), default="active", server_default="active", index=False)
    superseded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 被哪条新记忆取代（NULL=未取代）
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 生效时间（有效区间起点）
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 失效时间（取代/回滚时置空）
    derived_from_ids: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")  # 派生自哪些记忆（JSON 数组，M2 级联）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # 高频查询索引（原 init_db 手工 CREATE INDEX IF NOT EXISTS，纳入模型元数据作为单一事实源）
    __table_args__ = (
        Index("idx_memories_char_user", "character_id", "user_id"),
        Index("idx_memories_char_created", "character_id", "created_at"),
        Index("idx_memories_importance", "importance"),
        Index("idx_memories_next_review", "next_review_at"),
        Index("idx_memories_char_archived", "character_id", "is_archived"),
        Index("idx_memories_char_status", "character_id", "status"),  # #70-C：按角色取 active/stale 检索热路径
    )

    # 关系
    user = relationship("User", back_populates="memories")
    character = relationship("AICharacter", back_populates="memories")

# ── daily_summary.py ──
# 每日对话概要模型
class DailySummary(Base):
    __tablename__ = "daily_summaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    summary_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    summary_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_points: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("session_id", "summary_date", name="uq_session_date"),
    )

# ── conversation_topic.py ──
# 对话话题追踪表（认知架构 v2.1 Conversation State）
class ConversationTopic(Base):
    __tablename__ = "conversation_topics"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="进行中")  # 进行中/搁置/完成
    importance: Mapped[float] = mapped_column(Float, default=0.6)
    last_touched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    follow_up: Mapped[bool] = mapped_column(Boolean, default=True)
    goal: Mapped[bool] = mapped_column(Boolean, default=False)  # 目标记忆（v2.1）：长期目标类话题
    progress: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 目标进度：进行中/有进展/快完成/搁置/完成
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── stage_memory.py ──
# 舞台记忆模型（2026-08-09）：记录虚假的/角色扮演的记忆，隔离于常规记忆库。
#
# 用途：用户与 AI 角色扮演场景中发生的非现实互动（如一起洗澡、亲亲、扮演剧情动作等）
# 只写入 stage_memories，不进入 memories 常规库，避免污染 AI 的真实记忆与检索结果。
class StageMemory(Base):
    __tablename__ = "stage_memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # 舞台事件描述（AI 第一人称）
    stage_kind: Mapped[str] = mapped_column(String(20), default="roleplay")  # roleplay=角色扮演
    importance: Mapped[int] = mapped_column(Integer, default=3)  # 1-5
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 来源用户消息 ID
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── reflection_log.py ──
# 认知循环反思日志表（v2.1）：记录反思触发原因与自查结果，供评测
class ReflectionLog(Base):
    __tablename__ = "reflection_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # AI 回复消息 id
    triggers: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 触发原因
    checks: Mapped[str | None] = mapped_column(Text, nullable=True)    # JSON 自查结果
    pass_or_fail: Mapped[str | None] = mapped_column(String(10), nullable=True, default="PASS")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# ── processed_extraction.py ──
# 记忆提取去重：记录已处理的消息对（按用户消息 id）
class ProcessedExtraction(Base):
    __tablename__ = "processed_extractions"

    user_message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# ── shared_event.py ──
# Shared Event 模型（Phase C，2026-08-14，演进规划 v2）：用户与 AI 共同经历
#
# - 触发：user_marked（用户明确要求记住/第一次/纪念日）/ emotional_peak（情绪高峰）/ milestone（关系里程碑）/ ai_marked（AI 判断重要）
# - 纪念日：is_anniversary=1 时在满月/周年由 scheduler 触发回忆消息
# - 召回：对话构建时按重要性/新鲜度注入，AI 自然引用（防编造：只从记录检索）
class SharedEvent(Base):
    __tablename__ = "shared_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(30), default="user_marked")  # user_marked/emotional_peak/milestone/ai_marked
    category: Mapped[str] = mapped_column(String(20), default="daily")          # daily/milestone/anniversary
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    emotional_valence: Mapped[float] = mapped_column(Float, default=0.0)        # -1..1
    emotional_arousal: Mapped[float] = mapped_column(Float, default=0.5)        # 0..1
    importance: Mapped[float] = mapped_column(Float, default=0.6)               # 0..1
    is_anniversary: Mapped[bool] = mapped_column(Boolean, default=False)
    related_memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_recalled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 纪念日最近触发时间（防重复）
    event_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── weave_card.py ──
# 织库卡片模型（2026-08-12 织库·全景记忆 v1）
class WeaveCard(Base):
    """织库卡片：一批重要记忆经 LLM 整理编排成的结构化卡片（概要 + 详情 JSON）"""
    __tablename__ = "weave_cards"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)  # 卡片标题（12 字内）
    summary: Mapped[str] = mapped_column(Text, nullable=False)  # 概要（卡片展示）
    detail: Mapped[str] = mapped_column(Text, nullable=False)  # 详情 JSON（time/weather/location/mood/events/details）
    importance: Mapped[float] = mapped_column(Float, default=40.0)  # 参与记忆重要度均值（百分比 0-120）
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # 参与记忆 id 有序集合 sha256（幂等）
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)  # 卡片向量（bge-m3，JSON float 列表）
    domain: Mapped[str] = mapped_column(String(10), default="shared")  # shared=全·织库（共同记忆）/ private=私·织库（AI 生活，2026-08-12）
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)  # 参与记忆被删/改后置脏，generate 时懒重建
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_weave_cards_character", "character_id"),
    )

    memories = relationship(
        "WeaveCardMemory", back_populates="card", cascade="all, delete-orphan"
    )
class WeaveCardMemory(Base):
    """织库卡片 ↔ 记忆 多对多关联"""
    __tablename__ = "weave_card_memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("weave_cards.id"), nullable=False)
    memory_id: Mapped[int] = mapped_column(Integer, ForeignKey("memories.id"), nullable=False)

    card = relationship(
        "WeaveCard", back_populates="memories"
    )
class WeaveCardCharacter(Base):
    """织库卡片 ↔ 角色 多对多（跨角色合并卡片，2026-08-12）"""
    __tablename__ = "weave_card_characters"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("weave_cards.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)

# ── lorebook.py ──
# Lorebook 条目模型（P1-2，2026-08-16）：用户/角色可自定义的关键词触发设定条目
#
# 对话中出现关键词时，对应条目确定性注入上下文（精确匹配，不靠向量检索）；
# 排除词列表用于防误触发（如关键词「猫」排除「猫屎咖啡」）。
#
# L2 触发式注入进阶（核心版，2026-08-24）新增触发字段：
# - is_regex：关键词按正则解析（/pattern/flags 或裸 pattern），默认 False（向后兼容子串）；
# - probability：0-100 命中后注入概率，100=必注入；默认 100；
# - inclusion_group：同组条目同轮只注入一条（取 updated_at 最新）；默认 ''；
# - sticky_rounds：触发后持续注入 N 轮；默认 0；
# - cooldown_rounds：触发后 N 轮内不注入；默认 0。
class LorebookEntry(Base):
    __tablename__ = "lorebook_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[str] = mapped_column(Text, default="[]")       # JSON list[str]，必须 ≥2 字
    exclude_keywords: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[str]，出现则本轮不触发
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # L2 触发式注入进阶（核心版）：默认值保证 is_regex=False / probability=100 /
    # inclusion_group='' / sticky_rounds=0 / cooldown_rounds=0 时行为与现状完全一致。
    is_regex: Mapped[bool] = mapped_column(Boolean, default=False)
    probability: Mapped[int] = mapped_column(Integer, default=100)
    inclusion_group: Mapped[str] = mapped_column(String(50), default="")
    sticky_rounds: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_rounds: Mapped[int] = mapped_column(Integer, default=0)

# ── world_fact.py ──
# World Fact 模型（World & Cognition P4，2026-08-15）：世界状态物化视图
#
# 事件 → 规则折叠 → 当前世界事实（event-sourced）。
# 同 subject+predicate 的新事实取代旧事实（supersede），历史事实保留可审计；
# 查询按 audience 过滤（知识边界：audience 含 viewer 或 public 才可见）。
class WorldFact(Base):
    __tablename__ = "world_facts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(12), default="character")  # user/character/system
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    predicate: Mapped[str] = mapped_column(String(20), nullable=False)  # status/activity/location/mood
    object_value: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active/superseded/expired
    confidence: Mapped[float] = mapped_column(Float, default=1.0)  # 0-1
    epistemic_status: Mapped[str] = mapped_column(String(12), default="FACT")  # FACT/INFERRED/PLANNED
    audience: Mapped[str] = mapped_column(String(255), default="[]")  # JSON ["user:4","char:11"] 或 ["public"]
    author: Mapped[str] = mapped_column(String(20), default="system")  # user/character/system（P1-3 权威事实层）
    is_authoritative: Mapped[bool] = mapped_column(Boolean, default=False)  # 用户/创作者定义的不可动摇事实（P1-3）
    source: Mapped[str | None] = mapped_column(String(30), nullable=True)  # chat_status/life_activity/user_setting
    source_event_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    superseded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 过期时间（可空=不自动过期）
    asserted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── memory_archive.py ──
# 冷归档记忆模型（#70-C2）：superseded 且 valid_to 超过阈值 → 迁入本表，退出热检索/注入。
#
# - 只进不出的历史快照（payload = 原行 JSON），purge 时连本表一起物理删；
# - 不进任何检索/注入，只在「翻历史 / 关系报告 / 年度回顾」显式查。
class MemoryArchive(Base):
    __tablename__ = "memory_archive"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    memory_id: Mapped[int] = mapped_column(Integer, index=True)  # 原热库记忆 id
    user_id: Mapped[int] = mapped_column(Integer)
    character_id: Mapped[int] = mapped_column(Integer, index=True)
    payload: Mapped[str] = mapped_column(Text)  # 原行 JSON 快照
    archived_reason: Mapped[str] = mapped_column(String(30), default="superseded_cold", server_default="superseded_cold")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
__all__ = [
    "Memory",
    "DailySummary",
    "ConversationTopic",
    "StageMemory",
    "ReflectionLog",
    "ProcessedExtraction",
    "SharedEvent",
    "WeaveCard",
    "WeaveCardMemory",
    "WeaveCardCharacter",
    "LorebookEntry",
    "WorldFact",
    "MemoryArchive",
]
