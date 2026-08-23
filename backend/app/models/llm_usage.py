"""LLM token 用量统计与额度（2026-08-11：免费额度展示；总量仅主账号可设）"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class LlmUsage(Base):
    """单次 LLM 调用用量（llm_client 落库，后台异步写入不阻塞主流程）"""
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None=服务器级/未知
    task: Mapped[str | None] = mapped_column(String(30), nullable=True)  # 用途归因（审计 P1-07，2026-08-15）
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class LlmUsageLimit(Base):
    """总额度设置（免费额度总量，单行 id=1；0=未设置）"""
    __tablename__ = "llm_usage_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    total_limit: Mapped[int] = mapped_column(Integer, default=0)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
