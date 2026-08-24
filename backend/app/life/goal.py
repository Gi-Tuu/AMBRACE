"""AI 生活目标系统（Life Engine v2 Phase 3，2026-08-12）

- 目标生命周期：产生 → 激活（优先安排推进活动）→ 推进（progress+1）→ 完成（Life Event）/ 失败（deadline 过期）
- 目标类型：relationship/creative/growth/explore/skill（产出用户可感知价值）
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.life import LifeGoal

GOAL_ACTIVITY_MAP: dict[str, str] = {
    "relationship": "social_prepare",
    "creative": "create",
    "growth": "organize_memory",
    "explore": "browse",
    "skill": "learn",
}


async def get_active_goals(db, character_id: int) -> list[LifeGoal]:
    rows = (
        await db.execute(
            select(LifeGoal)
            .where(
                LifeGoal.character_id == character_id,
                LifeGoal.status == "active",
            )
            .order_by(LifeGoal.priority.desc(), LifeGoal.created_at.asc())
        )
    ).scalars().all()
    return list(rows)


async def create_goal(db, character_id: int, type: str, title: str,
                      description: str = "", priority: int = 2,
                      progress_total: int = 1, related_user: bool = True,
                      deadline: datetime | None = None) -> LifeGoal:
    g = LifeGoal(
        character_id=character_id, type=type, title=title[:160],
        description=description[:500], priority=priority,
        progress_total=max(1, progress_total), related_user=related_user,
        deadline=deadline,
    )
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return g


async def advance_goal(db, character_id: int, activity_name: str) -> LifeGoal | None:
    """活动推进对应类型目标 progress+1；完成 → status=completed + completed_at。返回被推进的目标"""
    goal = None
    for g in await get_active_goals(db, character_id):
        if GOAL_ACTIVITY_MAP.get(g.type) == activity_name:
            goal = g
            break
    if goal is None:
        return None
    goal.progress = min(goal.progress_total, goal.progress + 1)
    if goal.progress >= goal.progress_total:
        goal.status = "completed"
        goal.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(goal)
    return goal


async def expire_goals(db, character_id: int) -> int:
    """过期未完成目标 → failed（返回作废数）"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = (
        await db.execute(
            select(LifeGoal).where(
                LifeGoal.character_id == character_id,
                LifeGoal.status == "active",
                LifeGoal.deadline.is_not(None),
                LifeGoal.deadline < now,
            )
        )
    ).scalars().all()
    for g in rows:
        g.status = "failed"
    await db.commit()
    return len(rows)
