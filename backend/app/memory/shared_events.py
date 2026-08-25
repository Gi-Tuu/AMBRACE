"""Shared Memory 服务（Phase C，2026-08-14，演进规划 v2）

- 触发：用户标记（关键词，聊天时异步检测）/ AI 标记（高重要度记忆）
- 纪念日：is_anniversary=1 的事件在满月/周年触发回忆消息（scheduler 每日检查，last_recalled_at 防重复）
- 召回：对话构建时注入近期重要共同经历（AI 自然引用，防编造）
"""
from datetime import timedelta

from sqlalchemy import select

from app.models.shared_event import SharedEvent
from app.memory.format import format_memory_line  # X-1（2026-08-18）：记忆注入行公共格式化
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc as _now_naive

_logger = get_logger("memory.shared_events")

# 用户明确要求记住/标记的关键词（中文场景）
USER_MARK_KEYWORDS = (
    "记住", "别忘了", "别忘", "要记住", "记着", "很重要", "重要的事",
    "第一次", "纪念日", "周年", "今天特别", "特别的日子", "约定",
    "说好了", "拉钩", "答应我", "你要记得",
)
# 用户标记内容上限
MAX_DESC = 300
# 同类防重复窗口（小时）
DEDUP_HOURS = 24
# 纪念日触发间隔（天）：满月 30 / 60 / 100 / 半年 180 / 周年 365
ANNIVERSARY_DAYS = (30, 60, 100, 180, 365)


def detect_user_marked(text: str) -> bool:
    """用户消息是否包含明确"要记住"类标记意图"""
    t = (text or "").strip()
    if not t:
        return False
    return any(k in t for k in USER_MARK_KEYWORDS)


async def maybe_create_shared_event(db, user_id: int, character_id: int, content: str,
                                    event_type: str = "user_marked",
                                    importance: float = 0.7,
                                    is_anniversary: bool = False) -> SharedEvent | None:
    """检测命中标记意图 → 创建 Shared Event（同角色 24h 内同类型防重复）"""
    text = (content or "").strip()[:MAX_DESC]
    if not text:
        return None
    if not detect_user_marked(text) and event_type == "user_marked":
        return None
    now = _now_naive()
    # 防重复：同角色 24h 内已有同类型且同标题（前 20 字近似）则跳过
    dup = await db.execute(
        select(SharedEvent).where(
            SharedEvent.user_id == user_id,
            SharedEvent.character_id == character_id,
            SharedEvent.event_type == event_type,
            SharedEvent.created_at >= now - timedelta(hours=DEDUP_HOURS),
        )
    )
    for e in dup.scalars().all():
        if e.title[:20] == text[:20]:
            return None
    title = text[:40] + ("…" if len(text) > 40 else "")
    ev = SharedEvent(
        user_id=user_id, character_id=character_id, event_type=event_type,
        category="anniversary" if is_anniversary else "daily",
        title=title, description=text,
        importance=max(0.0, min(1.0, importance)),
        is_anniversary=is_anniversary,
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    _logger.info("shared event created: char=%d type=%s title=%.30s", character_id, event_type, title)
    return ev


async def list_shared_events(db, user_id: int, character_id: int | None = None,
                             limit: int = 20) -> list[SharedEvent]:
    q = select(SharedEvent).where(SharedEvent.user_id == user_id)
    if character_id is not None:
        q = q.where(SharedEvent.character_id == character_id)
    q = q.order_by(SharedEvent.importance.desc(), SharedEvent.created_at.desc()).limit(max(1, min(limit, 50)))
    return list((await db.execute(q)).scalars().all())


async def recall_text(db, user_id: int, character_id: int, limit: int = 2) -> str:
    """对话召回：近期高重要度共同经历 → 注入文本（AI 自然引用）"""
    rows = (
        await db.execute(
            select(SharedEvent)
            .where(SharedEvent.user_id == user_id, SharedEvent.character_id == character_id)
            .order_by(SharedEvent.importance.desc(), SharedEvent.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    if not rows:
        return ""
    lines = []
    for e in rows:
        d = (e.description or e.title or "").strip()
        if d:
            _ev_t = e.event_time or e.created_at
            # X-1（2026-08-18）：与主链路共用公共格式化函数（max_len=120 保持既有截断长度）
            _line = format_memory_line({"content": d, "created_at": _ev_t}, max_len=120)
            if _line:
                lines.append(_line)
    return "\n".join(lines) if lines else ""


async def check_anniversaries(db) -> list[SharedEvent]:
    """纪念日检查（scheduler 每日调用）：满月/周年到达且未触发过 → 返回待提醒事件（并标记 last_recalled_at）"""
    now = _now_naive()
    rows = (
        await db.execute(
            select(SharedEvent).where(SharedEvent.is_anniversary.is_(True))
        )
    ).scalars().all()
    due = []
    for e in rows:
        et = e.event_time.replace(tzinfo=None) if e.event_time and e.event_time.tzinfo else e.event_time
        if et is None:
            continue
        days = (now - et).days
        if days <= 0:
            continue
        if days not in ANNIVERSARY_DAYS:
            continue
        # 最近 3 天已提醒过则跳过（避免重复）
        if e.last_recalled_at and (now - e.last_recalled_at.replace(tzinfo=None)).days < 3:
            continue
        due.append(e)
    if due:
        for e in due:
            e.last_recalled_at = now
        await db.commit()
    return due


def anniversary_text(e: SharedEvent) -> str:
    """纪念日回忆消息文案"""
    et = e.event_time.replace(tzinfo=None) if e.event_time and e.event_time.tzinfo else e.event_time
    if et is None:
        return f"还记得吗？{e.title or '我们一起经历的那件事'}。时间过得真快。"
    days = (_now_naive() - et).days
    return f"还记得吗？{days}天前的今天，{e.title or '我们一起经历的那件事'}。时间过得真快。"
