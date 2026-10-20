# -*- coding: utf-8 -*-
"""角色**创建请求收据**（幂等键去重，2026-10-19）。

设计约束：
- 只存**不透明键 + 请求体摘要 + 归属 + 目标角色 id/时间**，**不存请求或响应正文**；
- 唯一约束 ``(user_id, idempotency_key)`` 是并发去重的最后防线（进程重启/多实例仍有效）；
- 按**当前认证用户**隔离，不用家庭 root 替代调用者；
- 角色被删除后收据**不自动清理**，重放时由服务层判定并返回冲突，绝不重建。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CharacterCreationRequest(Base):
    """一次「带幂等键的创建意图」的持久化凭证。"""

    __tablename__ = "character_creation_requests"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_char_creation_req_user_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 调用者本人（不是家庭 root）；账号彻底删除时随 FK 级联
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    # 规范化请求体的 sha256（十六进制），用于「同键不同 payload ⇒ 409」
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # 目标角色；角色删除后为 NULL（收据保留，重放置回冲突而不是重建）
    character_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ai_characters.id", ondelete="SET NULL"), nullable=True)
    # pending=已占键但角色尚未落定；active=角色已创建
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
