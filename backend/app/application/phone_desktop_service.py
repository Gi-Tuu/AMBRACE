"""小手机上下文注入（2026-08-11）：角色日历备注 + 浏览器搜索历史 → 聊天上下文（仅文本）"""
from datetime import datetime, timedelta, timezone

from app.utils.logger import get_logger

_logger = get_logger("services.phone_desktop")


# 段落末尾的硬约束（写给模型看）：完成/过期条目禁止再当待办催办（批G，2026-09-26）
_NOTE_STATUS_RULE = (
    "（[已完成]/[已过期] 的条目都已结束，只能当作历史提及，"
    "禁止再当成待办催办或追问进度；[待办]/[今天]/[未来] 才可以提醒。）"
)


def _calendar_status_tag(note_date: str, status: str, today) -> str:
    """日历项标记：done 优先，其次按日期比 today 分过期/今天/未来。"""
    if status == "done":
        return "[已完成]"
    try:
        d = datetime.strptime(note_date, "%Y-%m-%d").date()
    except ValueError:
        return "[未来]"
    if d < today:
        return "[已过期]"
    if d == today:
        return "[今天]"
    return "[未来]"


async def get_phone_desktop_inject_text(character_id: int) -> str:
    """返回小手机注入文本（空串 = 无内容，调用方显示“无”）"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.device import CalendarNote, BrowserHistory, MemoNote
        from app.utils.timeutil import app_local_now

        today = app_local_now().date()
        parts: list[str] = []
        async with async_session_factory() as db:
            # 日历备注：昨天 ~ 未来 7 天（用户/AI 共同维护）
            start = (today - timedelta(days=1)).isoformat()
            end = (today + timedelta(days=7)).isoformat()
            notes = (await db.execute(
                select(CalendarNote)
                .where(CalendarNote.character_id == character_id,
                       CalendarNote.note_date >= start,
                       CalendarNote.note_date <= end)
                .order_by(CalendarNote.note_date)
            )).scalars().all()
            if notes:
                parts.append("小手机日历备注（近期）：" + "；".join(
                    f"{n.note_date} {_calendar_status_tag(n.note_date, getattr(n, 'status', 'active') or 'active', today)} {n.note_text}" for n in notes[:8]
                ) + _NOTE_STATUS_RULE)
            # 浏览器搜索历史：保留 7 天，取最近 5 条
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
            hist = (await db.execute(
                select(BrowserHistory)
                .where(BrowserHistory.character_id == character_id,
                       BrowserHistory.created_at >= cutoff)
                .order_by(BrowserHistory.created_at.desc())
                .limit(5)
            )).scalars().all()
            if hist:
                parts.append("小手机浏览器最近搜索：" + "、".join(f"{h.created_at:%Y-%m-%d} {h.query}" for h in hist))
            # 备忘录：最近 3 条（AI 与用户共同维护，AI 主动记录）
            memos = (await db.execute(
                select(MemoNote)
                .where(MemoNote.character_id == character_id)
                .order_by(MemoNote.created_at.desc())
                .limit(3)
            )).scalars().all()
            if memos:
                parts.append("小手机备忘录：" + "；".join(
                    f"{m.created_at:%Y-%m-%d} {'[已完成]' if (getattr(m, 'status', 'active') or 'active') == 'done' else '[待办]'} {(m.text or '')[:40]}" for m in memos
                ) + _NOTE_STATUS_RULE)
        return "\n".join(parts)
    except Exception as e:
        _logger.warning("phone desktop inject failed: %s", e)
        return ""
