# -*- coding: utf-8 -*-
"""用户多 LLM 配置（#68 账号体系 × API 配置整合 P0）。

- user_id：配置归属用户；每个用户可有多个 LLM 配置（我的 LLM）。
- is_default：同一用户至多一个 default（设置新默认自动清其他），解析链命中用户默认。
- shared_with_subs：标记该配置可共享给子账号（主账号配置；子账号只读不可改）。
- UNIQUE(user_id, name)：同一用户配置名唯一。

解析链（_resolve_llm_config）：任务专用 → 角色绑定（ai_characters.user_llm_config_id）
→ 用户默认 → 主账号共享默认（仅子账号）→ 服务器级 → .env。
llm_usage.config_id/group_owner_id（P6 用量归因）本次不加。
"""
from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class UserLlmConfig(Base):
    __tablename__ = "user_llm_configs"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_llm_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)  # 深思考开关厂商适配用
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    shared_with_subs: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
