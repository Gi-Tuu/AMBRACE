"""记忆模型"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, Boolean, DateTime, Float, ForeignKey, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


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
    )

    # 关系
    user = relationship("User", back_populates="memories")
    character = relationship("AICharacter", back_populates="memories")
