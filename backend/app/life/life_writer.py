"""AI 生活写库加固（L5，2026-09-09）：locked 退避重试 + 记忆写入包装 + 悬空活动收尾。

背景：life 两套活动系统（life_tick → `activity.run_activity`、life_loop → `decision.ACTIONS`）
写记忆持续撞 `database is locked`。**根因不是重试次数不够，而是调用方自己持有写锁**：

  life_loop._execute 在外部 session 上改了 life_states（energy / needs_json）后未提交，
  随后 `_memory_allowed_today` 发查询触发 autoflush → UPDATE life_states 拿到 SQLite 唯一
  写锁；紧接着 `save_memory` 另开连接 INSERT memories，只能在 busy_timeout(10s) 耗尽后被
  判 locked。此时锁的持有者正是等待方自己，旧 `_retry_on_lock`（0.3s/0.6s）仍在同一持锁
  窗口内重试 → 必然再次失败（09-09 每 30 分钟一批 5 条 failed 即由此产生）。

加固三件套（flag `life_memory_write_retry` 默认开；关=回旧裸写路径，异常照旧上抛）：
  1. `retry_on_lock`——统一的 locked 退避重试（life_loop 原 `_retry_on_lock` 收敛到此）；
  2. `save_life_memory_with_retry`——记忆写入包装：失败返回 None 不抛出，活动照常 completed；
  3. `close_orphan_activities`——悬空 started（>30min 无 completed_at）置 timeout 收尾。

锁的正面修法在调用方（写记忆前先提交外部 session 释放锁，见 life_loop._execute），
本模块只提供统一的重试/收尾能力，不重复叠加第二套重试。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.db.database import async_session_factory
from app.utils.logger import get_logger

_logger = get_logger("life.writer")

# 通用写库重试退避（与 09-08 F5 的 life_loop 原行为一致：首次 + 2 次重试）
LOCK_RETRY_DELAYS = (0.3, 0.6)
# 记忆写入退避：跨了向量/Chroma IO，锁竞争窗口更长，给到 ~4s 总退避
MEMORY_RETRY_DELAYS = (0.4, 1.2, 2.5)
# 悬空活动收尾阈值（分钟）
ORPHAN_ACTIVITY_MINUTES = 30

_FLAG = "life_memory_write_retry"


def is_locked(exc: Exception) -> bool:
    """是否 SQLite 锁冲突（database is locked / database table is locked）。"""
    msg = str(exc).lower()
    return "database is locked" in msg or "database table is locked" in msg


def flag_on() -> bool:
    """L5 总开关（默认开；关=回退旧写路径）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(_FLAG, True))
    except Exception:
        return True


async def retry_on_lock(fn, what: str, delays: tuple[float, ...] = LOCK_RETRY_DELAYS):
    """写库协程遇 locked 退避重试；非锁错误或重试耗尽直接抛出（语义对齐原 _retry_on_lock）。"""
    err = None
    for attempt, delay in enumerate(((0.0, *delays))):
        try:
            if delay:
                await asyncio.sleep(delay)
            return await fn()
        except OperationalError as e:
            if not is_locked(e):
                raise
            err = e
            _logger.warning("life %s database locked (attempt %d/%d)",
                            what, attempt + 1, len(delays) + 1)
    raise err


async def save_life_memory_with_retry(**save_kwargs):
    """life 活动写记忆：独立短事务 + locked 退避；失败返回 None（不抛出、不拖垮活动）。

    flag 关闭时**原样直调** `save_memory`（异常照旧上抛，行为与加固前逐字节一致）。
    """
    from app.memory.service import save_memory
    if not flag_on():
        return await save_memory(**save_kwargs)
    try:
        return await retry_on_lock(
            lambda: save_memory(**save_kwargs), "save_memory", MEMORY_RETRY_DELAYS,
        )
    except Exception as e:
        _logger.warning("life save_memory failed after retry: %s", e)
        return None


async def close_orphan_activities(minutes: int = ORPHAN_ACTIVITY_MINUTES) -> int:
    """把 started 超过 N 分钟仍无 completed_at 的活动置 timeout（防长期悬挂污染状态结算）。

    两套活动系统共用同一张 life_activity_logs，故只需一处扫描。失败静默返回 0。
    """
    if not flag_on() or minutes <= 0:
        return 0
    try:
        from app.models.life import LifeActivityLog
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).replace(tzinfo=None)
        async with async_session_factory() as db:
            res = await db.execute(
                update(LifeActivityLog)
                .where(
                    LifeActivityLog.status == "started",
                    LifeActivityLog.started_at < cutoff,
                )
                .values(
                    status="timeout",
                    output_json=json.dumps(
                        {"error": "orphaned started activity, auto-closed"}, ensure_ascii=False,
                    ),
                )
            )
            await db.commit()
            n = int(res.rowcount or 0)
            if n:
                _logger.info("life orphan activities closed: %d (>%dmin)", n, minutes)
            return n
    except Exception as e:
        _logger.warning("life orphan activity cleanup failed: %s", e)
        return 0
