"""主动消息「主题熔断」闸门（L0，2026-09-09，零 LLM）。

解决的问题：timer / state_trigger / memory_review / life_regression / storyline 等多条主动
通道围绕同一生活主题（如"喝粥/吃饭"）在数小时内各发各的，且用户对主题已回应/已离场后仍在催。

设计约束：
- 纯函数 `topic_bucket` / `topic_closed_by_user` 可单测（零 IO、零 LLM）；
- `should_suppress` 只做只读查询，**任何异常 fail-open**（返回 False，绝不阻塞正常主动消息）；
- 不引入新表（近窗扫 `proactive_message_logs` 已发主动消息 + `chat_messages` 用户回复）；
- flag `proactive_topic_guard` 默认关，关=不做任何抑制。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.chat import ChatMessage, ChatSession
from app.models.character import ProactiveMessageLog
from app.utils.logger import get_logger

_logger = get_logger("scheduler.topic_guard")

# 节庆/纪念日等必须送达、不参与主题熔断的消息类型
GUARD_EXEMPT_TYPES = frozenset({
    "birthday", "holiday", "anniversary", "anniversary_recall",
})

# 主题桶：关键词 → 桶名。按表顺序优先，命中任一关键词即归入该桶（一条消息只归一个桶）。
_TOPIC_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("meal", ("粥", "吃饭", "饭", "吃面", "面条", "饿", "用餐", "早餐", "午饭", "晚饭",
              "宵夜", "菜凉", "趁热", "锅里")),
    ("sleep", ("睡觉", "睡了", "睡吧", "起床", "醒了", "醒一醒", "困", "休息", "早睡", "赖床", "午觉")),
    ("shower", ("洗澡", "澡", "洗头", "淋浴")),
    ("commute", ("出门", "到家", "回来路上", "去上课", "下课", "上班", "放学", "通勤")),
    ("meds", ("吃药", "药", "喝药", "头疼", "头痛", "胃疼", "不舒服", "多喝热水")),
]

# 用户在该主题上"已闭环 / 已婉拒 / 已离场"的信号词：命中即认为本主题近期不必再主动催。
_TOPIC_CLOSE_WORDS: dict[str, tuple[str, ...]] = {
    "meal": ("吃过", "吃完", "吃了", "吃过了", "吃饱", "不饿", "在外面吃", "饭堂吃", "食堂吃",
             "去上课", "上课了", "在上课", "下课再说", "不用了", "别催", "等会吃", "晚点吃", "吃过面"),
    "sleep": ("醒了", "起床了", "起来了", "不用叫", "不睡了", "在忙"),
    "shower": ("洗完", "洗过", "洗好了", "不洗了", "等会洗"),
    "commute": ("到了", "到教室", "到公司", "回来了", "不用接", "在上课", "去上课了"),
    "meds": ("吃过药", "好了", "不疼了", "没事了", "不用了"),
}

# 缺省窗口与阈值（调用方传参可覆盖）
DEFAULT_WINDOW_HOURS = 3
DEFAULT_MAX_SAME_TOPIC = 2

# 近窗用户消息取样条数（只用于判闭环，够用即可）
_USER_TEXT_LIMIT = 10


def topic_bucket(text: str) -> str | None:
    """把一条消息归入主题桶；无命中返回 None。纯函数。"""
    t = text or ""
    for bucket, kws in _TOPIC_KEYWORDS:
        if any(kw in t for kw in kws):
            return bucket
    return None


def topic_closed_by_user(recent_user_texts: list[str], bucket: str | None) -> bool:
    """用户最近消息是否已对该主题闭环（吃过了 / 去上课了 / 不用了…）。纯函数。"""
    if not bucket or not recent_user_texts:
        return False
    kws = _TOPIC_CLOSE_WORDS.get(bucket, ())
    return any(any(kw in (txt or "") for kw in kws) for txt in recent_user_texts)


async def _recent_proactive_contents(db, character_id: int, since) -> list[str]:
    rows = await db.execute(
        select(ProactiveMessageLog.content)
        .where(
            ProactiveMessageLog.character_id == character_id,
            ProactiveMessageLog.created_at >= since,
        )
    )
    return [r[0] or "" for r in rows.all()]


async def _recent_user_texts(db, character_id: int, since) -> list[str]:
    """该角色最近会话里的用户消息（跨该角色会话；取最近若干条足够判闭环）。"""
    rows = await db.execute(
        select(ChatMessage.content)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .where(
            ChatSession.character_id == character_id,
            ChatMessage.sender_type == "user",
            ChatMessage.created_at >= since,
        )
        .order_by(ChatMessage.id.desc())
        .limit(_USER_TEXT_LIMIT)
    )
    return [r[0] or "" for r in rows.all()]


async def should_suppress(
    character_id: int,
    content: str,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    max_same_topic: int = DEFAULT_MAX_SAME_TOPIC,
) -> tuple[bool, str]:
    """是否抑制这条主动消息。返回 (是否抑制, 原因)；只读 + fail-open（异常一律 False）。

    规则 1：用户近窗内已对该主题闭环/离场/婉拒 → 抑制；
    规则 2：近窗内同主题主动消息已达上限 → 抑制。
    """
    try:
        bucket = topic_bucket(content)
        if bucket is None:
            return False, "no-topic"
        since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).replace(tzinfo=None)
        async with async_session_factory() as db:
            sent = await _recent_proactive_contents(db, character_id, since)
            user_texts = await _recent_user_texts(db, character_id, since)
        if topic_closed_by_user(user_texts, bucket):
            return True, f"topic-{bucket}-closed-by-user"
        same = sum(1 for c in sent if topic_bucket(c) == bucket)
        if same >= max_same_topic:
            return True, f"topic-{bucket}-cap({same}>={max_same_topic})"
        return False, f"topic-{bucket}-ok({same})"
    except Exception as e:  # fail-open：闸门自身故障绝不阻塞发送
        _logger.warning("topic guard fail-open char=%d: %s", character_id, e)
        return False, "guard-error-fail-open"
