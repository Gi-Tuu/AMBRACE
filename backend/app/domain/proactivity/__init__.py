"""proactivity 域（F2-b 起步，2026-08-31）：离线主动行为决策的唯一归属。

边界：cognition=在线同步（用户消息驱动）；proactivity=离线批量（定时器驱动，决定
"AI 要不要主动做点什么"）；scheduling（规划中）只管到点触发，不含人格决策。
decision.py = 纯决策（常量+打分，零 IO）；查询/生成/发送等 IO 仍暂居 scheduling/arbiter.py
（经门面重导出，逐步迁入本包）。
"""
from app.domain.proactivity.decision import (  # noqa: F401
    CONTEXT_SORT_BONUS,
    MAX_PER_HOUR,
    MIN_PROACTIVE_INTERVAL_MINUTES,
    MOTIVATION_MAX_PER_6H,
    MOTIVATION_MAX_PER_DAY,
    MOTIVATION_SPEAK_THRESHOLD,
    REFLECTION_BONUS,
    REFLECTION_LOOKBACK_DAYS,
    UNREPLIED_COOLDOWN_HOURS,
    UNREPLIED_COOLDOWN_LIMIT,
    USER_ACTIVE_MINUTES,
    _apply_reflection_bonus,
    _context_sort_bonus,
    _in_dnd_window,
    _motivation_score,
    scheduler_gray_character,
)
# B1-③（2026-09-04）：主动接触意图层（闲置分级 + 意图选择，方案 §1-§9）纯模块
from app.domain.proactivity.outreach import (  # noqa: F401
    ALL_INTENTS,
    CHECK_IN,
    CONTINUE_MAX_HOURS,
    COLD_ALLOWED,
    FOLLOW_UP,
    INTEREST_HOOK,
    MEMORY_QUERY_BY_INTENT,
    OutreachMaterials,
    OutreachPlan,
    RECALL_SHARED,
    RECENT_AVOID,
    RECENT_MAX_HOURS,
    SHARE_SELF,
    STALE_MAX_HOURS,
    TIER_COLD,
    TIER_CONTINUE,
    TIER_RECENT,
    TIER_STALE,
    TIER_WEIGHTS,
    _candidates,
    select_outreach,
    staleness_tier,
)

__all__ = [
    "MAX_PER_HOUR", "MIN_PROACTIVE_INTERVAL_MINUTES", "USER_ACTIVE_MINUTES",
    "UNREPLIED_COOLDOWN_LIMIT", "UNREPLIED_COOLDOWN_HOURS", "MOTIVATION_SPEAK_THRESHOLD",
    "REFLECTION_BONUS", "REFLECTION_LOOKBACK_DAYS", "MOTIVATION_MAX_PER_6H",
    "MOTIVATION_MAX_PER_DAY", "CONTEXT_SORT_BONUS",
    "_motivation_score", "_apply_reflection_bonus", "_context_sort_bonus",
    "_in_dnd_window", "scheduler_gray_character",
    # B1-③ outreach
    "TIER_CONTINUE", "TIER_RECENT", "TIER_STALE", "TIER_COLD",
    "CONTINUE_MAX_HOURS", "RECENT_MAX_HOURS", "STALE_MAX_HOURS",
    "CHECK_IN", "SHARE_SELF", "RECALL_SHARED", "FOLLOW_UP", "INTEREST_HOOK",
    "ALL_INTENTS", "TIER_WEIGHTS", "COLD_ALLOWED", "RECENT_AVOID", "MEMORY_QUERY_BY_INTENT",
    "OutreachMaterials", "OutreachPlan",
    "staleness_tier", "select_outreach", "_candidates",
]
