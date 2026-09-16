"""AMBRACE 3.10 —— 情感渴望触发源（arbiter collect_motivation_events，等价迁入）。

X6-c（2026-09-17）：flag ``proactive_strategy_plugins`` 开 **且** 有已启用策略包接管
``motivation`` 类别（``sdk.register_proactive_strategy("motivation", ["motivation"])``）时，
本源整体**让位**：「此刻要不要表达渴望 / 说什么」交给策略包（它用 ``relationship`` +
``user_rhythm`` + ``quota`` + ``character_state`` 判定）。

让位**不等于**放弃内核职责——想念通道有**独立配额**（6h 1 条 / 每日 ≤2 条）与**独立生成链路**
（剧情线 + 想念配额），以下全部在 :func:`prepare_strategy_candidate` 里由内核执行
（策略包无状态、每 tick 都投，靠这些闸收口）：

- 选人（不在活跃名单 = 不发）；
- 免打扰（DND 时段）；
- **关系门**：内核按关系标量/状态算渴望度，未达 ``MOTIVATION_SPEAK_THRESHOLD`` 不发；
- 配额与去重（近 6h / 北京当日，与 arbiter 想念通道同口径）；
- 素材装配：会话、最近聊天语境、闲置时长、角色人格与现状。

策略包**不得**自己记配额、自己节流、自己发消息（沿用 X6/X6-b 边界）。
flag 关 / 无包接管 → 行为与 X6-c 前逐字节一致。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.domain.proactivity.decision import MOTIVATION_SPEAK_THRESHOLD
from app.utils.logger import get_logger

from .base import SourceContext, TriggerItem
from .registry import register_source

_logger = get_logger("scheduler.sources.motivation")

_yield_logged = False  # 让位只在首次记一条 info，避免每 tick 刷日志


def _yielded_to_strategy_pack() -> bool:
    """本源是否应让位给策略包（异常→False，即不让位、回退旧行为）。"""
    try:
        from .strategy import category_yielded

        return category_yielded("motivation")
    except Exception as e:
        _logger.warning("strategy yield check failed: %s", e)
        return False


# 想念通道独立配额的兜底值（正常从 decision 常量取，取不到才用这里）
_FALLBACK_MAX_PER_6H = 1
_FALLBACK_MAX_PER_DAY = 2


async def prepare_strategy_candidate(candidate: dict) -> dict | None:
    """内核侧执行前处理（仅 motivation 策略候选走这里）：资格 → 免打扰 → 关系门 → 配额 → 素材装配。

    返回装配后的候选（含剧情线生成所需字段），``None`` = 内核闸未通过，丢弃。
    """
    from app.scheduling import arbiter
    from app.scheduling.triggers import get_latest_session, get_last_messages

    char_id = int(candidate["character_id"])
    user_id = int(candidate["user_id"])
    char_info = None
    for c in await arbiter.get_active_characters():
        if int(c["character_id"]) == char_id:
            char_info = c
            break
    if char_info is None:                       # 资格（选人）归内核
        return None
    # 免打扰（归内核；策略包看不到 DND 配置）
    if await arbiter.is_dnd_now(char_id, datetime.now(timezone(timedelta(hours=8)))):
        return None
    # 关系门：渴望度由内核按关系标量/状态计算，未达阈值不发（策略包只说"想发"）
    score = await arbiter._compute_motivation(char_id)
    if score < MOTIVATION_SPEAK_THRESHOLD:
        return None
    # 配额（独立想念通道：近 6h / 当日，与 arbiter 想念分支同口径）
    from .strategy import category_quota_limits, quota_used

    used = await quota_used(char_id, "motivation")
    limits = category_quota_limits("motivation")
    if used["used_6h"] >= int(limits.get("6h") or _FALLBACK_MAX_PER_6H):
        return None
    if used["used_today"] >= int(limits.get("day") or _FALLBACK_MAX_PER_DAY):
        return None

    session = await get_latest_session(char_id, char_info["user_id"] or user_id)
    if not session:
        return None
    # P0-1（2026-08-24）：想念候选必须有最近聊天语境，否则生成无法承接的消息
    context = await get_last_messages(session["id"], limit=5)
    # 闲置时长（分钟）：与内核想念通道同口径（会话最后一条消息时间）
    last_active = await arbiter._session_last_message_at(session["id"])
    if last_active is None:
        last_active = session.get("updated_at")
    if last_active is not None and last_active.tzinfo is not None:
        last_active = last_active.replace(tzinfo=None)
    idle_minutes = 0
    if last_active is not None:
        idle_minutes = max(0, int((datetime.now(timezone.utc).replace(tzinfo=None) - last_active).total_seconds() / 60))
    return {
        **char_info,
        **candidate,
        "session_id": session["id"],
        "behavior": "motivation",
        "last_context": context,
        "idle_minutes": idle_minutes,
        "motivation": round(float(score), 4),
    }


@register_source(name="motivation")
class MotivationSource:
    """情感渴望驱动的主动唤醒（priority=1）：渴望度 >= 阈值 → 主动搭话候选。

    仅提供候选，仍受 _execute 的最小间隔/连续不回复冷却/每日上限约束。
    """

    name = "motivation"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        global _yield_logged
        if _yielded_to_strategy_pack():
            if not _yield_logged:
                _yield_logged = True
                _logger.info("motivation source yielded: 由 proactive_strategy 策略包接管（防双发）")
            return []

        from app.scheduling import arbiter

        items: list[TriggerItem] = []
        try:
            for c in await arbiter.get_active_characters():
                cid = c.get("character_id")
                if not cid:
                    continue
                score = await arbiter._compute_motivation(cid)
                if score >= MOTIVATION_SPEAK_THRESHOLD:
                    # P0-4 修复（2026-08-16）：候选补 session_id（_execute proactive 分支直接索引），无会话跳过
                    from app.application.chat_service import get_latest_session_id
                    sid = await get_latest_session_id(c.get("user_id"), cid)
                    if not sid:
                        continue
                    c["session_id"] = sid
                    # P0-1（2026-08-24）：动机候选补最近聊天语境（复用节律类来源 get_last_messages，limit=5），
                    # 使 generate_proactive_event 不再因 motivation 通道语境为空而生成无法承接的消息；失败静默空串
                    context = ""
                    try:
                        from app.scheduling.triggers import get_last_messages
                        context = (await get_last_messages(sid, limit=5)) or ""
                    except Exception:
                        context = ""
                    c["last_context"] = context
                    # B1-③（2026-09-04，方案 §9 审核清单末条 / Codex 修订）：collect_motivation_events 原本漏算
                    # idle_minutes —— 照节律源（sources/rhythm.py）用 _session_last_message_at 补算，
                    # 否则 motivation 通道的闲置分级会落到 recent（误判久违/新鲜）；失败静默 None（→ recent）
                    idle_minutes = None
                    try:
                        from datetime import datetime, timezone
                        _last = await arbiter._session_last_message_at(sid)
                        if _last is not None:
                            if _last.tzinfo is not None:
                                _last = _last.replace(tzinfo=None)
                            idle_minutes = max(
                                0,
                                int((datetime.now(timezone.utc).replace(tzinfo=None) - _last).total_seconds() / 60),
                            )
                    except Exception:
                        idle_minutes = None
                    c["idle_minutes"] = idle_minutes
                    items.append(TriggerItem(
                        type="motivation",
                        priority=1,
                        candidate=c,
                        motivation=round(score, 4),
                    ))
        except Exception as e:
            _logger.warning("collect_motivation_events failed: %s", e)
        return items

    def quota(self, ctx: SourceContext) -> int:
        return 1000
