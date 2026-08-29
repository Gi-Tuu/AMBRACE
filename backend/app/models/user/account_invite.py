# -*- coding: utf-8 -*-
"""受邀码（#68 账号体系 × API 配置整合 P3 账号关联）。

- code：8 位大写十六进制（唯一）；creator_id：发出码的独立主账号。
- expires_at：过期时间（生成后 5 分钟有效）；一次性：used_by 非空即已使用。
- used_by/used_at：记录兑换者，支持审计；同事务 used_by 检查防并发兑换。

账号关联采用「受邀码」方案：users.parent_id 关联（NULL=独立主账号），
account_invites 独立表存放一次性码（比 users 内嵌 invite_code 更干净、可审计）。
"""
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AccountInvite(Base):
    __tablename__ = "account_invites"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)  # 8 位大写 hex
    creator_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # 5 分钟有效
    used_by: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 兑换者 user_id；NULL=未使用
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
