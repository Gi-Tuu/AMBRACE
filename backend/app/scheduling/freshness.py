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
    "read_unfinished_topic_facts", "read_life_regression_facts", "refresh_life_items",
    "read_timer_facts", "check_timer_event", "timer_channel", "recent_user_texts",
    "items_for_prompt", "pre_send_check", "CANCEL", "KEEP", "REGENERATE",
]

# 话题行仍处于这两个状态之一才算「还该提起」（与 sources/unfinished_topic 的内核复核同口径）
_ACTIVE_TOPIC_STATUS = "进行中"

# 通道 2 重取结果的缓存键（放在 candidate 里，避免实闸开时第二次查库）
_LIFE_CACHE_KEY = "_a39_fresh_life_items"


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


async def refresh_life_items(items: list[dict]) -> list[dict]:
    """通道 2 的结构修法：按 id 回读**同几条**生活记忆，用当前正文，行没了就剔掉。

    collect 与 run 之间隔着 arbiter 排队与免打扰窗口，这几条记忆可能被归档／删除／改写；
    原实现直接把 collect 时的字符串带到 prompt 里，于是"我最近去爬了山"可能说的是一条已经不存在的记忆。

    读失败＝原样返回（本函数的失败口径与 `pre_send_check` 一致：绝不把"我读不到"变成"这条没了"）。
    """
    ids = [int(it["id"]) for it in items if str(it.get("id") or "").strip().isdigit()]
    if not ids or len(ids) != len(items):
        return items                    # 有缺 id 的项＝不是本通道形态，整体不动（不猜）
    try:
        from sqlalchemy import select

        from app.db.database import async_session_factory
        from app.models.memory import Memory

        async with async_session_factory() as db:
            rows = (
                await db.execute(
                    select(Memory.id, Memory.content, Memory.delete_at).where(Memory.id.in_(ids))
                )
            ).all()
        alive = {int(r[0]): str(r[1] or "") for r in rows if r[2] is None}
        out = []
        for it in items:
            fresh = alive.get(int(it["id"]))
            if fresh is None:
                continue
            keep = dict(it)
            keep["content"] = fresh[:200]
            out.append(keep)
        return out
    except Exception as e:
        _logger.warning("freshness life refresh failed ids=%s: %s", ids, e)
        return items


async def read_life_regression_facts(candidate: dict) -> FreshFacts:
    """通道 2 的生成前重取：`underlying_gone`＝列出的生活记忆**一条都不在了**。

    只剩"全没了"这一档是安全的 cancel：部分消失由 `items_for_prompt` 就地修好，不该升级为整条不发。
    重取的**内容**顺手缓存进 `candidate`（私有键），这样实闸开的时候不必再查第二次库。
    """
    items = candidate.get("life_items") or []
    if not items:
        return FreshFacts(underlying_gone=False)
    alive = await refresh_life_items(items)
    if len(alive) != len(items):
        candidate[_LIFE_CACHE_KEY] = alive
    return FreshFacts(underlying_gone=(len(alive) == 0))


def items_for_prompt(channel: str, candidate: dict, items: list[dict]) -> list[dict]:
    """影子档＝**一个字都不改**；实闸开＝用刚回读到的新鲜正文。

    分档的理由：影子窗口的职责是"量现状有多旧"，它一旦顺手改了 prompt，
    下次读数量的就不再是"旧现状"而是"我已经修过的现状"，这个窗口自会把自己的存在抹掉。
    """
    if channel != "life_regression":
        return items
    try:
        from app.flags.agent_flags import AGENT_FLAGS

        if not AGENT_FLAGS.get("proactive_freshness_gate", False):
            return items
    except Exception:
        return items
    fresh = candidate.get(_LIFE_CACHE_KEY)
    return fresh if isinstance(fresh, list) and fresh else items


def _flags() -> tuple[bool, bool]:
    from app.flags.agent_flags import AGENT_FLAGS

    return (bool(AGENT_FLAGS.get("proactive_freshness_shadow", False)),
            bool(AGENT_FLAGS.get("proactive_freshness_gate", False)))


async def read_moment_facts(moment_id: int, snapshot_comments: list) -> tuple[FreshFacts, list]:
    """通道 4（朋友圈评论）的重读：动态还在不在、评论列表比快照新几条。

    旧口径是**进函数时读一次**评论列表，然后在角色循环里一路用到底——前一个角色刚发的评论
    不在列表里，于是后面的角色会重复同一句、或去回复一条已经有人回过的评论；而这段时间里
    动态也可能已被作者删掉。
    返回 ``(事实, 重读到的评论行)``；**读失败返回 ``(None, snapshot_comments)``**——
    None 表示"这条闸今天没参与"，比给出一个 `[fresh=keep]` 诚实：没读到不等于没变。
    """
    try:
        from sqlalchemy import select

        from app.db.database import async_session_factory
        from app.models.life import AIMoment, MomentComment

        async with async_session_factory() as db:
            alive = (await db.execute(
                select(AIMoment.id).where(AIMoment.id == moment_id)
            )).first()
            rows = list((await db.execute(
                select(MomentComment).where(MomentComment.moment_id == moment_id)
                .order_by(MomentComment.created_at.asc())
            )).scalars().all())
        # A48：条数差在这里算——两个列表都在手里，与调用方随后打出的 快照=／现状= 同源
        return (FreshFacts(underlying_gone=alive is None,
                           items_delta=len(rows) - len(snapshot_comments)), rows)
    except Exception as e:
        _logger.warning("freshness moment read failed moment=%s: %s", moment_id, e)
        return None, snapshot_comments


async def moment_pre_send(moment_id: int, snapshot_comments: list) -> tuple[str | None, list]:
    """通道 4 入口：**返回 ``(cancel 标记, 该用的评论列表)``**。

    ``(None, …)``＝动态已不在，调用方必须停下这批评论；
    影子档只读数**一个字都不改**（列表原样退回），只有实闸开才把列表换成重读到的那份；
    读不到＝``("", 原列表)``，不冒充量过。
    """
    try:
        shadow, gate = _flags()
        if not (shadow or gate):
            return "", snapshot_comments
        facts, fresh = await read_moment_facts(moment_id, snapshot_comments)
        if facts is None:
            return "", snapshot_comments
        # A48：留痕里的两个数与判定用的 items_delta 必须同源（都来自这两次 len）；
        # 别在别处再数一遍列表长度，否则又会「话与数各说各话」。
        snapshot_n, fresh_n = len(snapshot_comments), len(fresh)
        verdict, reason, mark = verdict_for("moment_comment", facts,
                                            快照=snapshot_n, 现状=fresh_n)
        _logger.info("A39 闸② channel=%s%s %s", "moment_comment", "" if gate else "（影子）", mark)
        if gate and verdict == CANCEL:
            return None, snapshot_comments
        if gate:
            return mark, fresh
        return mark, snapshot_comments
    except Exception as e:
        _logger.warning("A39 闸② moment failed moment=%s: %s", moment_id, e)
        return "", snapshot_comments


# 通道 → 读数函数（没登记的通道＝本闸不看它，pre_send_check 直接放行）
_READERS: dict[str, object] = {
    "unfinished_topic": read_unfinished_topic_facts,
    "life_regression": read_life_regression_facts,
}


# 通道 6：只有这两类事件带"到达／吃药"信号表，其余 event_type 不猜（`_signal_seen` 内部
# 对未知类别会退化成用药表，拿来判"回家"这类事件就是把不相干的字面表拖进判据）。
_SIGNAL_KINDS = frozenset({"arrival", "medication"})


async def recent_user_texts(event, session_factory=None) -> list[str]:
    """这条承诺**之后**该会话里的用户正文（旧→新，最多 5 条）＝通道 6 唯一的取料口径。

    抽成单点有两个理由：①`executors/timer.py` 的 settled 判据读的是**同一条查询**（同 WHERE、
    同 limit），两处各写一遍＝ready 事件一拍发两条一样的 SELECT，且谓词会各自漂移；②闸②要能
    在 ready 上也拿到读数，又必须复用那一次取料（见 `check_timer_event(texts=...)`）。
    会话工厂由调用点显式传入（`timer.py` 的命门规矩：本模块不许 import `async_session_factory`，
    否则 tests/ 那 13 处打桩会静默绕过桩去查真库）。锚缺失／读不到 ⇒ 空列表（调用方按"没变"处理）。
    """
    session_id = getattr(event, "session_id", None)
    src_id = getattr(event, "source_message_id", None)
    if not session_id or not src_id:
        return []                           # 反查锚缺失＝未知，不猜
    try:
        from sqlalchemy import select

        from app.models.chat import ChatMessage

        factory = session_factory
        if factory is None:
            from app.db.database import async_session_factory as factory  # type: ignore[misc]
        async with factory() as db:
            rows = (
                await db.execute(
                    select(ChatMessage.content)
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.sender_type == "user",
                        ChatMessage.id > src_id,
                    )
                    .order_by(ChatMessage.id.desc())
                    .limit(5)
                )
            ).all()
    except Exception as e:
        _logger.warning("freshness timer read failed event=%s: %s", getattr(event, "id", "?"), e)
        return []
    return [r[0] for r in reversed(rows) if r[0]]


async def read_timer_facts(event, session_factory=None, texts=None) -> FreshFacts:
    """通道 6（timer）的兑现前重取：用户在这条承诺**之后**是否已经把结果说了。

    旧口径只有 `event_type == "ready"` 才做这件事，`back` 这类到点照问，于是"我已经到家了"
    之后还会被问一遍到家没。这里把同一条判据扩到非 ready：
      - `result_ready` ← `promise_parser.ready_result_seen`（既有探测器，不复制字面表）
      - `signal_seen` ← `prospective_intent._signal_seen`，只在 arrival／medication 两类上调用

    `texts` 传入时**不再查库**：调用点（`timer.py` 的 settled 判据）已经取过同一批正文。
    """
    from app.scheduling.prospective_intent import _signal_seen
    from app.scheduling.promise_parser import ready_result_seen

    kind = str(getattr(event, "event_type", "") or "")
    hint = str(getattr(event, "content_hint", "") or "").strip()
    if texts is None:
        texts = await recent_user_texts(event, session_factory)
    if not texts:
        return FreshFacts()
    return FreshFacts(result_ready=ready_result_seen(texts, hint),
                      signal_seen=bool(kind in _SIGNAL_KINDS and _signal_seen(kind, *texts)))


def timer_channel(event) -> str:
    return "timer_ready" if str(getattr(event, "event_type", "") or "") == "ready" else "timer_general"


async def check_timer_event(event, session_factory=None, texts=None) -> str | None:
    """timer 专用入口：语义与 `pre_send_check` 一致（""＝没参与，None＝cancel）。

    `texts`＝调用点已取好的用户正文，传了就复用、本闸一次查询都不发；两闸全关时连 `texts` 都不看。
    """
    try:
        shadow, gate = _flags()
        if not (shadow or gate):
            return ""
        channel = timer_channel(event)
        verdict, reason, mark = verdict_for(
            channel, await read_timer_facts(event, session_factory, texts=texts))
        _logger.info("A39 闸② channel=%s%s %s", channel, "" if gate else "（影子）", mark)
        if gate and verdict == CANCEL:
            return None
        return mark
    except Exception as e:
        _logger.warning("A39 闸② timer failed event=%s: %s", getattr(event, "id", "?"), e)
        return ""


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

