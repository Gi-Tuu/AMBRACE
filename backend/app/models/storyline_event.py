"""剧情线事件模型：状态联动剧情线的节点进度档案（v5 剧情线）"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class StorylineEvent(Base):
    """剧情线节点记录：每角色每剧情一条记录，节点执行/推进的档案

    - storyline_key: 剧情类型（cold_war / jealousy / fatigue…）
    - node_index: 节点序号（0=爆发, 1=冷战, 2=加时, 3=深夜emo, 4=破冰, 5=和好后遗症）
    - status: active(进行中) / done(已完成) / skipped(跳过) / aborted(中止)
    - trigger_source: 触发出处（如 anger_mood_low）
    - user_context: 用户当时行为摘要（剧情分支决策依据）
    - output_text: 节点产出的文本（消息/朋友圈内容）
    """
    __tablename__ = "storyline_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    storyline_key: Mapped[str] = mapped_column(String(30), nullable=False)  # cold_war / jealousy / fatigue
    node_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")  # active/done/skipped/aborted
    trigger_source: Mapped[str] = mapped_column(String(40), default="")
    user_context: Mapped[str] = mapped_column(String(500), default="")
    output_text: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("character_id", "storyline_key", "node_index", name="uq_char_story_node"),
    )
