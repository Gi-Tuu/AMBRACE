"""关系标量衰减（认知架构 v2.1）：长期不互动 → trust/attachment 缓慢下降。

进程内每日最多执行一次；由 arbiter.run_tick 调用（失败静默）。
互动加分（bump）随 Phase 3「主动/被动统一状态机」一起做。
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.character_state import CharacterState
from app.utils.logger import get_logger

_logger = get_logger("scheduler.relationship_decay")

# 距最后一次互动超过 N 天才开始衰减；每多一天下降步长；下限
IDLE_DAYS_THRESHOLD = 1
DAILY_DECAY_STEP = 0.5
RELATION_MIN = 20

_last_run_date: str | None = None


async def run_relationship_decay() -> None:
    """每日关系衰减（进程内节流，每天一次）"""
    global _last_run_date
    today = datetime.now(timezone.utc).date().isoformat()
    if _last_run_date == today:
        return
    _last_run_date = today
    try:
        async with async_session_factory() as db:
            states = (await db.execute(select(CharacterState))).scalars().all()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            changed = 0
            for st in states:
                last = st.last_activity_at
                if last is None:
                    continue
                last = last.replace(tzinfo=None) if last.tzinfo else last
                idle_days = (now - last).total_seconds() / 86400.0
                if idle_days <= IDLE_DAYS_THRESHOLD:
                    continue
                drop = int((idle_days - IDLE_DAYS_THRESHOLD) * DAILY_DECAY_STEP)
                if drop <= 0:
                    continue
                if (st.trust or 50) > RELATION_MIN:
                    st.trust = max(RELATION_MIN, int((st.trust or 50) - drop))
                    changed += 1
                if (st.attachment or 50) > RELATION_MIN:
                    st.attachment = max(RELATION_MIN, int((st.attachment or 50) - drop))
                    changed += 1
            if changed:
                await db.commit()
                _logger.info("Relationship decay applied: %d dims changed", changed)
    except Exception as e:
        _logger.warning("Relationship decay failed: %s", e)