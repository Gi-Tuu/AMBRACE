"""AI 伙伴生活引擎模型（2026-08-12 Life Engine v2）

- life_states：AI 生活状态机（energy/focus/needs/phase），与情绪八维 character_states 并存不混
- life_activity_logs：活动执行日志（防编造：执行前先写 started，完成后写 memory_id）
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LifeState(Base):
    __tablename__ = "life_states"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), unique=True, nullable=False)
    energy: Mapped[int] = mapped_column(Integer, default=70)          # 精力 0-100
    focus: Mapped[int] = mapped_column(Integer, default=50)           # 专注 0-100
    needs_json: Mapped[str] = mapped_column(Text, default="{}")       # 8 需求 JSON（curiosity/productivity/...）
    phase: Mapped[str] = mapped_column(String(20), default="morning")  # sleep/morning/afternoon/evening/night
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_user_interaction_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
