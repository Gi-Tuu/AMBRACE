"""life_followup 缓冲管理（设计稿 §10.4b）。"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.models.life import LifeFollowup


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def add_followup(
    db, character_id: int, user_id: int, summary: str,
    action: str, memory_id: int | None, window: str = "next_online",
) -> LifeFollowup | None:
    """动作完成后写入回聊缓冲。每角色每窗口最多 3 条 pending。"""
    count = (await db.execute(
        select(LifeFollowup).where(
            LifeFollowup.character_id == character_id,
            LifeFollowup.trigger_window == window,
            LifeFollowup.status == "pending",
        )
    )).scalars().all()
    if len(count) >= 3:
        return None
    f = LifeFollowup(
        character_id=character_id, user_id=user_id,
        summary=summary[:300], action=action,
        memory_id=memory_id, trigger_window=window,
        not_before=_now() + timedelta(hours=1),
    )
    db.add(f)
    await db.commit()
    return f


async def pop_followups(
    db, character_id: int, window: str, limit: int = 1,
) -> list[LifeFollowup]:
    """时机窗口触发时取出 pending 回聊素材（早安/夜间复盘/下次上线）。"""
    rows = (await db.execute(
        select(LifeFollowup).where(
            LifeFollowup.character_id == character_id,
            LifeFollowup.trigger_window == window,
            LifeFollowup.status == "pending",
            LifeFollowup.not_before <= _now(),
        ).order_by(LifeFollowup.created_at.asc()).limit(limit)
    )).scalars().all()
    for r in rows:
        r.status = "used"
        r.used_at = _now()
    await db.commit()
    return rows
