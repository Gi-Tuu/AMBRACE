"""AMBRACE 3.10 —— 对话未收尾跟进触发源（arbiter 事件源 unfinished_topic）。

原采集逻辑在 app.scheduling.unfinished_topic（collect_unfinished_events），本类仅作 TriggerSource 适配。

X6-c（2026-09-17）：flag ``proactive_strategy_plugins`` 开 **且** 有已启用策略包接管
``unfinished_topic`` 类别时，本源整体**让位**：「哪个话头值得追问」交给策略包
（它经 ``sdk.get_proactive_context(["open_topics"])`` 取未收尾话题）。

让位**不等于**放弃内核职责——本类别有**独立配额**（每角色每日 1 条）与**独立生成链路**
（``run_unfinished_topic``），以下全部在 :func:`prepare_strategy_candidate` 里由内核执行
（策略包无状态、每 tick 都投，靠这些闸收口）：

- 选人（不在活跃名单 = 不发）；
- 免打扰（DND 时段）+ 连续不回复冷却（防骚扰）；
- 每日配额（1 条/角色，与 ``unfinished_topic.MAX_DAILY`` 同口径）与当日去重；
- 最小间隔（距该会话最后一条消息 ≥ ``MIN_GAP_MINUTES``）；
- 素材装配：会话、**话头正文按 topic_id 内核复核**（防伪造/防过期话题）与角色人格。

**落库与去重口径（迁移时先钉死，否则会重演 rhythm 的"去重闸空转"）**：
``message_type = "unfinished_topic"``（与内核 ``send_to_session`` 落库口径一致），
当日去重 = ``proactive_message_logs`` 中 (角色, ``unfinished_topic``, 北京日界) 已有行
（``strategy.CATEGORY_DEDUP`` 登记为 ``("message_log", "day")``）。

flag 关 / 无包接管 → 行为与 X6-c 前逐字节一致。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.utils.logger import get_logger

from .base import SourceContext, TriggerItem
from .registry import register_source

_logger = get_logger("scheduler.sources.unfinished_topic")

_yield_logged = False  # 让位只在首次记一条 info，避免每 tick 刷日志


def _yielded_to_strategy_pack() -> bool:
    """本源是否应让位给策略包（异常→False，即不让位、回退旧行为）。"""
    try:
        from .strategy import category_yielded

        return category_yielded("unfinished_topic")
    except Exception as e:
        _logger.warning("strategy yield check failed: %s", e)
        return False


# 内核既定口径（与 app.scheduling.unfinished_topic 一致，别另立一套）
MESSAGE_TYPE = "unfinished_topic"
# 话头正文截断长度（与 collect_unfinished_events 一致）
TOPIC_CHARS = 120


async def _resolve_content(candidate: dict, char_id: int) -> str:
    """话头正文归内核：优先按 ``topic_id`` 复核话题归属与状态，取不到才用候选自带文本。

    策略包只能投 id（或短文本），正文由内核按 id 读回并截断——防伪造、防过期话题被追问。
    """
    tid = candidate.get("topic_id")
    if tid:
        try:
            from sqlalchemy import select

            from app.db.database import async_session_factory
            from app.models.memory import ConversationTopic

            async with async_session_factory() as db:
                row = (
                    await db.execute(
                        select(ConversationTopic).where(ConversationTopic.id == int(tid))
                    )
                ).scalar_one_or_none()
            if row is None or int(row.character_id) != int(char_id):
                return ""                        # 不是该角色的话题 → 不发
            if str(row.status or "") != "进行中":
                return ""                        # 已收尾/搁置 → 不再追问
            return str(row.topic or "")[:TOPIC_CHARS]
        except Exception as e:
            _logger.warning("unfinished topic resolve failed id=%s: %s", tid, e)
            return ""
    return str(candidate.get("unfinished_content") or "")[:TOPIC_CHARS]


async def prepare_strategy_candidate(candidate: dict) -> dict | None:
    """内核侧执行前处理（仅 unfinished_topic 策略候选走这里）：
    资格 → 免打扰 → 未回复冷却 → 日配额 → 最小间隔 → 素材装配。

    返回装配后的候选（供 ``run_unfinished_topic`` 执行），``None`` = 内核闸未通过，丢弃。
    """
    from app.scheduling import arbiter
    from app.scheduling.triggers import get_latest_session
    from app.scheduling.unfinished_topic import MAX_DAILY, MIN_GAP_MINUTES

    char_id = int(candidate["character_id"])
    user_id = int(candidate["user_id"])
    char_info = None
    for c in await arbiter.get_active_characters():
        if int(c["character_id"]) == char_id:
            char_info = c
            break
    if char_info is None:                       # 资格（选人）归内核
        return None
    # 免打扰（归内核）
    if await arbiter.is_dnd_now(char_id, datetime.now(timezone(timedelta(hours=8)))):
        return None
    # 连续不回复冷却（防骚扰，与主动搭话同款闸门）
    if await arbiter.unreplied_cooldown_active(char_id, user_id):
        return None
    # 每日配额（本类别独立：每角色每日 MAX_DAILY 条；与内核 _used_today 同口径）
    from .strategy import quota_used

    used = await quota_used(char_id, MESSAGE_TYPE)
    if used["used_today"] >= int(MAX_DAILY):
        return None

    session = await get_latest_session(char_id, char_info["user_id"] or user_id)
    if not session:
        return None
    # 最小间隔：用户刚说完就追问会显得黏人（内核口径 MIN_GAP_MINUTES）
    last_at = await arbiter._session_last_message_at(session["id"])
    if last_at is None:
        last_at = session.get("updated_at")
    if last_at is not None:
        if last_at.tzinfo is not None:
            last_at = last_at.replace(tzinfo=None)
        if datetime.now(timezone.utc).replace(tzinfo=None) - last_at < timedelta(minutes=MIN_GAP_MINUTES):
            return None
    # 素材装配：话头正文由内核按 topic_id 复核
    content = await _resolve_content(candidate, char_id)
    if not content:
        return None
    return {
        **char_info,
        **candidate,
        "session_id": session["id"],
        "unfinished_content": content,
    }


@register_source(name="unfinished_topic")
class UnfinishedTopicSource:
    """对话未收尾跟进：用户抛了话头（下次/改天/有空）→ 自然捡起话题（每日 1 次/角色，collect 内去重，priority=1）。"""

    name = "unfinished_topic"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        global _yield_logged
        if _yielded_to_strategy_pack():
            if not _yield_logged:
                _yield_logged = True
                _logger.info("unfinished_topic source yielded: 由 proactive_strategy 策略包接管（防双发）")
            return []

        from app.scheduling.unfinished_topic import collect_unfinished_events

        return [TriggerItem.from_dict(d) for d in await collect_unfinished_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100
