"""记忆衰减：艾宾浩斯遗忘曲线（R=exp(-Δt/S)）惰性结算 + 阈值倒计时删除。

2026-08-05 改造：原恒定 3%-5%/日复合衰减改为指数遗忘——
保留率 R = exp(-Δt / S)，S 为记忆强度（天），importance = R * 120；
每次强化（写入查重命中/检索命中）S 递增、遗忘变慢，符合艾宾浩斯复习间隔递增规律。
"""
import math
from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.utils.logger import get_logger
from app.memory.constants import (
    DECAY_THRESHOLD_PCT, DECAY_COUNTDOWN_DAYS, DECAY_MAX_PCT, S_DEFAULT,
)

_logger = get_logger("memory.decay")
from app.utils.timeutil import now_naive_utc as _now_naive


def retention_pct(dt_days: float, strength_days: float) -> float:
    """艾宾浩斯保留率：R=exp(-Δt/S)，×120 得百分比重要度并钳制 [0, DECAY_MAX_PCT]，展示与结算共用"""
    s = float(strength_days or S_DEFAULT)
    return min(DECAY_MAX_PCT, max(0.0, math.exp(-dt_days / s) * 120.0))


async def _apply_decay(db, mem, now=None) -> bool:
    """艾宾浩斯惰性结算。返回 True 表示已删除（到期倒计时结束）。

    结算即视为一次轻复习：刷新 last_reinforce_at（与旧版刷新 decay_base_at 语义一致），
    防止每次读列表都按同一 Δt 反复重算。is_pinned 不参与。
    """
    from app.memory.service import delete_memory
    from datetime import datetime, timedelta
    if mem.is_pinned or mem.is_locked:
        return False
    now = now or _now_naive()
    if mem.delete_at is not None:
        if now >= mem.delete_at:
            await delete_memory(mem.id)
            return True
        return False
    base = mem.last_reinforce_at or mem.decay_base_at or mem.created_at
    if base is None:
        mem.last_reinforce_at = now
        await db.commit()
        return False
    if isinstance(base, datetime) and base.tzinfo is not None:
        base = base.replace(tzinfo=None)
    dt_hours = (now - base).total_seconds() / 3600.0
    if dt_hours <= 0:
        return False
    dt_days = dt_hours / 24.0
    pct = retention_pct(dt_days, mem.strength_days)
    mem.importance = pct
    mem.last_reinforce_at = now
    if pct < DECAY_THRESHOLD_PCT and mem.delete_at is None:
        mem.delete_at = now + timedelta(days=DECAY_COUNTDOWN_DAYS)
    elif pct >= DECAY_THRESHOLD_PCT and mem.delete_at is not None:
        mem.delete_at = None
    await db.commit()
    return False

async def run_memory_decay():
    """记忆衰减与到期删除（惰性结算 + 倒计时到期移除）"""
    deleted = 0
    # P1 性能（2026-08-16）：单 session 批量处理，消除 N+1（原逐条开 session + db.get）
    from app.memory.service import _active_status_clause  # #70-C：失效记忆不再衰减（flag 关=永真）
    async with async_session_factory() as db:
        result = await db.execute(
            select(Memory).where(Memory.is_archived == False, Memory.is_pinned == False, _active_status_clause())
        )
        memories = result.scalars().all()
        for m in memories:
            try:
                if await _apply_decay(db, m):
                    deleted += 1
            except Exception as e:
                _logger.warning("Decay mem %d failed: %s", m.id, e)
        await db.commit()
    if deleted:
        _logger.info("Memory decay removed %d expired memories", deleted)
    return deleted
