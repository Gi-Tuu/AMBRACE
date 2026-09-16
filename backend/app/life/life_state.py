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

# 批次三(2026-09-16)：需求基线与弹性曲线（settle_needs 向基线收敛，避免长期贴边）
NEEDS_BASELINE = 55          # 需求自然收敛中心（原无衰减 → 恒 100）
NEEDS_DECAY = 0.25           # 高于基线时的回落系数（截断取整，防跳变）
NEEDS_RECOVER_RATE = 0.10    # 低于基线时的回升系数（温和，避免刚被满足就弹回）
NEEDS_RECOVER_MAX = 6        # 单 tick 回升上限


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
    """需求自然变化（批次三 P0-5 重做，2026-09-16）。

    旧公式：固定 +3~8 增长、从无回落 → 不被活动满足的需求长期贴边 100；
    learning 被 study 频繁 -18 直接耗到 4。

    新公式（三段，全部截断取整，避免跳跃）：
    1. 自然增长 +2~5；
    2. 减去本次活动满足量（``satisfied``）；
    3. 向基线 ``NEEDS_BASELINE`` 弹性收敛：
       - 高于基线 → 回落 ``(v-B) * NEEDS_DECAY``（顶格 100 逐 tick 降到 ~70 均衡）；
       - 低于基线 → 温和回升 ``min(NEEDS_RECOVER_MAX, (B-v) * NEEDS_RECOVER_RATE)``
         （被耗到 4 的 learning 会真实回升，但不会刚满足就弹回）。

    均衡点 ≈ 基线 + 增长/衰减系数（≈70），因此需求既不再顶格 100，也不再单边探底；
    ``decision._score_action`` 的 60 阈值附近长期有真实波动。
    """
    out = {}
    for k in NEEDS:
        v = int(needs.get(k, 50))
        v += random.randint(2, 5)
        if satisfied:
            v -= int(satisfied.get(k, 0))
        if v > NEEDS_BASELINE:
            v -= int((v - NEEDS_BASELINE) * NEEDS_DECAY)
        else:
            v += min(NEEDS_RECOVER_MAX, int((NEEDS_BASELINE - v) * NEEDS_RECOVER_RATE))
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
