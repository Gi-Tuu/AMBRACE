"""AMBRACE 3.10 —— 随机节律触发源（arbiter collect_rhythm_events，原逻辑整体迁入，等价）。

X6-b（2026-09-17）：flag ``proactive_strategy_plugins`` 开 **且** 有已启用策略包接管
``rhythm`` 类别（``sdk.register_proactive_strategy("rhythm", ...)``）时，本源整体**让位**：
「今天该不该发 / 发哪一类」交给策略包（它用 ``time_ctx`` / ``character_state`` 判定）。

让位**不等于**放弃内核职责——以下仍在本模块 :func:`prepare_strategy_candidate` 里执行
（策略包无状态、每 tick 都投，靠这些闸收口）：

- pending 计时器 / 未发完剧情线互斥；
- 每日上限（``get_daily_count`` ≥ ``max_daily_proactive`` 则丢弃）；
- 素材装配：会话、最近消息、闲置时长、角色人格/现状等剧情线生成所需字段。

flag 关 / 无包接管 → 行为与 X6-b 前逐字节一致。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from app.utils.logger import get_logger

from .base import SourceContext, TriggerItem
from .registry import register_source

_logger = get_logger("scheduler.sources.rhythm")

_yield_logged = False  # 让位只在首次记一条 info，避免每 tick 刷日志


def _yielded_to_strategy_pack() -> bool:
    """本源是否应让位给策略包（异常→False，即不让位、回退旧行为）。"""
    try:
        from .strategy import category_yielded

        return category_yielded("rhythm")
    except Exception as e:
        _logger.warning("strategy yield check failed: %s", e)
        return False


async def prepare_strategy_candidate(candidate: dict) -> dict | None:
    """内核侧执行前处理（仅策略候选走这里）：频控闸 + 素材装配。

    返回装配后的候选（含剧情线生成所需字段），``None`` = 内核闸未通过，丢弃。
    """
    from app.scheduling import arbiter
    from app.scheduling.triggers import get_daily_count, get_latest_session, get_last_messages

    char_id = int(candidate["character_id"])
    user_id = int(candidate["user_id"])
    # 有 pending 计时器 → 跳过随机节律（AI 正在"洗澡/睡觉"）
    if await arbiter.has_pending_timer(char_id):
        return None
    # 有未发完的剧情切片 → 跳过随机节律，避免剧情重叠
    if await arbiter.has_pending_storyline(char_id):
        return None
    char_info = None
    for c in await arbiter.get_active_characters():
        if int(c["character_id"]) == char_id:
            char_info = c
            break
    if char_info is None:                       # 资格（选人）归内核：不在活跃名单 = 不发
        return None
    # 每日上限（频控归内核）
    if await get_daily_count(char_id) >= char_info["max_daily_proactive"]:
        return None

    session = await get_latest_session(char_id, char_info["user_id"] or user_id)
    if not session:
        return None
    context = await get_last_messages(session["id"])
    # 闲置时长（分钟）：与内核节律同口径（会话最后一条消息时间；无则退回 updated_at）
    last_active = await arbiter._session_last_message_at(session["id"])
    if last_active is None:
        last_active = session.get("updated_at")
    if last_active is not None and last_active.tzinfo is not None:
        last_active = last_active.replace(tzinfo=None)
    idle_minutes = 0
    if last_active is not None:
        idle_minutes = max(0, int((datetime.now(timezone.utc).replace(tzinfo=None) - last_active).total_seconds() / 60))
    behavior = str(candidate.get("message_type") or "")
    return {
        **char_info,
        **candidate,
        "session_id": session["id"],
        "behavior": behavior,
        "last_context": context,
        "idle_minutes": idle_minutes,
    }


@register_source(name="rhythm")
class RhythmSource:
    """随机节律采样：时间窗 + 概率 + 每日上限 + 计时器/剧情线互斥（priority=1）。"""

    name = "rhythm"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        global _yield_logged
        if _yielded_to_strategy_pack():
            if not _yield_logged:
                _yield_logged = True
                _logger.info("rhythm source yielded: 由 proactive_strategy 策略包接管（防双发）")
            return []

        from app.scheduling import arbiter
        from app.scheduling.life_rhythm import get_time_window, sample_should_trigger, pick_behavior
        from app.scheduling.triggers import get_daily_count, get_latest_session, get_last_messages

        window = get_time_window()
        if window is None:
            return []
        items: list[TriggerItem] = []
        for char_info in await arbiter.get_active_characters():
            try:
                char_id = char_info["character_id"]
                # 有 pending 计时器 → 跳过随机节律（AI 正在"洗澡/睡觉"）
                if await arbiter.has_pending_timer(char_id):
                    continue
                # 有未发完的剧情切片 → 跳过随机节律，避免剧情重叠
                if await arbiter.has_pending_storyline(char_id):
                    continue
                # 每日上限
                if await get_daily_count(char_id) >= char_info["max_daily_proactive"]:
                    continue
                # 概率采样
                if not sample_should_trigger(char_info["frequency"], window):
                    continue

                # 会话
                session = await get_latest_session(char_id, char_info["user_id"])
                if not session:
                    continue
                context = await get_last_messages(session["id"])

                # 闲置时长（分钟）—— 告知 AI 上次聊天已过去多久
                # P-fix（2026-08-31）：idle 基准改用会话最后一条消息 created_at（naive UTC），
                # 不用 session.updated_at —— SSE 流式路径落用户/AI 消息时不更新该字段，会虚高闲置。
                last_active = await arbiter._session_last_message_at(session["id"])
                if last_active is None:
                    last_active = session["updated_at"]
                if last_active.tzinfo is not None:
                    last_active = last_active.replace(tzinfo=None)
                idle_minutes = max(0, int((datetime.now(timezone.utc).replace(tzinfo=None) - last_active).total_seconds() / 60))

                behavior = pick_behavior(window)
                items.append(TriggerItem(
                    type=behavior,
                    priority=1,
                    candidate={
                        **char_info,
                        "session_id": session["id"],
                        "window": window,
                        "behavior": behavior,
                        "last_context": context,
                        "idle_minutes": idle_minutes,
                    },
                ))
            except Exception as e:
                _logger.warning("rhythm sample error char=%d: %s", char_info["character_id"], e)
        return items

    def quota(self, ctx: SourceContext) -> int:
        return 100
