"""AI 生活状态机：Tick 结算公式（纯函数，零 LLM）+ LifeState 读写

2026-08-12 Life Engine v2（Phase 1）
- energy/focus/needs 随 Tick 自然变化；夜间 sleep 恢复
- 与情绪八维 character_states 并存不混（本引擎不写 mood）
"""
import json
import random
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.life import LifeState

NEEDS = [
    "curiosity", "productivity", "relaxation", "social",
    "creativity", "learning", "reflection", "entertainment",
]


def default_needs() -> dict[str, int]:
    return {k: 50 for k in NEEDS}


def clamp(v: float) -> int:
    return max(0, min(100, int(v)))


def beijing_hour(now: datetime | None = None) -> int:
    """北京时间小时（0-23）"""
    if now is None:
        now = datetime.now(timezone.utc)
    return (now.hour + 8) % 24


def phase_of(local_hour: int) -> str:
    """时段：sleep(23-7) / morning(7-12) / afternoon(12-18) / evening(18-23)"""
    if local_hour < 7 or local_hour >= 23:
        return "sleep"
    if local_hour < 12:
        return "morning"
    if local_hour < 18:
        return "afternoon"
    return "evening"


def settle_energy(energy: int, phase: str, activity_cost: int = 0) -> int:
    """一次 Tick 的精力结算：sleep 每小时 +5；白天每小时 -2 + 活动消耗（rest 恢复）"""
    if phase == "sleep":
        return clamp(energy + 5)
    return clamp(energy - 2 - max(0, activity_cost))


def settle_focus(energy: int) -> int:
    """专注主要受精力影响 + 随机波动"""
    return clamp(int(energy * 0.7) + random.randint(0, 30))


def settle_needs(needs: dict[str, int], satisfied: dict[str, int] | None = None) -> dict[str, int]:
    """需求自然增长 +3-8，被活动满足的 -10-20"""
    out = {}
    for k in NEEDS:
        v = int(needs.get(k, 50)) + random.randint(3, 8)
        if satisfied:
            v -= int(satisfied.get(k, 0))
        out[k] = clamp(v)
    return out


async def get_life_state(db, character_id: int) -> LifeState:
    """读取（无则创建默认状态）"""
    st = (
        await db.execute(select(LifeState).where(LifeState.character_id == character_id))
    ).scalar_one_or_none()
    if st is None:
        st = LifeState(character_id=character_id, needs_json=json.dumps(default_needs(), ensure_ascii=False))
        db.add(st)
        await db.commit()
        await db.refresh(st)
    return st


async def apply_tick(db, character_id: int, phase: str, activity_cost: int = 0,
                     satisfied: dict[str, int] | None = None) -> LifeState:
    """结算并保存一次 Tick；返回更新后的状态"""
    st = await get_life_state(db, character_id)
    needs = dict(json.loads(st.needs_json or "{}")) or default_needs()
    st.energy = settle_energy(st.energy, phase, activity_cost)
    st.focus = settle_focus(st.energy)
    st.needs_json = json.dumps(settle_needs(needs, satisfied), ensure_ascii=False)
    st.phase = phase
    st.last_tick_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(st)
    return st
