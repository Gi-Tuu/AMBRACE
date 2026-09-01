"""主动交流触发器 — 闲置/生日/节日条件检查"""
from datetime import date, timedelta
from sqlalchemy import select, func
from app.db.database import async_session_factory
from app.models.chat import ChatSession
from app.models.chat import ChatMessage
from app.models.user import User
from app.models.character import AICharacter
from app.models.character import (
    ProactiveSettings, HolidayPreference, ProactiveMessageLog,
)
from app.scheduler.holiday_calendar import get_holidays
from app.utils.logger import get_logger
from app.utils.timeutil import beijing_day_start_utc as _beijing_day_start_utc

_logger = get_logger("scheduler.triggers")


async def get_active_characters() -> list[dict]:
    """获取所有开启了主动交流的角色及其配置"""
    results = []
    async with async_session_factory() as db:
        # 联表查询：AICharacter + ProactiveSettings
        stmt = (
            select(
                AICharacter, ProactiveSettings,
                User.id.label("user_id"), User.username, User.nickname, User.birthday,
            )
            .join(ProactiveSettings, ProactiveSettings.character_id == AICharacter.id, isouter=True)
            .join(User, User.id == AICharacter.user_id)
            .where(AICharacter.is_active == True)
        )
        rows = await db.execute(stmt)
        for row in rows:
            char: AICharacter = row[0]
            settings: ProactiveSettings | None = row[1]
            user_id: int = row[2]
            username: str = row[3]
            nickname: str = row[4]
            birthday: str | None = row[5]

            # 默认启用
            enable = settings.enable_proactive if settings else True
            idle_threshold = settings.idle_threshold_minutes if settings else 120
            freq = settings.frequency if settings else "medium"
            max_daily = settings.max_daily_proactive if settings else 5
            bday_enabled = settings.birthday_enabled if settings else True
            hol_enabled = settings.holiday_enabled if settings else True

            if not enable:
                continue

            results.append({
                "character_id": char.id,
                "character_name": char.name,
                "character_bio": char.bio or "",
                "character_personality": char.personality or "",
                "current_status": char.current_status or "",
                "relationship_summary": char.relationship_summary or "",
                "user_id": user_id,
                "username": username,
                "nickname": nickname,
                "birthday": birthday,
                "idle_threshold_minutes": idle_threshold,
                "frequency": freq,
                "max_daily_proactive": max_daily,
                "birthday_enabled": bday_enabled,
                "holiday_enabled": hol_enabled,
            })
    return results


async def get_latest_session(character_id: int, user_id: int) -> dict | None:
    """获取最近的活跃会话（按最新消息时间选会话，避免 updated_at 污染选错）"""
    from app.services.chat_service import get_latest_session_id
    session_id = await get_latest_session_id(user_id, character_id)
    if not session_id:
        return None
    async with async_session_factory() as db:
        session = await db.get(ChatSession, session_id)
        if not session:
            return None
        return {"id": session.id, "updated_at": session.updated_at}


async def get_last_messages(session_id: int, limit: int = 10) -> str:
    """获取最近几条消息（用于上下文）。
    P0-2（2026-08-24）：主动消息承接语境扩容——默认 10 条 × 每条约 120 字，供 generate_proactive_event 注入。"""
    async with async_session_factory() as db:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        msgs = list(reversed(result.scalars().all()))
        lines = []
        for m in msgs:
            role = "用户" if m.sender_type == "user" else "你"
            lines.append(f"{role}: {m.content[:120]}")
        return "\n".join(lines)


# 随机节律产出的主动消息类型（get_daily_count 统计口径）
_RHYTHM_MESSAGE_TYPES = ("proactive", "proactive_chat", "storyline", "status_update", "greeting", "goodnight")


async def get_daily_count(character_id: int) -> int:
    """获取今天已发送的主动消息数（随机节律类）。

    节律事件落库 message_type 已改为 storyline/proactive_chat 等，旧口径只数
    "proactive" 永远为 0 → 每日上限失效；现按节律产出类型统计。
    """
    start = _beijing_day_start_utc()
    async with async_session_factory() as db:
        stmt = (
            select(func.count())
            .select_from(ProactiveMessageLog)
            .where(
                ProactiveMessageLog.character_id == character_id,
                ProactiveMessageLog.created_at >= start,
                ProactiveMessageLog.message_type.in_(_RHYTHM_MESSAGE_TYPES),
            )
        )
        return (await db.execute(stmt)).scalar() or 0


async def was_birthday_sent_today(character_id: int) -> bool:
    """今天是否已送出生日祝福"""
    start = _beijing_day_start_utc()
    async with async_session_factory() as db:
        stmt = (
            select(ProactiveMessageLog)
            .where(
                ProactiveMessageLog.character_id == character_id,
                ProactiveMessageLog.created_at >= start,
                ProactiveMessageLog.message_type == "birthday",
            )
        )
        result = await db.execute(stmt)
        return result.first() is not None


async def was_holiday_sent_today(character_id: int) -> bool:
    """该角色今天是否已发送过节日祝福（每个角色每天最多一条节日祝福）"""
    start = _beijing_day_start_utc()
    async with async_session_factory() as db:
        stmt = (
            select(ProactiveMessageLog)
            .where(
                ProactiveMessageLog.character_id == character_id,
                ProactiveMessageLog.created_at >= start,
                ProactiveMessageLog.message_type == "holiday",
            )
        )
        result = await db.execute(stmt)
        return result.first() is not None


async def is_holiday_blocked(user_id: int, holiday_name: str) -> bool:
    """用户是否屏蔽了这个节日"""
    async with async_session_factory() as db:
        stmt = (
            select(HolidayPreference)
            .where(
                HolidayPreference.user_id == user_id,
                HolidayPreference.holiday_name == holiday_name,
                HolidayPreference.enabled == False,
            )
        )
        result = await db.execute(stmt)
        return result.first() is not None



async def get_birthday_candidates() -> list[dict]:
    """检查当天过生日的用户，返回需要发送祝福的候选列表"""
    today = date.today()
    today_mmdd = today.strftime("%m-%d")
    candidates = []

    active_chars = await get_active_characters()
    for char_info in active_chars:
        try:
            if not char_info["birthday_enabled"]:
                continue
            if char_info["birthday"] != today_mmdd:
                continue
            if await was_birthday_sent_today(char_info["character_id"]):
                continue

            session = await get_latest_session(
                char_info["character_id"], char_info["user_id"]
            )
            if not session:
                continue

            candidates.append({**char_info, "session_id": session["id"]})
        except Exception as e:
            _logger.warning("check_birthday error char=%d: %s", char_info["character_id"], e)

    return candidates


_ANNIVERSARY_MILESTONES = (7, 30, 100, 365, 730)  # 认识第 N 天


async def get_first_session(character_id: int, user_id: int) -> dict | None:
    """获取最早的活跃会话（认识日）"""
    async with async_session_factory() as db:
        stmt = (
            select(ChatSession)
            .where(ChatSession.user_id == user_id, ChatSession.character_id == character_id)
            .order_by(ChatSession.created_at.asc())
            .limit(1)
        )
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()
        if not session:
            return None
        return {"id": session.id, "created_at": session.created_at}


async def get_anniversary_candidates() -> list[dict]:
    """认识纪念日候选：认识第 7/30/100/365/730 天当天触发，每角色每天最多一条"""
    from datetime import date as _date
    active_chars = await get_active_characters()
    candidates = []
    for char_info in active_chars:
        try:
            session = await get_first_session(
                char_info["character_id"], char_info["user_id"]
            )
            if not session or not session["created_at"]:
                continue
            first_bj = (session["created_at"] + timedelta(hours=8)).date()
            days = (_date.today() - first_bj).days + 1
            if days not in _ANNIVERSARY_MILESTONES:
                continue
            # 防重复：今天已发过 anniversary
            start = _beijing_day_start_utc()
            async with async_session_factory() as db:
                stmt = (
                    select(ProactiveMessageLog)
                    .where(
                        ProactiveMessageLog.character_id == char_info["character_id"],
                        ProactiveMessageLog.created_at >= start,
                        ProactiveMessageLog.message_type == "anniversary",
                    )
                )
                if (await db.execute(stmt)).first() is not None:
                    continue
            candidates.append({**char_info, "session_id": session["id"], "anniversary_days": days})
        except Exception as e:
            _logger.warning("check_anniversary error char=%d: %s", char_info["character_id"], e)
    return candidates


async def get_holiday_candidates() -> list[dict]:
    """检查当天节日，返回需要发送祝福的候选列表（同一天多个节日合并为一条）"""
    today = date.today()
    holidays = get_holidays(today)
    if not holidays:
        return []

    candidates = []
    active_chars = await get_active_characters()

    for char_info in active_chars:
        try:
            if not char_info["holiday_enabled"]:
                continue
            session = await get_latest_session(
                char_info["character_id"], char_info["user_id"]
            )
            if not session:
                continue
            # 每角色每天最多一条节日祝福
            if await was_holiday_sent_today(char_info["character_id"]):
                continue

            # 合并当天所有未被屏蔽的节日名（如 "国庆节、中秋节"）
            names = []
            for holiday in holidays:
                holiday_name = holiday["name"]
                if await is_holiday_blocked(char_info["user_id"], holiday_name):
                    continue
                if holiday_name not in names:
                    names.append(holiday_name)
            if not names:
                continue

            joined = "、".join(names)
            if len(names) > 1:
                joined = "、".join(names[:-1]) + "和" + names[-1]

            candidates.append({
                **char_info,
                "session_id": session["id"],
                "holiday_name": joined,
            })
        except Exception as e:
            _logger.warning("check_holiday error char=%d: %s", char_info["character_id"], e)

    return candidates
async def proactive_enabled(character_id: int) -> bool:
    """角色是否开启主动互动主开关（无设置记录默认开启）。供 memory_review/emotion_care/pet_care 等通道校验"""
    async with async_session_factory() as db:
        s = (await db.execute(
            select(ProactiveSettings).where(ProactiveSettings.character_id == character_id)
        )).scalar_one_or_none()
    return s.enable_proactive if s else True


async def memory_review_enabled(character_id: int) -> bool:
    """记忆复习子开关（主动交流下）：关闭主动互动则一并关闭；无设置记录默认开启"""
    async with async_session_factory() as db:
        s = (await db.execute(
            select(ProactiveSettings).where(ProactiveSettings.character_id == character_id)
        )).scalar_one_or_none()
    if s is None:
        return True
    return bool(s.enable_proactive and s.memory_review_enabled)
