"""AI 生活状态机：Tick 结算公式（纯函数，零 LLM）+ LifeState 读写

2026-08-12 Life Engine v2（Phase 1）
- energy/focus/needs 随 Tick 自然变化；夜间 sleep 恢复
- 与情绪八维 character_states 并存不混（本引擎不写 mood）
"""
import json
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.life import LifeState
from app.utils.timeutil import now_naive_utc

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
        now = now_naive_utc()
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


# M1（2026-11-04）：ΔT 化参数。一次结算覆盖的时长夹在 [MIN, MAX] 之间——
# 下限防「刚结算过又结算一次」，上限防长停摆（离线数天）一次性把状态推到极端。
MIN_SETTLE_HOURS = 0.25
MAX_CATCHUP_HOURS = 24.0


def _settle_dt_v2() -> bool:
    """是否启用 ΔT 结算（flag 默认关 ⇒ 一次调用＝一拍，与历史逐字节一致）。"""
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("life_settle_dt_v2", False))
    except Exception:  # noqa: BLE001 - 取数失败按旧口径（不改变现网行为）
        return False


def settle_energy(energy: int, phase: str, activity_cost: int = 0,
                  hours: float = 1.0) -> int:
    """精力结算：sleep 每小时 +5；白天每小时 -2 + 活动消耗（rest 恢复）。

    ``hours``（M1）：本次结算覆盖的小时数，默认 1.0 ⇒ 与历史「一次调用＝一拍」完全等价。
    ``activity_cost`` 是一次性消耗，不随时长缩放。
    """
    h = 1.0 if hours is None else max(0.0, float(hours))
    if phase == "sleep":
        return clamp(energy + int(round(5 * h)))
    return clamp(energy - int(round(2 * h)) - max(0, activity_cost))


def settle_focus(energy: int) -> int:
    """专注主要受精力影响 + 随机波动"""
    return clamp(int(energy * 0.7) + random.randint(0, 30))


def dead_fields_v1_enabled() -> bool:
    """M2b（2026-11-07）：focus 派生只读化 + mood_delta 死字段停写（flag `life_state_dead_fields_v1`，默认关）。

    开＝结算不再回写 ``life_states.focus``（读方一律从 energy 现算）且活动日志停写 mood_delta；
    关＝历史行为逐字节不变。详见 docs/human-like-mechanism-research.md M2 行。
    """
    from app.flags.agent_flags import AGENT_FLAGS
    return bool(AGENT_FLAGS.get("life_state_dead_fields_v1", False))


def settle_needs(needs: dict[str, int], satisfied: dict[str, int] | None = None,
                 hours: float = 1.0) -> dict[str, int]:
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
    h = 1.0 if hours is None else max(0.0, float(hours))
    out = {}
    if h <= 0:
        return {k: clamp(int(needs.get(k, 50))) for k in NEEDS}
    # 按「整小时」切段逐拍演进（保持随机分布与旧语义）；不足 1 小时的余量按比例缩放。
    steps: list[float] = []
    rest = h
    while rest > 1e-9:
        step = 1.0 if rest >= 1.0 else rest
        steps.append(step)
        rest -= step
    for k in NEEDS:
        v = int(needs.get(k, 50))
        for step in steps:
            if step >= 1.0:
                # step=1 时与历史公式**逐字等价**（不许改这三行）
                v += random.randint(2, 5)
                if satisfied:
                    v -= int(satisfied.get(k, 0))
                if v > NEEDS_BASELINE:
                    v -= int((v - NEEDS_BASELINE) * NEEDS_DECAY)
                else:
                    v += min(NEEDS_RECOVER_MAX,
                             int((NEEDS_BASELINE - v) * NEEDS_RECOVER_RATE))
            else:
                v += int(round(random.randint(2, 5) * step))
                if satisfied:
                    v -= int(round(satisfied.get(k, 0) * step))
                if v > NEEDS_BASELINE:
                    v -= int((v - NEEDS_BASELINE) * NEEDS_DECAY * step)
                else:
                    v += min(int(NEEDS_RECOVER_MAX * step),
                             int((NEEDS_BASELINE - v) * NEEDS_RECOVER_RATE * step))
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
                     satisfied: dict[str, int] | None = None,
                     hours: float | None = None) -> LifeState:
    """结算并保存一次 Tick；返回更新后的状态。

    M1（flag ``life_settle_dt_v2``）：
    - flag 关（默认）⇒ ``hours=1``，逐字节等价历史「一次调用＝一拍」；
    - flag 开 ⇒ 按 ``last_tick_at`` 到现在的真实间隔结算：逐小时切片、**各自用那一小时的时段**
      （跨夜/跨日出正确），间隔夹在 [MIN_SETTLE_HOURS, MAX_CATCHUP_HOURS]；
    - ``hours`` 显式传入时优先（调用方可指定固定时长，测试/回放用）。
    """
    st = await get_life_state(db, character_id)
    needs = dict(json.loads(st.needs_json or "{}")) or default_needs()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    v2 = _settle_dt_v2()
    if hours is None:
        if v2:
            last = st.last_tick_at
            gap = 1.0 if last is None else (now - last).total_seconds() / 3600.0
            hours = min(max(gap, MIN_SETTLE_HOURS), MAX_CATCHUP_HOURS)
        else:
            hours = 1.0
    if v2:
        # 逐小时切片：每一拍用「那一小时」的时段，活动成本/满足量只落在最后一拍。
        total = max(1, int(round(hours)))
        for i in range(total):
            at = now - timedelta(hours=total - 1 - i)
            ph = phase_of(beijing_hour(at))
            last_step = i == total - 1
            st.energy = settle_energy(
                st.energy, ph,
                activity_cost if last_step else 0, hours=1.0)
            needs = settle_needs(needs, satisfied if last_step else None, hours=1.0)
            st.needs_json = json.dumps(needs, ensure_ascii=False)
            st.phase = ph
        if not dead_fields_v1_enabled():
            st.focus = settle_focus(st.energy)
    else:
        st.energy = settle_energy(st.energy, phase, activity_cost)
        if not dead_fields_v1_enabled():
            st.focus = settle_focus(st.energy)
        st.needs_json = json.dumps(settle_needs(needs, satisfied), ensure_ascii=False)
        st.phase = phase
    st.last_tick_at = now
    await db.commit()
    await db.refresh(st)
    return st
