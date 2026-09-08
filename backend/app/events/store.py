# -*- coding: utf-8 -*-
"""领域事件追加写入器（3.10 outbox-lite 事件流水 P0）。

定位：业务主表 commit 成功之后的「旁路持久流水」。设计对齐 events.facts.assert_fact
（独立 session、失败静默）与 agent_task_logs（AGENT_FLAGS 灰度）。

绝对约束：
1) 只能在业务事务 commit + refresh（拿到真实主键）之后调用；
2) 用独立 session，绝不与业务事务共享，绝不上抛——事件失败不得影响回复/推送；
3) idempotency_key 确定性生成，重复补写/重放安全（唯一键冲突静默忽略）；
4) payload 只放外键/计数/短摘要，不双份存长文全文。

注意：本模块只负责「落表」，不注册任何 event_bus 订阅者（防回环，方案 §8.2）。
"""
import json
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.db.database import async_session_factory
from app.models.domain_event import DomainEvent
from app.utils.logger import get_logger

_logger = get_logger("events.store")

_MAX_SUMMARY = 200          # 文本摘要上限，对齐既有 moment 事件 content[:200]
_SUMMARY_FIELDS = ("content", "text", "summary_preview")


def domain_events_enabled() -> bool:
    """统一灰度开关：agent loop AGENT_FLAGS 同款读取；任何异常都按「关」处理（fail-closed 到不写）。

    热切说明：flag 经 app.application.flag_service.set_runtime_flag 写 runtime_flags 行并同步更新
    AGENT_FLAGS 内存（立即生效，无需重启）；但 key 必须先存在于 AGENT_FLAGS 硬编码默认表，
    因此本 key 首次加入后需先重启一次服务（加载新代码），之后即可 API 热切。
    """
    try:
        from app.agent import loop as _loop
        return bool(_loop.AGENT_FLAGS.get("domain_event_log_enabled", False))
    except Exception:
        return False


def _summarize(value: Any) -> str:
    s = "" if value is None else str(value)
    return s if len(s) <= _MAX_SUMMARY else s[:_MAX_SUMMARY] + "…"


def _default_key(event_type: str, entity_type: str | None, entity_id: int | None,
                 aggregate_type: str, aggregate_id: int | None) -> str:
    if entity_id is not None:
        return f"{event_type}:{entity_type or aggregate_type}:{entity_id}"
    return f"{event_type}:{aggregate_type}:{aggregate_id}"


def domain_event_retention_days() -> int:
    """保留策略（P1，方案 §8.5）：>0 时删除超过 N 天的事件行；默认 0=永久保留（本地优先）。

    与灰度 flag 同源读 AGENT_FLAGS；非法值/异常一律按 0（不清理）处理，宁可多留不误删。
    """
    try:
        from app.agent import loop as _loop
        return max(0, int(_loop.AGENT_FLAGS.get("domain_event_retention_days", 0) or 0))
    except Exception:
        return 0


async def purge_expired_domain_events() -> int:
    """按保留策略清理超期事件行，返回删除条数；flag=0 或未开启 → 0（不删）。

    独立 session、失败静默（事件旁路不得影响主链路）；由定时清理任务顺带调用（每 6h 一次）。
    """
    days = domain_event_retention_days()
    if days <= 0:
        return 0
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        from sqlalchemy import delete as _delete

        # F-8（v3.4.6 审查）：DomainEvent.created_at 的 server_default 是 SQLite
        # datetime('now')（UTC naive），cutoff 必须同口径用 UTC naive——原 _dt.now()
        # 取本地时间（东八区）会提前 8h 误删未到期事件。
        cutoff = _dt.now(_tz.utc).replace(tzinfo=None) - _td(days=days)
        async with async_session_factory() as db:
            res = await db.execute(
                _delete(DomainEvent).where(DomainEvent.created_at < cutoff)
            )
            await db.commit()
            return int(res.rowcount or 0)
    except Exception as e:  # noqa: BLE001
        _logger.warning("purge_expired_domain_events failed: %s", e)
        return 0


async def append_domain_event(
    event_type: str,
    aggregate_type: str,
    aggregate_id: int | None,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    actor_type: str | None = None,
    actor_id: int | None = None,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    origin: str = "system_event",
) -> None:
    """追加一条领域事件；永不抛出。"""
    if not domain_events_enabled():
        return
    # 会话级事件允许只有 aggregate_id；实体级事件至少要有一个可定位 id
    if aggregate_id is None and entity_id is None:
        return
    try:
        body = dict(payload or {})
        for k in _SUMMARY_FIELDS:
            if isinstance(body.get(k), str):
                body[k] = _summarize(body[k])
        key = idempotency_key or _default_key(
            event_type, entity_type, entity_id, aggregate_type, aggregate_id
        )
        async with async_session_factory() as db:
            db.add(DomainEvent(
                aggregate_type=aggregate_type,
                aggregate_id=int(aggregate_id or 0),
                entity_type=entity_type,
                entity_id=entity_id,
                event_type=event_type,
                actor_type=actor_type,
                actor_id=actor_id,
                payload_json=json.dumps(body, ensure_ascii=False, default=str),
                idempotency_key=key[:120],
                origin=origin,
            ))
            try:
                await db.commit()
            except IntegrityError:
                # 幂等键冲突＝同一业务事实重复上报，安全忽略
                await db.rollback()
            except Exception as e:  # noqa: BLE001 - 事件旁路必须吞掉一切
                await db.rollback()
                _logger.warning("append_domain_event commit failed: %s", e)
    except Exception as e:  # noqa: BLE001
        _logger.warning("append_domain_event failed: %s", e)
