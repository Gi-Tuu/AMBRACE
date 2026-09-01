"""分层衰减（M2-S2，2026-08-31，WikiSkill 式）：高置信持久、低置信可退化。

规则（纯函数，可单测；flag `memory_tiered_decay` 默认关，开启后 decay 结算生效）：
- 低置信推测（INFERRED/UNVERIFIED 且 reliability<0.4）：S×0.8 加速退化，防推测长期污染；
- 高价值（is_core 或 确认≥2 或 有意义记忆）：S 下限抬到 14 天，且保留率跌破阈值时
  **直接冷归档**（is_archived=True）而非进 3 天删除倒计时；
- 高可靠事实（FACT 且 reliability≥0.8）：S 下限抬到 S_MAX_DAYS（60）、上限 180；
- 其余：维持现状。

effective_strength_days 只在衰减结算时**派生**使用，不改存量 strength_days——
因此无需回填迁移；cold archive 可逆（is_archived=False 即恢复）。
辅助脚本：scripts/diagnostics/memory_tiering_snapshot.py（快照/回滚 memories 关键字段）。
"""
from app.memory.constants import S_DEFAULT, S_MAX_DAYS, S_MIN_DAYS

_TIER_FLAG = "memory_tiered_decay"


def tiered_decay_on() -> bool:
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(_TIER_FLAG, False))
    except Exception:
        return False


def _fields(mem) -> tuple:
    epistemic = (getattr(mem, "epistemic_status", None) or "FACT").upper()
    reliable = float(getattr(mem, "reliability_score", None) or 1.0)  # 未评（NULL）按全信处理（与原行为一致）
    is_core = bool(getattr(mem, "is_core", False))
    confirmed = int(getattr(mem, "confirmation_count", 0) or 0)
    has_meaning = bool(getattr(mem, "why_it_matters", None))
    return epistemic, reliable, is_core, confirmed, has_meaning


def is_high_confidence(mem) -> bool:
    """高置信 = 核心 / 确认≥2 / 有意义 / FACT 且高可靠（四者其一）。"""
    epistemic, reliable, is_core, confirmed, has_meaning = _fields(mem)
    return is_core or confirmed >= 2 or has_meaning or (epistemic == "FACT" and reliable >= 0.8)


def effective_strength_days(mem) -> float:
    """有效强度 S（派生值，不落库）：低置信加速、高价值抬下限、高可靠事实抬上限。"""
    base = float(getattr(mem, "strength_days", None) or S_DEFAULT)
    epistemic, reliable, is_core, confirmed, has_meaning = _fields(mem)
    # 低置信推测：维持/加速退化
    if epistemic in ("INFERRED", "UNVERIFIED") and reliable < 0.4:
        return max(S_MIN_DAYS, base * 0.8)
    # 高价值：抬 S 下限（≥14 天），走更慢衰减
    if is_core or confirmed >= 2 or has_meaning:
        return max(base, 14.0)
    # 高可靠事实：S 下限抬到 60、上限 180（分级上限）
    if epistemic == "FACT" and reliable >= 0.8:
        return min(180.0, max(base, S_MAX_DAYS))
    return base


def should_cold_archive(mem) -> bool:
    """保留率跌破阈值时：高置信记忆直接冷归档（is_archived=True，可逆），不进删除倒计时。"""
    if not tiered_decay_on():
        return False
    if getattr(mem, "delete_at", None) is not None:
        return False  # flag 开启前已进倒计时的记忆维持现路径（到期删除）
    return is_high_confidence(mem)
