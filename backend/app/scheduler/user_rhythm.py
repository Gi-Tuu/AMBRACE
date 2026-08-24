"""用户作息学习（#28 ②，2026-08-24）：从聊天记录按小时统计活跃度，推断用户活跃时段。

- 纯函数 infer_active_hours / hourly_rhythm_weight 便于单测；
- learn_user_rhythm 把结果落库 user_rhythm（幂等 upsert）；
- get_rhythm_weight 供 arbiter 做时段权重/推迟（Feature Flag proactive_user_rhythm，默认开可回退）。
"""
import json
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.utils.logger import get_logger

_logger = get_logger("scheduler.user_rhythm")

# 学习窗口与刷新阈值
LEARN_LOOKBACK_DAYS = 7
STALE_AFTER_HOURS = 24

# 权重缓存：{user_id: (expire_ts, weight)}（60s 过期）
_weight_cache: dict[int, tuple[float, float]] = {}


def infer_active_hours(
    hourly_counts: dict[int, int],
    threshold_ratio: float = 0.4,
    min_count: int = 1,
    gap_merge: int = 2,
) -> list[list[int]]:
    """从「小时 → 活跃计数」推断用户活跃时段（list[[start_hour, end_hour)]，升序）。

    规则（简化版）：
    - 活跃小时：count >= max(1, max_count * threshold_ratio)；
    - 相邻/间隔 <= gap_merge 的活跃小时合并为一个时段；
    - 空数据返回 []（未学习/无信号 → 调用方按不挡住现行为处理）。
    """
    if not hourly_counts:
        return []
    max_c = max(hourly_counts.values())
    if max_c <= 0:
        return []
    threshold = max(min_count, max_c * threshold_ratio)
    active = sorted(h for h, c in hourly_counts.items() if c >= threshold)
    if not active:
        return []
    ranges: list[list[int]] = []
    start = prev = active[0]
    for h in active[1:]:
        if h <= prev + gap_merge:
            prev = h
        else:
            ranges.append([start, prev + 1])
            start = prev = h
    ranges.append([start, prev + 1])
    return ranges


def hourly_rhythm_weight(cn_hour: int, active_hours: list[list[int]] | None) -> float:
    """当前小时是否位于用户活跃时段：1.0=活跃/未学习；0.0=明显不活跃（可推迟）。

    支持跨天时段（如 [[22,2]] 表示 22:00-次日 02:00）。
    """
    if not active_hours:
        return 1.0
    for s, e in active_hours:
        if s <= e:
            if s <= cn_hour < e:
                return 1.0
        else:
            if cn_hour >= s or cn_hour < e:
                return 1.0
    return 0.0


async def _load_hourly_counts(user_id: int) -> dict[int, int]:
    """近 LEARN_LOOKBACK_DAYS 天内该用户消息（sender_type=user）按北京时间小时聚合计数。"""
    since_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=LEARN_LOOKBACK_DAYS)
    from app.db.database import async_session_factory
    from app.models.chat_message import ChatMessage
    from app.models.chat_session import ChatSession

    counts: dict[int, int] = {}
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(ChatMessage.created_at)
                .join(ChatSession, ChatSession.id == ChatMessage.session_id)
                .where(
                    ChatSession.user_id == user_id,
                    ChatMessage.sender_type == "user",
                    ChatMessage.created_at >= since_utc_naive,
                )
            )
        ).scalars().all()
    for ts in rows:
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        cn_hour = (ts + timedelta(hours=8)).hour  # 北京时间小时
        counts[cn_hour] = counts.get(cn_hour, 0) + 1
    return counts


async def learn_user_rhythm(user_id: int) -> list[list[int]]:
    """重算并落库该用户活跃时段，返回 active_hours；失败静默返回 []。"""
    try:
        counts = await _load_hourly_counts(user_id)
        active = infer_active_hours(counts)
        from app.models.user_rhythm import UserRhythm
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            row = (await db.execute(select(UserRhythm).where(UserRhythm.user_id == user_id))).scalar_one_or_none()
            if row is None:
                row = UserRhythm(user_id=user_id, active_hours=json.dumps(active, ensure_ascii=False))
                db.add(row)
            else:
                row.active_hours = json.dumps(active, ensure_ascii=False)
            await db.commit()
        _logger.info("user_rhythm learned user=%d active_hours=%s", user_id, active)
        return active
    except Exception as e:
        _logger.warning("learn_user_rhythm failed user=%d: %s", user_id, e)
        return []


def _row_age_hours(row) -> float | None:
    ts = getattr(row, "learned_at", None)
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


async def get_rhythm_weight(user_id: int, cn_hour: int) -> float:
    """返回用户当前小时活跃权重（1.0=活跃窗口/未学习/Flag 关；0.0=明显不活跃）。

    带 60s 进程内缓存；首次/过期（>STALE_AFTER_HOURS）时重学（失败静默返回 1.0，不影响现状）。
    """
    from app.agent import loop as _loop
    if not _loop.AGENT_FLAGS.get("proactive_user_rhythm", True):
        return 1.0
    now_ts = time.time()
    cached = _weight_cache.get(user_id)
    if cached and now_ts - cached[0] < 60:
        return cached[1]
    weight = 1.0
    try:
        from app.models.user_rhythm import UserRhythm
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            row = (await db.execute(select(UserRhythm).where(UserRhythm.user_id == user_id))).scalar_one_or_none()
        active: list[list[int]] = []
        if row is None:
            active = await learn_user_rhythm(user_id)
        else:
            active = json.loads(row.active_hours or "[]")
            age = _row_age_hours(row)
            if age is None or age > STALE_AFTER_HOURS:
                active = await learn_user_rhythm(user_id)
        weight = hourly_rhythm_weight(cn_hour, active)
    except Exception as e:
        _logger.warning("get_rhythm_weight failed user=%d: %s", user_id, e)
        weight = 1.0
    _weight_cache[user_id] = (now_ts, weight)
    return weight
