# -*- coding: utf-8 -*-
"""通用领域事件追加流水（append-only，3.10 chat/moment 事件流水 P0）。

定位澄清：本表是 **outbox-lite 事件流水**（旁路追加日志），**不是**经典 Event Sourcing——
- 主业务表（chat_messages / ai_moments …）始终是**唯一权威读源**，任何现有查询/前端/Agent
  都不得从本表读当前态；
- 本表只服务审计 / 追溯 / 按聚合重放 / 对账（回放重建当前态不在本轮范围）；
- 只增不改不删：业务事务 commit 成功后由 app.events.store.append_domain_event 以
  **独立 session + fail-open** 追加，绝不与业务事务共享、绝不向上抛；
- idempotency_key 唯一：同一业务事实被重复补写/重放时安全（IntegrityError 静默忽略）。
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DomainEvent(Base):
    __tablename__ = "domain_events"

    # SQLite 自增用 Integer，其它库可走 BigInteger（与项目其它主键风格兼容）
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True,
    )

    # —— 聚合根：按聚合重放的主维度 ——
    # chat_session（aggregate_id=chat_sessions.id）/ moment（aggregate_id=ai_moments.id）
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # —— 具体实体（可空：会话级/清算级事件没有单一实体）——
    # chat_message / moment_comment / moment_like / moment_ai_like ...
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 事件类型，取值见 app.events.types.EventType（chat.* / moment.*）
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # 动作发起方：user / ai / system（system 用于已读流转、清算、清理等）
    actor_type: Mapped[str | None] = mapped_column(String(12), nullable=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 载荷 JSON：只放外键 / 计数 / 短摘要，不双份存长文全文（长文按 entity_id 回主表 join）
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # 确定性幂等键（见 store.append_domain_event 的缺省生成规则），最长 120
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)

    # 对齐 events.schema.PROVENANCE_META 的 origin 白名单
    origin: Mapped[str] = mapped_column(String(32), nullable=False, server_default="system_event")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_domain_event_idem"),
        Index("ix_domain_event_agg", "aggregate_type", "aggregate_id", "created_at"),
        Index("ix_domain_event_type_time", "event_type", "created_at"),
        # F-9（v3.4.6 审查）：purge_expired_domain_events 按 created_at 单列过滤，
        # 原只有两列复合索引前导列不含 created_at → 全表扫；补单列索引。
        Index("ix_domain_event_created", "created_at"),
    )
