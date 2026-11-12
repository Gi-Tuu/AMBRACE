# -*- coding: utf-8 -*-
"""M4：主动消息「直发通道」内核闸单点化（合并 state_triggers / life_share 两份漂移实现）。

纪律：
- 行为默认不变：state_trigger 新增的最严 3 项由 flag `proactive_kernel_full_gate` 控制，默认关；
- arbiter 候选阶段（pre_gates）继续走 GateBundle 注入（测试桩接缝），本模块只登记它覆盖的检查名，
  由防漂移棘轮比对；
- 任何取数异常 ⇒ fail-closed（kernel_gate_error），与既有两份实现一致。
"""
from __future__ import annotations

from datetime import timedelta

from app.flags.agent_flags import AGENT_FLAGS
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc, to_naive_utc

_logger = get_logger("scheduling.proactive_kernel")

# 全量检查（= life_share 既有 8 项，最严集）
CHECKS = (
    "bad_identity", "not_eligible", "inactive_char", "pending_timer",
    "pending_storyline", "hourly_cap", "min_interval", "daily_cap",
    "night_said_sleep",
)
FULL = CHECKS
# state_trigger 既有口径（6 项；缺 inactive_char / hourly_cap / night_said_sleep——
# hourly 由它的调用点已判，另两项当年未接）
LEGACY_STATE_TRIGGER = (
    "bad_identity", "not_eligible", "pending_timer", "pending_storyline",
    "min_interval", "daily_cap",
)
# arbiter 候选阶段 pre_gates 覆盖的检查（仅登记用途，供棘轮比对；实现在 executors/guards.py）
ARBITER_PRE_GATE_CHECKS = ("dnd", "user_said_sleep", "user_active", "hourly_cap", "pacing")


def _enabled(set_) -> set[str]:
    return set(set_)


async def direct_send_gate_reason(character_id, user_id, *, channel: str,
                                  checks: tuple[str, ...] = FULL,
                                  trigger_type: str | None = None) -> str | None:
    """直发通道内核闸：返回命中的闸名（None = 全通过）。

    ``channel`` 只用于日志；``checks`` 决定跑哪几项（默认最严 FULL）。
    """
    from app.domain.proactivity.decision import MAX_PER_HOUR, MIN_PROACTIVE_INTERVAL_MINUTES
    from app.scheduling import arbiter
    from app.scheduling.triggers import get_daily_count

    want = _enabled(checks)
    try:
        cid = int(character_id or 0)
        uid = int(user_id or 0)
    except (TypeError, ValueError):
        return "bad_identity"
    if not cid or not uid:
        return "bad_identity"
    try:
        active = {int(c["character_id"]): c for c in await arbiter.get_active_characters()}
        if "not_eligible" in want and cid not in active:
            return "not_eligible"
        if "inactive_char" in want and await arbiter.inactive_char_skip(cid):
            return "inactive_char"
        if "pending_timer" in want and await arbiter.has_pending_timer(cid):
            return "pending_timer"
        if "pending_storyline" in want and await arbiter.has_pending_storyline(cid):
            return "pending_storyline"
        if "hourly_cap" in want and await arbiter.get_hourly_active_count(cid) >= MAX_PER_HOUR:
            return "hourly_cap"
        if "min_interval" in want:
            last = await arbiter.get_last_proactive_time(cid)
            if last is not None and now_naive_utc() - to_naive_utc(last) < timedelta(
                    minutes=MIN_PROACTIVE_INTERVAL_MINUTES):
                return "min_interval"
        if "daily_cap" in want:
            own_today = await arbiter.get_daily_sent_count(cid, trigger_type) if trigger_type \
                else 0
            max_daily = int(active.get(cid, {}).get("max_daily_proactive") or 5)
            if await get_daily_count(cid) + own_today >= max_daily:
                return "daily_cap"
        if "night_said_sleep" in want and await arbiter.has_user_said_sleep(cid, uid):
            return "night_said_sleep"
        return None
    except Exception as e:  # noqa: BLE001 - fail-closed，与既有实现一致
        _logger.warning("%s 内核闸判定失败 char=%s: %s", channel, cid, e)
        return "kernel_gate_error"


def state_trigger_checks() -> tuple[str, ...]:
    """state_trigger 当前生效的检查集：flag 开＝全量（最严），关＝历史口径。"""
    try:
        full = bool(AGENT_FLAGS.get("proactive_kernel_full_gate", False))
    except Exception:  # noqa: BLE001
        full = False
    return FULL if full else LEGACY_STATE_TRIGGER
