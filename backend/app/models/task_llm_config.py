"""任务专用 LLM 配置：user_id=0=服务器级全局、>0=用户级；task 指定用途（记忆/卡片/情绪/状态/复习/主动消息/日记/时光）
与 api_configs（聊天主链路 chat 配置）并存：任务配置命中时优先于 chat 配置，实现"按任务指定模型"。
api_key 支持多 Key 池（逗号/换行分隔或 JSON 数组），llm_client 按请求轮换。
"""
from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class TaskLlmConfig(Base):
    __tablename__ = "task_llm_configs"
    __table_args__ = (UniqueConstraint("user_id", "task", name="uq_task_llm_user_task"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, default=0)  # 0=服务器级全局哨兵
    task: Mapped[str] = mapped_column(String(20), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 支持多 Key（逗号/JSON 数组）
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())