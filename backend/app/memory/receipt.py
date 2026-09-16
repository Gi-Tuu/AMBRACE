"""#70 M3：记忆写入回执（memory_write_receipts）。

终态追踪「这条记忆为什么在/不在」。设计（docs/memory-tiering-observability-plan.md 附录 C-可选）：

- 仅当 feature flag ``memory_write_receipt`` 开时由写入点异步写一条回执；
- 写入失败静默（只记 warning），绝不阻塞主链路（沿用项目铁律「异步不阻塞主回复」）；
- flag 关 = 零写入、零行为变化（``emit_memory_receipt`` 首行即 return）。

action 取值（与迁移/模型一致）：
    create / update / merge / supersede / stale / reject / downgrade
"""
from __future__ import annotations

import json

from app.agent.loop import AGENT_FLAGS
from app.utils.logger import get_logger

_logger = get_logger("memory.receipt")

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_MERGE = "merge"
ACTION_SUPERSEDE = "supersede"
ACTION_STALE = "stale"
ACTION_REJECT = "reject"
ACTION_DOWNGRADE = "downgrade"

# reason 列长度（模型 String(255)）
_REASON_MAX = 255


async def _persist_receipt(
    character_id, memory_id, action, reason, detail
) -> None:
    """独立 session 写一条回执；任何异常只 warning（失败静默、不阻塞）。"""
    try:
        from app.db.database import async_session_factory
        from app.models.memory import MemoryWriteReceipt
        from app.utils.timeutil import now_naive_utc

        async with async_session_factory() as db:
            db.add(MemoryWriteReceipt(
                character_id=character_id,
                memory_id=memory_id,
                action=action,
                reason=(reason or "")[:_REASON_MAX] if reason else None,
                detail_json=json.dumps(detail or {}, ensure_ascii=False),
                created_at=now_naive_utc(),
            ))
            await db.commit()
    except Exception as e:
        _logger.warning(
            "write memory receipt failed action=%s mem=%s: %s", action, memory_id, e
        )


def emit_memory_receipt(
    character_id, memory_id, action, *, reason: str = "", detail: dict | None = None
) -> None:
    """闸控入口：flag 关 = 零写入、零行为；开 = 异步写一条回执（失败静默、不阻塞主链路）。

    character_id / memory_id 均可空（部分场景无具体记忆 id，如全局记忆 / 拒绝落库）。
    命中异步通道（spawn_background 发射后不管），主回复零延迟。
    """
    if not AGENT_FLAGS.get("memory_write_receipt", False):
        return
    try:
        from app.utils.async_tasks import spawn_background

        coro = _persist_receipt(character_id, memory_id, action, reason, detail)
    except Exception as e:
        _logger.warning(
            "build memory receipt failed action=%s mem=%s: %s", action, memory_id, e
        )
        return
    try:
        spawn_background(coro, name=f"mreceipt-{action}")
    except Exception as e:
        # 无运行中事件循环等场景：关闭协程避免 RuntimeWarning，绝不抛出
        try:
            coro.close()
        except Exception:
            pass
        _logger.warning(
            "spawn memory receipt failed action=%s mem=%s: %s", action, memory_id, e
        )
