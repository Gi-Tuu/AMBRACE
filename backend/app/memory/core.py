"""核心记忆 / 开放循环 / 关系锚点（World & Cognition P1，2026-08-15）

- Core Memory：高重要 + 多次确认的记忆 → 对话无条件注入（不靠向量检索）
- Open Loops：未完成事项（active Goal / 未到期承诺计时 / 到期备忘）→ 条件注入
- Relationship Anchors：importance ≥ 80 的关系/共享记忆 → 优先注入
"""
from datetime import datetime, timezone

from sqlalchemy import select, func

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.utils.logger import get_logger

_logger = get_logger("memory.core")

# 晋升阈值：importance ≥ 80（4 星+）且用户确认 ≥ 2 次 → 核心记忆
CORE_MIN_IMPORTANCE = 80.0
CORE_MIN_CONFIRMATIONS = 2
CORE_MAX_PER_CHAR = 30  # 每角色核心记忆上限，超出按 importance 淘汰

# 核心分类：身份 / 偏好 / 里程碑 / 承诺
CORE_CATEGORY_BY_SUBTYPE = {
    "name": "identity", "age": "identity", "location": "identity", "job": "identity",
    "relationship": "identity", "family": "identity", "education": "identity",
    "food": "preference", "hobby": "preference", "dislike": "preference",
    "habit": "preference", "preference": "preference", "style": "preference",
    "anniversary": "milestone", "milestone": "milestone", "life_event": "milestone",
    "commitment": "commitment", "promise": "commitment", "goal": "commitment",
}


def _core_category(sub_type: str | None, memory_type: str | None) -> str | None:
    if sub_type:
        c = CORE_CATEGORY_BY_SUBTYPE.get(sub_type)
        if c:
            return c
    if memory_type == "user_info":
        return "identity"
    if memory_type == "preference":
        return "preference"
    return None


async def maybe_promote_core(memory_id: int, importance: float,
                             sub_type: str | None, memory_type: str | None) -> None:
    """写入后自动晋升检查：高重要 或（已确认≥2次 且 重要≥阈值）→ is_core。失败静默。"""
    try:
        async with async_session_factory() as db:
            m = await db.get(Memory, memory_id)
            if m is None or m.is_core:
                return
            pct = float(m.importance or 0)
            confirmed = int(m.confirmation_count or 0)
            promote = pct >= CORE_MIN_IMPORTANCE and confirmed >= CORE_MIN_CONFIRMATIONS
            # 用户明确高价值类型（身份/偏好/承诺）+ 单次高重要也晋升（降低门槛）
            if not promote:
                cat = _core_category(sub_type, memory_type)
                promote = cat in ("identity", "preference", "commitment") and pct >= 100.0
            if not promote:
                return
            # 超上限淘汰最不重要的一条
            cnt = (await db.execute(
                select(func.count()).where(Memory.character_id == m.character_id, Memory.is_core == True)
            )).scalar() or 0
            if cnt >= CORE_MAX_PER_CHAR:
                old = (await db.execute(
                    select(Memory).where(
                        Memory.character_id == m.character_id, Memory.is_core == True,
                    ).order_by(Memory.importance.asc()).limit(1)
                )).scalar_one_or_none()
                if old is not None:
                    old.is_core = False
            m.is_core = True
            m.core_category = _core_category(sub_type, memory_type) or "identity"
            await db.commit()
    except Exception as e:
        _logger.warning("maybe_promote_core failed mem=%s: %s", memory_id, e)


async def confirm_memory(memory_id: int) -> None:
    """用户确认信号（"对/没错/记得"）：confirmation_count+1，达阈值自动晋升。失败静默。"""
    try:
        async with async_session_factory() as db:
            m = await db.get(Memory, memory_id)
            if m is None:
                return
            m.confirmation_count = (m.confirmation_count or 0) + 1
            if (m.confirmation_count >= CORE_MIN_CONFIRMATIONS
                    and float(m.importance or 0) >= CORE_MIN_IMPORTANCE):
                m.is_core = True
                m.core_category = _core_category(m.sub_type, m.memory_type) or "identity"
            await db.commit()
    except Exception as e:
        _logger.warning("confirm_memory failed mem=%s: %s", memory_id, e)


async def get_core_memories(character_id: int, limit: int = 10) -> list[Memory]:
    """核心记忆（无条件注入源）。"""
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Memory).where(
                    Memory.character_id == character_id,
                    Memory.is_core == True,
                    Memory.is_archived == False,
                ).order_by(Memory.importance.desc()).limit(limit)
            )).scalars().all()
            return list(rows)
    except Exception as e:
        _logger.warning("get_core_memories failed char=%d: %s", character_id, e)
        return []


async def get_relationship_anchors(character_id: int, user_id: int, limit: int = 5) -> list[Memory]:
    """关系锚点：importance ≥ 80 的关系/共享/事件记忆。"""
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Memory).where(
                    Memory.character_id == character_id,
                    Memory.user_id == user_id,
                    Memory.is_archived == False,
                    Memory.importance >= 80.0,
                    Memory.memory_type.in_(["event", "insight"]),
                ).order_by(Memory.importance.desc(), Memory.created_at.desc()).limit(limit)
            )).scalars().all()
            return list(rows)
    except Exception as e:
        _logger.warning("get_relationship_anchors failed char=%d: %s", character_id, e)
        return []


async def get_open_loops(character_id: int, user_id: int, limit: int = 10) -> list[str]:
    """开放循环（未完成事项）文本列表：active Goal + 未到期计时承诺。"""
    loops: list[str] = []
    try:
        from app.models.life import LifeGoal
        from app.models.scheduled_event import ScheduledEvent
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with async_session_factory() as db:
            goals = (await db.execute(
                select(LifeGoal).where(
                    LifeGoal.character_id == character_id,
                    LifeGoal.status == "active",
                ).order_by(LifeGoal.priority.desc()).limit(limit)
            )).scalars().all()
            for g in goals:
                loops.append(f"进行中的目标：{g.title or ''}")
            # 未到期的承诺计时（AI/用户承诺过的时间点）
            timers = (await db.execute(
                select(ScheduledEvent).where(
                    ScheduledEvent.character_id == character_id,
                    ScheduledEvent.user_id == user_id,
                    ScheduledEvent.status == "pending",
                    ScheduledEvent.trigger_at > now,
                ).order_by(ScheduledEvent.trigger_at.asc()).limit(limit)
            )).scalars().all()
            for t in timers:
                hint = (t.content_hint or "").strip()
                left = max(1, int((t.trigger_at - now).total_seconds() / 60))
                owner = "用户" if (t.owner or "ai") == "user" else "你"
                loops.append(f"{owner}说过「{hint or '某件事'}」，约 {left} 分钟后到点")
    except Exception as e:
        _logger.warning("get_open_loops failed char=%d: %s", character_id, e)
    return loops[:limit]
