# -*- coding: utf-8 -*-
"""生活域：生命状态/日记（AI+用户）/备忘/朋友圈/日程/时光轴/生图/节律/工作流/表情包（F6 聚合，2026-08-31）。

原 life/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.life.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# ── life.py ──
# AI 伙伴生活引擎模型（2026-08-12 Life Engine v2）
#
# - life_states：AI 生活状态机（energy/focus/needs/phase），与情绪八维 character_states 并存不混
# - life_activity_logs：活动执行日志（防编造：执行前先写 started，完成后写 memory_id）
class LifeState(Base):
    __tablename__ = "life_states"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), unique=True, nullable=False)
    energy: Mapped[int] = mapped_column(Integer, default=70)          # 精力 0-100
    focus: Mapped[int] = mapped_column(Integer, default=50)           # 专注 0-100
    needs_json: Mapped[str] = mapped_column(Text, default="{}")       # 8 需求 JSON（curiosity/productivity/...）
    home_layout_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 角色自定义房间布局 JSON（小家 v3.2 家具自由摆放；None=用默认布局）
    phase: Mapped[str] = mapped_column(String(20), default="morning")  # sleep/morning/afternoon/evening/night
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_user_interaction_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # ── Life Loop v1.1（2026-08-26）──
    location: Mapped[str] = mapped_column(String(20), default="home")
    # home / exit / world / friend / outside
    location_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_room: Mapped[str] = mapped_column(String(20), default="living")
    # living / bedroom / kitchen / bathroom / exit
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
class LifeActivityLog(Base):
    __tablename__ = "life_activity_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    activity_type: Mapped[str] = mapped_column(String(30), nullable=False)  # rest/organize_memory/reflect/social_prepare/browse/create/learn
    status: Mapped[str] = mapped_column(String(16), default="started")      # started/completed/failed/skipped
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    execution_json: Mapped[str] = mapped_column(Text, default="{}")
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    energy_cost: Mapped[int] = mapped_column(Integer, default=0)
    mood_delta: Mapped[int] = mapped_column(Integer, default=0)
    memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # 对应 memories 行（Life Event）
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
class LifeArtifact(Base):
    """AI 生活产物（Phase 2）：创作/浏览/学习的可展示成果（图片/文字/笔记）

    - 产物必须由真实活动执行产生（life_activity_logs 先写 started，防编造）
    - type: text(纯文字创作) / image(生图产物) / note(浏览/学习笔记)
    - content_url: image 类型存 /uploads/... 相对路径；text/note 存 content_text
    """
    __tablename__ = "life_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(16), default="text")        # text/image/note
    title: Mapped[str] = mapped_column(String(120), default="")
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")       # 来源活动/生图 prompt 等
    source_activity: Mapped[str] = mapped_column(String(30), default="")  # browse/create/learn
    source_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # life_activity_logs.id
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
class LifeInterest(Base):
    """AI 生活兴趣（Phase 3）：来源/衰减/成长

    - level 0-100；>=60 标记热爱（可能产生长期目标）；<5 标记沉寂（不删除，可重新激活）
    - decay_rate 默认 0.02/小时（约 3 天不接触降到一半）
    - 相关活动执行 +5-15；用户在线聊到该兴趣 +3-8（后续）
    """
    __tablename__ = "life_interests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)       # 兴趣名（如 摄影/诗歌/天文）
    level: Mapped[int] = mapped_column(Integer, default=20)             # 0-100
    source: Mapped[str] = mapped_column(String(30), default="seed")     # seed/browse/learn/create/user
    decay_rate: Mapped[float] = mapped_column(Float, default=0.02)      # 每小时衰减比例
    last_engaged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="active")   # active/hot/dormant
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
class LifeGoal(Base):
    """AI 生活目标（Phase 3）：生命周期（产生/激活/推进/完成/失败）

    - type: relationship/creative/growth/explore/skill
    - 每次相关活动 progress +1；完成 → Life Event + mood；deadline 过期未完成 → failed
    """
    __tablename__ = "life_goals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(20), default="growth")     # relationship/creative/growth/explore/skill
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=2)           # 1-3
    progress: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="active")   # active/completed/failed
    related_user: Mapped[bool] = mapped_column(Boolean, default=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
class LifeSchedule(Base):
    """AI 日程（Phase B-2，2026-08-14）：AI 自己的未来安排（三来源：固定作息/Goal 推导/reflect 自生成）

    - status: scheduled → active（时间到）→ completed（结束）/ cancelled / overdue（低优先级超时自动取消）
    - source: fixed_routine（固定作息 daily）/ goal_derived（有 deadline 的 Goal 提前推导）/ ai_generated（reflect 顺手生成）
    - 活跃（scheduled+active）每角色 ≤5 条；不单独推送，只在对话/回归摘要自然提及
    """
    __tablename__ = "life_schedules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)   # UTC naive
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="scheduled")     # scheduled/active/completed/cancelled/overdue
    priority: Mapped[int] = mapped_column(Integer, default=2)                # 1-3
    source: Mapped[str] = mapped_column(String(20), default="ai_generated")  # fixed_routine/goal_derived/ai_generated
    source_goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recurrence: Mapped[str | None] = mapped_column(String(16), nullable=True)  # daily（固定作息）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
class LifeFollowup(Base):
    """生活动作回聊缓冲：动作完成后写入，在时机窗口（下次上线/早安/夜间复盘）自然提起。"""
    __tablename__ = "life_followups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)       # 一句话回聊素材
    action: Mapped[str] = mapped_column(String(30), default="")            # 来源动作
    memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 关联记忆
    trigger_window: Mapped[str] = mapped_column(String(20), default="next_online")
    # next_online / morning / night_review
    not_before: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    # pending / used / expired
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
class LifeChatIntent(Base):
    """聊天驱动的生活意图缓冲（§10）。Life Loop 消费后 status=consumed。"""
    __tablename__ = "life_chat_intents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    horizon: Mapped[str] = mapped_column(String(12), default="today")
    # this_turn / today / this_week
    raw_text: Mapped[str] = mapped_column(String(200), default="")
    priority: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    # pending / consumed / expired
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

# ── diary.py ──
# AI 日记模型
class AIDiary(Base):
    __tablename__ = "ai_diaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    diary_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("character_id", "diary_date", name="uq_char_date"),
    )

# ── user_diary.py ──
# 用户日记模型（用户写给 AI 好友看的日记，供角色聊天阅读）
class UserDiary(Base):
    __tablename__ = "user_diaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    diary_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "diary_date", name="uq_user_diary_date"),
    )

# ── user_memo.py ──
# 用户备忘录模型（供角色聊天阅读，注入角色上下文）
class UserMemo(Base):
    __tablename__ = "user_memos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── moment.py ──
# AI 朋友圈模型
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

# ── scheduled_event.py ──
# 定时承诺事件模型 — AI 在对话中承诺的定时事件（如洗n分钟澡后回来）
class ScheduledEvent(Base):
    """AI 承诺的定时事件：到期触发一条兑现消息"""
    __tablename__ = "scheduled_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    trigger_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), default="back")  # shower/sleep/meal/back
    status: Mapped[str] = mapped_column(String(20), default="pending")   # pending/fired/cancelled/expired
    content_hint: Mapped[str | None] = mapped_column(String(200), nullable=True)
    owner: Mapped[str] = mapped_column(String(10), default="ai")  # ai=AI承诺 / user=用户承诺
    source_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_scheduled_events_status_trigger", "status", "trigger_at"),
    )

# ── timeline_event.py ──
# 时光页大事记：离线 LLM 精选的重要时刻（每角色生成一次，可强制重生成）
class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    event_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    title: Mapped[str] = mapped_column(String(50), nullable=False)
    desc: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(20), default="milestone")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# ── image_gen_task.py ──
# 生图任务模型（异步任务：生成中/完成/失败）
class ImageGenTask(Base):
    __tablename__ = "image_gen_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    character_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 触发生图角色的角色ID（AI发图）
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/generating/done/failed
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

# ── image_gen_config.py ──
# 生图服务器级全局配置（user_id=0 哨兵，单行：开源部署填一次，key 不进 .env）
class ImageGenConfig(Base):
    __tablename__ = "image_gen_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, unique=True)  # 0 = 服务器级全局配置哨兵
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)  # openai / dashscope
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_limit: Mapped[int] = mapped_column(Integer, default=10)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── user_rhythm.py ──
# 用户作息学习（#28 ②，2026-08-24）：持久化用户活跃时段（user_rhythm 表）。
#
# - user_id 主键（每用户一行）；active_hours 为 JSON 字符串（list[list[int]]，如 [[8,11],[20,23]]）。
# - 新表由 Base.metadata.create_all（init_db）幂等创建，无需 ALTER 迁移。
class UserRhythm(Base):
    """用户活跃时段（按小时推断；active_hours = list[list[int]] 的 JSON 字符串）。"""

    __tablename__ = "user_rhythm"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    active_hours: Mapped[str] = mapped_column(Text, default="[]")  # JSON 活跃时段 [[start,end),...]
    learned_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── user_workflow.py ──
# 用户自建手机操作工作流（2026-08-14 P1）：AI 触发执行的多步动作序列
class UserWorkflow(Base):
    """用户编排的手机操作工作流（步骤 JSON 存 steps 字段）"""
    __tablename__ = "user_workflows"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    steps: Mapped[str] = mapped_column(Text, nullable=False)  # 线性步骤 JSON 数组（旧格式/无分支时）
    graph: Mapped[str | None] = mapped_column(Text, nullable=True)  # 画布 JSON：{"nodes":[{id,action,...}], "edges":[{from,to,type,target?}]}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── emoji_pack.py ──
# 表情包下载记录：用户已下载的表情包（包数据服务端内置，下载=启用记录）
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
__all__ = [
    "LifeState",
    "LifeActivityLog",
    "LifeArtifact",
    "LifeInterest",
    "LifeGoal",
    "LifeSchedule",
    "LifeFollowup",
    "LifeChatIntent",
    "AIDiary",
    "UserDiary",
    "UserMemo",
    "AIMoment",
    "MomentLike",
    "MomentAILike",
    "MomentComment",
    "MomentReadMark",
    "ScheduledEvent",
    "TimelineEvent",
    "ImageGenTask",
    "ImageGenConfig",
    "UserRhythm",
    "UserWorkflow",
    "UserEmojiPack",
    "UserCustomEmoji",
]
