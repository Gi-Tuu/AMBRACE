"""定时承诺服务 — scheduled_events 表的创建、到期扫描、兑现"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.scheduled_event import ScheduledEvent
from app.utils.logger import get_logger

_logger = get_logger("scheduler.promise")

# 错过触发后仍补发的窗口（超过则放弃，避免半夜突然弹消息）
GRACE_MINUTES = 120


async def create_event(timer_info: dict) -> ScheduledEvent | None:
    """创建定时承诺事件；同角色、同承诺方（owner）的旧 pending 先取消（新承诺覆盖旧承诺）"""
    if not timer_info:
        return None
    owner = timer_info.get("sender", "ai")
    async with async_session_factory() as db:
        # 取消同角色、同承诺方旧的 pending（AI 承诺与用户承诺互不覆盖）
        old_result = await db.execute(
            select(ScheduledEvent).where(
                ScheduledEvent.character_id == timer_info["character_id"],
                ScheduledEvent.status == "pending",
                ScheduledEvent.owner == owner,
            )
        )
        for old in old_result.scalars().all():
            old.status = "cancelled"
            _logger.info(
                "Cancelled old scheduled event id=%d for char=%d (owner=%s)",
                old.id, timer_info["character_id"], owner,
            )

        event = ScheduledEvent(
            user_id=timer_info["user_id"],
            character_id=timer_info["character_id"],
            session_id=timer_info["session_id"],
            trigger_at=timer_info["trigger_at"],
            event_type=timer_info.get("event_type", "back"),
            source_message_id=timer_info.get("source_message_id"),
            owner=owner,
            content_hint=(timer_info.get("promise_text") or "")[:200] or None,
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)
        _logger.info(
            "Scheduled event created: id=%d char=%d type=%s owner=%s trigger_at=%s",
            event.id, event.character_id, event.event_type, owner, event.trigger_at,
        )
        return event


async def get_due_events() -> list[ScheduledEvent]:
    """获取已到期且状态为 pending 的事件（Python 层二次判定，规避 naive/aware 比较隐患）"""
    now = datetime.now(timezone.utc)
    async with async_session_factory() as db:
        result = await db.execute(
            select(ScheduledEvent).where(ScheduledEvent.status == "pending")
        )
        events = list(result.scalars().all())
    due = []
    for e in events:
        ts = e.trigger_at
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts <= now:
            due.append(e)
    return due


async def recover_overdue_events() -> None:
    """服务器启动/重启后恢复：2 小时内到期的补触发，超过的标记 expired"""
    now = datetime.now(timezone.utc)
    due = await get_due_events()
    for event in due:
        trigger_ts = event.trigger_at
        if trigger_ts is not None and trigger_ts.tzinfo is None:
            trigger_ts = trigger_ts.replace(tzinfo=timezone.utc)
        overdue = (now - trigger_ts).total_seconds() / 60
        if overdue > GRACE_MINUTES:
            async with async_session_factory() as db:
                e = await db.get(ScheduledEvent, event.id)
                if e and e.status == "pending":
                    e.status = "expired"
                    await db.commit()
                    _logger.info("Scheduled event id=%d expired (overdue %.0fmin)", event.id, overdue)
        else:
            _logger.info("Scheduled event id=%d recovered (overdue %.0fmin)", event.id, overdue)
    if due:
        _logger.info("Recovery scan done: %d due events", len(due))


async def mark_fired(event_id: int) -> None:
    """标记事件已兑现"""
    async with async_session_factory() as db:
        event = await db.get(ScheduledEvent, event_id)
        if event and event.status == "pending":
            event.status = "fired"
            await db.commit()


async def get_pending_timer_text(character_id: int, user_id: int) -> str:
    """该角色进行中的定时承诺（未到期）的人话描述，供对话上下文注入防剧情穿帮。

    返回空串表示无进行中承诺；有则给出「谁承诺了什么、还有多久、到点时间、行为约束」。
    """
    now = datetime.now(timezone.utc)
    async with async_session_factory() as db:
        result = await db.execute(
            select(ScheduledEvent)
            .where(
                ScheduledEvent.character_id == character_id,
                ScheduledEvent.user_id == user_id,
                ScheduledEvent.status == "pending",
            )
            .order_by(ScheduledEvent.trigger_at.asc())
        )
        events = list(result.scalars().all())
    lines = []
    cn_tz = timezone(timedelta(hours=8))
    for e in events:
        ts = e.trigger_at
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts <= now:
            continue
        left_min = max(1, int((ts - now).total_seconds() / 60))
        cn_ts = ts.astimezone(cn_tz)
        hint = (e.content_hint or "").strip()
        owner = e.owner or "ai"
        if owner == "user":
            who = f"用户承诺了「{hint}」" if hint else "用户说要去办点事（承诺了时间）"
            lines.append(
                f"- {who}，还有约 {left_min} 分钟（约 {cn_ts.hour:02d}:{cn_ts.minute:02d} 完成）。"
                f"现在还没到时间：如果用户提前出现，可以自然地问「这么快就好了？」，但不要替用户说「你回来了/做完了」。"
            )
        else:
            who = f"你承诺了「{hint}」" if hint else "你说要去做某件事（承诺了时间）"
            lines.append(
                f"- {who}，还有约 {left_min} 分钟（约 {cn_ts.hour:02d}:{cn_ts.minute:02d} 回来）。"
                f"现在还没到时间，你还没回来/没做完；如果用户问起，就自然地说你还在忙，不要提前演「已经回来了/做完了」。"
            )
    return "\n".join(lines)
