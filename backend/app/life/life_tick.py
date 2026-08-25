"""Life Tick：AI 离线生活调度任务（每小时触发；白天结算+概率活动，夜间睡眠恢复）

强度（proactive_settings.life_intensity）：low=每 3 tick 尝试活动(15%) / medium=每 2 tick(25%) / high=每 tick(40%)
"""
import json
from app.utils.logger import get_logger

from sqlalchemy import select

from app.models.character import AICharacter
from app.models.proactive_settings import ProactiveSettings
from app.scheduler.registry import BaseTask

_logger = get_logger("life.tick")

_STEP = {"low": 3, "medium": 2, "high": 1}
_tick_count: dict[int, int] = {}


class LifeTickTask(BaseTask):
    def __init__(self):
        super().__init__("life_tick", 3600)  # 每小时

    async def execute(self):
        from app.db.database import async_session_factory
        from app.life.life_state import apply_tick, get_life_state, phase_of, beijing_hour, default_needs
        from app.life.activity import run_activity

        hour = beijing_hour()
        phase = phase_of(hour)
        try:
            async with async_session_factory() as db:
                chars = (
                    await db.execute(
                        select(AICharacter).where(AICharacter.is_active.is_(True))
                    )
                ).scalars().all()
                for c in chars:
                    try:
                        ps = (
                            await db.execute(
                                select(ProactiveSettings).where(ProactiveSettings.character_id == c.id)
                            )
                        ).scalar_one_or_none()
                        if ps is not None and not ps.life_enabled:
                            continue
                        intensity = (ps.life_intensity if ps else "low") or "low"
                        st = await get_life_state(db, c.id)
                        if phase == "sleep":
                            await apply_tick(db, c.id, "sleep")
                            await _phase3_hook(db, c.id, c.user_id)
                            continue
                        # 白天：计数 + 每 step 次尝试一次活动
                        n = _tick_count.get(c.id, 0) + 1
                        _tick_count[c.id] = n
                        if n % _STEP.get(intensity, 3) != 0:
                            await apply_tick(db, c.id, phase)
                            continue
                        needs = json.loads(st.needs_json or "{}") or default_needs()
                        log = await run_activity(db, c.user_id, c, phase, needs, st.energy, intensity)
                        if log is not None:
                            satisfied = json.loads(log.output_json or "{}").get("satisfied", {})
                            await apply_tick(db, c.id, phase, activity_cost=max(0, log.energy_cost), satisfied=satisfied)
                        else:
                            await apply_tick(db, c.id, phase)
                        await _phase3_hook(db, c.id, c.user_id)
                    except Exception as e:
                        _logger.warning("life tick char=%d failed: %s", c.id, e)
        except Exception as e:
            _logger.warning("life tick run failed: %s", e)


async def _phase3_hook(db, character_id: int, user_id: int) -> None:
    """Phase 3：兴趣衰减 + 目标过期 + 种子（兴趣缺失 / 无进行中目标时播种）；sleep 与白天均执行"""
    try:
        from app.life.interest import apply_interest_decay, touch_interest
        from app.life.goal import expire_goals, create_goal
        from sqlalchemy import func as _func
        from app.models.life import LifeInterest, LifeGoal
        _n_i = (await db.execute(
            select(_func.count()).where(LifeInterest.character_id == character_id)
        )).scalar() or 0
        _n_active_g = (await db.execute(
            select(_func.count()).where(
                LifeGoal.character_id == character_id,
                LifeGoal.status == "active",
            )
        )).scalar() or 0
        if _n_i == 0:
            for _nm in ("探索", "创作"):
                await touch_interest(db, character_id, _nm, delta=5, source="seed")
        # 无进行中目标时播种：优先按热门兴趣生成个性化目标，避免反复同一批
        if _n_active_g == 0:
            _interests = (
                await db.execute(
                    select(LifeInterest)
                    .where(
                        LifeInterest.character_id == character_id,
                        LifeInterest.level >= 20,
                    )
                    .order_by(LifeInterest.level.desc())
                )
            ).scalars().all()
            _hot = [i.name for i in _interests if i.level >= 45][:2]
            # 兴趣 → 目标类型/标题（热门兴趣优先，其余按类型兜底）
            _interest_goal = {
                "创作": ("creative", "完成一次创作", "把最近的想法做成一件作品", 3),
                "探索": ("explore", "探索一个新话题", "找一件感兴趣的新鲜事研究", 2),
                "学习": ("skill", "学一个新技能", "花时间系统学一个想掌握的东西", 3),
            }
            _sown = 0
            for _nm in _hot:
                _spec = _interest_goal.get(_nm)
                if not _spec:
                    continue
                await create_goal(db, character_id, _spec[0], _spec[1],
                                  description=_spec[2], priority=2, progress_total=_spec[3])
                _sown += 1
            # 兜底：保证至少 2 个进行中目标
            for _t, _title, _desc, _total in (
                ("growth", "整理一段时间的记忆", "把最近的共同记忆整理清楚", 3),
                ("explore", "探索一个新话题", "找一件感兴趣的新鲜事研究", 2),
            ):
                if _sown >= 2:
                    break
                await create_goal(db, character_id, _t, _title, description=_desc,
                                  priority=1 if _t == "explore" else 2, progress_total=_total)
                _sown += 1
        await apply_interest_decay(db, character_id)
        await expire_goals(db, character_id)
        # AI 日程（Phase B-2，2026-08-14）：固定作息/Goal 推导生成 + 状态流转
        from app.life.schedule import schedule_tick
        await schedule_tick(db, character_id, user_id)
    except Exception as e:
        _logger.warning("life phase3 hook failed: %s", e)
