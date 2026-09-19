# -*- coding: utf-8 -*-
"""控制台审计域：admin_audit_log（账号独立 P2，2026-09-19）。

铁律（契约 §0）：控制台只调 HTTP API，所有写动作由后端落本表，控制台不得直连 DB / 直接 UPDATE。

- ``actor_user_id``：操作者（server_admin 的 user_id）；
- ``action``：动作名（如 ``account.disable`` / ``server.modality.update`` / ``flag.update``）；
- ``target``：可读目标串（如 ``user:3`` / ``modality:llm`` / ``flag:agent_loop_chat``）；
- ``before_json`` / ``after_json``：变更前后快照（JSON 字符串；api_key 等密钥一律脱敏为 "***"，
  由 app/application/admin_audit_service._dump 统一处理）；
- ``created_at``：UTC naive（与库内其他时间列同口径，见 app/utils/timeutil.py）。

只加表：不动既有表/列；缺行即空审计（上线不改变任何现有行为）。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AdminAuditLog(Base):
    """服务器控制台写动作审计流水（append-only）。"""

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


__all__ = ["AdminAuditLog"]
