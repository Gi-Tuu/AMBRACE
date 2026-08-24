"""AI 生活兴趣系统（Life Engine v2 Phase 3，2026-08-12）

- 兴趣衰减：每小时 level * decay_rate（默认 2%/小时，约 3 天降到一半）
- 兴趣成长：相关活动执行后 +5-15（按活动深度）
- 状态：>=60 hot（热爱）/ <5 dormant（沉寂，不删除可激活）/ 其余 active
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.life import LifeInterest

# 活动 → 兴趣关键词（中文兴趣名匹配用，phase3 简化：按活动类型归属兴趣桶）
ACTIVITY_INTEREST_MAP: dict[str, str] = {
    "browse": "探索",
    "learn": "学习",
    "create": "创作",
}

HOT_LEVEL = 60
DORMANT_LEVEL = 5


def decay_level(level: float, hours: float, decay_rate: float = 0.02) -> int:
    """兴趣衰减：level * (1 - decay_rate) ** hours；钳制 >=1"""
    if hours <= 0:
        return max(1, int(round(level)))
    v = level * ((1 - decay_rate) ** hours)
    return max(1, int(round(v)))


def grow_level(level: int, delta: int) -> int:
    """兴趣成长：+delta 钳制 [0,100]"""
    return max(0, min(100, level + delta))


def _publish_interest_event(character_id: int, name: str, old_level: int | None,
                            new_level: int, source: str, created: bool) -> None:
    """兴趣变更 trace（S-3，2026-08-16）：发 interest.updated 事件，只写不读可追溯变化链；失败静默"""
    try:
        from app.events import publish
        from app.events.schema import make_event
        _evt = make_event(
            "interest.updated",
            speaker={"type": "system", "id": "life_engine"},
            target={"type": "character", "id": character_id},
            provenance={"origin": "system_event"},
            data={
                "character_id": character_id,
                "interest": name,
                "old_level": old_level,
                "new_level": new_level,
                "source": source,
                "created": created,
            },
        )
        publish("interest.updated", _evt)
    except Exception:
        pass


def interest_status(level: int) -> str:
    """兴趣状态：hot/dormant/active"""
    if level >= HOT_LEVEL:
        return "hot"
    if level < DORMANT_LEVEL:
        return "dormant"
    return "active"


async def get_interest(db, character_id: int, name: str) -> LifeInterest | None:
    row = (
        await db.execute(
            select(LifeInterest).where(
                LifeInterest.character_id == character_id,
                LifeInterest.name == name,
            )
        )
    ).scalar_one_or_none()
    return row


async def touch_interest(db, character_id: int, name: str, delta: int = 8,
                         source: str = "activity") -> LifeInterest:
    """记录一次兴趣接触：存在则成长，否则创建（初始 20 + delta）"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    it = await get_interest(db, character_id, name)
    if it is None:
        _publish_interest_event(character_id, name, None, grow_level(20, delta), source, created=True)
        it = LifeInterest(
            character_id=character_id, name=name, level=grow_level(20, delta),
            source=source, last_engaged_at=now, status=interest_status(grow_level(20, delta)),
        )
        db.add(it)
    else:
        old_level = it.level
        it.level = grow_level(it.level, delta)
        it.last_engaged_at = now
        it.status = interest_status(it.level)
        it.source = source
        _publish_interest_event(character_id, name, old_level, it.level, source, created=False)
    await db.commit()
    await db.refresh(it)
    return it


async def apply_interest_decay(db, character_id: int) -> list[LifeInterest]:
    """对角色全部兴趣执行一次衰减（每小时 tick 调用；按距上次接触小时数）"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # 数据库存 naive UTC
    rows = (
        await db.execute(
            select(LifeInterest).where(LifeInterest.character_id == character_id)
        )
    ).scalars().all()
    for it in rows:
        if it.last_engaged_at is not None:
            last = it.last_engaged_at
            if last.tzinfo is not None:
                last = last.replace(tzinfo=None)
            hours = max(0.0, (now - last).total_seconds() / 3600)
            it.level = decay_level(it.level, hours, it.decay_rate)
            it.status = interest_status(it.level)
            it.last_engaged_at = now  # 衰减后重置计时基准
    await db.commit()
    return rows
