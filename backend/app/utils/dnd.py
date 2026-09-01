"""免打扰与每日限额公共函数（2026-08-17 重构收敛：原 scheduler 4 份逐字复制）"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models.user import UserDndSettings


async def user_in_dnd_period(db, user_id: int) -> bool:
    """用户免打扰时段（北京时间，支持跨天）。无记录或未开启返回 False。"""
    result = await db.execute(select(UserDndSettings).where(UserDndSettings.user_id == user_id))
    dnd = result.scalar_one_or_none()
    if dnd is None or not dnd.dnd_enabled:
        return False
    cn_tz = timezone(timedelta(hours=8))
    now = datetime.now(cn_tz)
    cur = now.hour * 60 + now.minute
    start = dnd.start_hour * 60 + dnd.start_minute
    end = dnd.end_hour * 60 + dnd.end_minute
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end


async def daily_count(db, character_id: int, model) -> int:
    """角色今日（北京时间）记录数（按 model 表 created_at 统计）"""
    cn_tz = timezone(timedelta(hours=8))
    today_start = datetime.now(cn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start.astimezone(timezone.utc).replace(tzinfo=None)
    cnt = (await db.execute(
        select(select(func.count()).select_from(model).where(
            model.character_id == character_id,
            model.created_at >= today_start,
        ).subquery())
    )).scalar() or 0
    return int(cnt)
