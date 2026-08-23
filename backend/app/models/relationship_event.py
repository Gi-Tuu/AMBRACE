"""关系事件表（记忆架构 v2.1 Phase 3b）：AI 与用户关系的里程碑互动。

承载关系记忆的"事件侧"（标量侧在 character_states.trust/attachment/curiosity，
AI 视角摘要侧在 ai_characters.relationship_summary）；本表记录可追溯的关系变化事件，
供关系温度曲线、情境复习与未来关系分析使用。不双写记忆（可关联 memory_id 溯源）。
"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RelationshipEvent(Base):
    __tablename__ = "relationship_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    event: Mapped[str] = mapped_column(String(300), nullable=False)      # 事件摘要（≤300）
    content: Mapped[str | None] = mapped_column(Text, nullable=True)     # 原始上下文（截断）
    change_type: Mapped[str] = mapped_column(String(20), default="other")  # trust_up/trust_down/closer/distant/care/apology/cold_war/other
    trust_delta: Mapped[int] = mapped_column(Integer, default=0)         # 信任变化量 -10..+10
    memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 关联记忆 id（溯源，不双写）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = {"extend_existing": True}
