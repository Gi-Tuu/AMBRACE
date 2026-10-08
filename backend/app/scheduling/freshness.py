# -*- coding: utf-8 -*-
"""A39 批 2b：主动通道「闸②＝生成前重取现状」的**读数层**。

分工是写死的（守卫按这条核）：
  `app/domain/proactivity/freshness.py` ← 判据（纯函数、零 IO，三档 cancel／regenerate／keep）
  本模块 ← 只做 IO：把事实读进 `FreshFacts`，**不在这里判该不该发**

不新写第四套正则：识别「事件是否已经发生」一律复用既有探测器——`prospective_intent._signal_seen`
（到达／吃药这类）、`promise_parser.ready_result_seen`（等待结果已兑现）、
`domain.emotion.care._story_advanced`（剧情已推进）。本模块只吃它们返回的布尔，不复制它们的字面表。

失败口径（与既有通道一致）：**读不到就按「没变」处理**，绝不让本闸变成"读不到就一律不发"的隐性收紧。
"""
from __future__ import annotations

from app.domain.proactivity.freshness import (  # noqa: F401  （判据本体在 domain，这里只转发）
    CANCEL,
    KEEP,
    REGENERATE,
    FreshFacts,
    decide,
    shadow_mark,
)
from app.utils.logger import get_logger

_logger = get_logger("scheduler.freshness")

__all__ = [
    "FreshFacts", "decide", "shadow_mark", "verdict_for",
    "read_unfinished_topic_facts", "pre_send_check",
    "CANCEL", "KEEP", "REGENERATE",
]

# 话题行仍处于这两个状态之一才算「还该提起」（与 sources/unfinished_topic 的内核复核同口径）
_ACTIVE_TOPIC_STATUS = "进行中"


def verdict_for(channel: str, facts: FreshFacts, **extra) -> tuple[str, str, str]:
    """ ``(档位, 原因, 留痕串)``。留痕串走 `shadow_mark` 的统一形状，判效时按原因分档统计。"""
    verdict, reason = decide(channel, facts)
    return verdict, reason, shadow_mark(verdict, reason, extra or None)


async def _topic_row_active(topic_id) -> bool | None:
    """按 id 复核话题行是否仍在进行；读不到／没给 id 一律 None（＝未知，不触发 cancel）。"""
    if not topic_id:
        return None
    try:
        from sqlalchemy import select

        from app.db.database import async_session_factory
        from app.models.memory import ConversationTopic

        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(ConversationTopic.status, ConversationTopic.character_id)
                    .where(ConversationTopic.id == int(topic_id))
                )
            ).first()
        if row is None:
            return False
        return str(row[0] or "") == _ACTIVE_TOPIC_STATUS
    except Exception as e:  # 读失败＝未知，不是"没了"
        _logger.warning("freshness topic row failed id=%s: %s", topic_id, e)
        return None


async def read_unfinished_topic_facts(candidate: dict) -> FreshFacts:
    """通道 1（unfinished_topic）的生成前重取。

    两条事实，都只靠**重读同一样东西再比一次**得到，不引入新表新正则：
      - ``user_replied``：话头正文是否仍是该会话最后一条**用户**消息——不是了就说明用户接着说了别的，
        此时再「对了你上次说的那个…」等于复述旧事（A37 现场那条被吐槽的形态）；
      - ``topic_active`` / ``underlying_gone``：插件路径会下发 ``topic_id``，按 id 复核话题行状态
        （内核路径没有 id ⇒ 未知，按 True 处理，见本模块头部的失败口径）。
    """
    from app.scheduling.prospective_intent import _latest_user_message

    session_id = int(candidate.get("session_id") or 0)
    snapshot_topic = str(candidate.get("unfinished_content") or "")
    facts = FreshFacts(topic_active=True)
    if session_id <= 0:
        return facts
    latest = await _latest_user_message(session_id)
    user_replied = bool(latest) and latest.strip()[:120] != snapshot_topic.strip()[:120]
    active = await _topic_row_active(candidate.get("topic_id"))
    if active is None:
        topic_active, underlying_gone = True, False
    else:
        topic_active, underlying_gone = active, (not active and bool(candidate.get("topic_id")))
    return FreshFacts(user_replied=user_replied, topic_active=topic_active,
                      underlying_gone=underlying_gone)


# 通道 → 读数函数（没登记的通道＝本闸不看它，pre_send_check 直接放行）
_READERS: dict[str, object] = {
    "unfinished_topic": read_unfinished_topic_facts,
}


async def pre_send_check(channel: str, candidate: dict) -> str | None:
    """闸②的统一入口。**返回 ""＝本闸没参与；返回 None＝判定为 cancel，调用方必须不发。**

    两个闸都关时立刻返回 ""，一次额外查询都不发（守卫按计数桩钉这条）。
    shadow 只读数打留痕、不拦；gate 才真拦。读数或判定出任何异常 ⇒ 放行并记 WARNING，
    绝不让"我读不到"变成"这条消息没了"（隐性收紧）。
    """
    try:
        from app.flags.agent_flags import AGENT_FLAGS

        shadow = bool(AGENT_FLAGS.get("proactive_freshness_shadow", False))
        gate = bool(AGENT_FLAGS.get("proactive_freshness_gate", False))
        if not (shadow or gate):
            return ""
        reader = _READERS.get(channel)
        if reader is None:
            return ""
        verdict, reason, mark = verdict_for(channel, await reader(candidate))
        _logger.info("A39 闸② channel=%s%s %s", channel, "" if gate else "（影子）", mark)
        if gate and verdict == CANCEL:
            return None
        return mark
    except Exception as e:  # fail-open：闸自己坏掉不能把消息一起带走
        _logger.warning("A39 闸② failed channel=%s: %s", channel, e)
        return ""

