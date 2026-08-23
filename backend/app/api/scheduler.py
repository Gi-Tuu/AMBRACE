"""主动交流系统 API — 设置管理"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.models.proactive_settings import (
    ProactiveSettings, HolidayPreference,
)
from app.models.character import AICharacter
from app.scheduler.holiday_calendar import get_holidays
from app.scheduler import scheduler as scheduler_engine
from app.utils.logger import get_logger
from app.auth.deps import get_current_user_id
from app.i18n import tr_lang

router = APIRouter(prefix="/api/v1/scheduler", tags=["Scheduler"])
_logger = get_logger("api.scheduler")


# ── 角色主动交流设置 ──


async def _check_char_owned(db: AsyncSession, character_id: int, user_id: int, lang: str = "zh"):
    char_result = await db.execute(
        select(AICharacter).where(
            AICharacter.id == character_id,
            AICharacter.user_id == user_id,
        )
    )
    if char_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))


@router.get("/settings/{character_id}")
async def get_settings(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """获取角色的主动交流配置"""
    # 确认角色归属
    await _check_char_owned(db, character_id, user_id, lang)
    char_result = await db.execute(
        select(AICharacter).where(AICharacter.id == character_id)
    )
    if not char_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))

    # 获取或创建默认设置
    result = await db.execute(
        select(ProactiveSettings).where(ProactiveSettings.character_id == character_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = ProactiveSettings(character_id=character_id)
        db.add(settings)
        await db.flush()
        await db.commit()
        await db.refresh(settings)

    return {
        "character_id": settings.character_id,
        "enable_proactive": settings.enable_proactive,
        "idle_threshold_minutes": settings.idle_threshold_minutes,
        "frequency": settings.frequency,
        "max_daily_proactive": settings.max_daily_proactive,
        "birthday_enabled": settings.birthday_enabled,
        "holiday_enabled": settings.holiday_enabled,
        "diary_enabled": settings.diary_enabled,
        "moments_enabled": settings.moments_enabled,
        "state_trigger_enabled": settings.state_trigger_enabled,
        "memory_review_enabled": bool(settings.memory_review_enabled),
        "cold_war_enabled": settings.cold_war_enabled,
        "mood_badge_enabled": settings.mood_badge_enabled,
        "image_gen_enabled": bool(settings.image_gen_enabled),
        "active_image_gen_enabled": bool(getattr(settings, "active_image_gen_enabled", False)),
        "privacy_enabled": bool(getattr(settings, "privacy_enabled", True)),
        "privacy_lock_enabled": bool(getattr(settings, "privacy_lock_enabled", True)),
        "reasoning_level": int(getattr(settings, "reasoning_level", 0) or 0),
        "show_tools_enabled": bool(getattr(settings, "show_tools_enabled", False)),
        "moments_comment_enabled": bool(getattr(settings, "moments_comment_enabled", True)),
        "weave_full_inject_enabled": bool(getattr(settings, "weave_full_inject_enabled", False)),
        "dnd_enabled": bool(getattr(settings, "dnd_enabled", False)),
        "dnd_start": str(getattr(settings, "dnd_start", "00:00") or "00:00"),
        "dnd_end": str(getattr(settings, "dnd_end", "07:00") or "07:00"),
        "check_in_enabled": bool(getattr(settings, "check_in_enabled", False)),
        "control_enabled": bool(getattr(settings, "control_enabled", False)),
        "life_enabled": bool(getattr(settings, "life_enabled", True)),
        "life_intensity": str(getattr(settings, "life_intensity", "low") or "low"),
        "life_share_enabled": bool(getattr(settings, "life_share_enabled", True)),
    }


@router.put("/settings/{character_id}")
async def update_settings(
    character_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """更新角色的主动交流配置"""
    await _check_char_owned(db, character_id, user_id, lang)
    char_result = await db.execute(
        select(AICharacter).where(AICharacter.id == character_id)
    )
    if not char_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))

    result = await db.execute(
        select(ProactiveSettings).where(ProactiveSettings.character_id == character_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = ProactiveSettings(character_id=character_id)
        db.add(settings)
        await db.flush()

    # 更新允许的字段
    allowed_fields = {
        "enable_proactive": bool,
        "idle_threshold_minutes": int,
        "frequency": str,
        "max_daily_proactive": int,
        "birthday_enabled": bool,
        "holiday_enabled": bool,
        "diary_enabled": bool,
        "moments_enabled": bool,
        "state_trigger_enabled": bool,
        "memory_review_enabled": bool,
        "cold_war_enabled": bool,
        "mood_badge_enabled": bool,
        "image_gen_enabled": bool,
        "active_image_gen_enabled": bool,
        "privacy_enabled": bool,
        "privacy_lock_enabled": bool,
        "reasoning_level": int,
        "show_tools_enabled": bool,
        "moments_comment_enabled": bool,
        "weave_full_inject_enabled": bool,
        "dnd_enabled": bool,
        "dnd_start": str,
        "life_enabled": bool,
        "life_intensity": str,
        "life_share_enabled": bool,
        "dnd_end": str,
        "check_in_enabled": bool,
        "control_enabled": bool,
    }
    for field, field_type in allowed_fields.items():
        if field in data:
            value = data[field]
            if field == "frequency" and value not in ("low", "medium", "high"):
                raise HTTPException(status_code=400, detail=tr_lang(lang, "frequency_invalid"))
            if field == "reasoning_level" and value not in (0, 1, 2):
                raise HTTPException(status_code=400, detail=tr_lang(lang, "reasoning_invalid"))
            if field in ("dnd_start", "dnd_end"):
                _t = str(value or "")
                try:
                    _h, _m = _t.split(":")
                    if not (0 <= int(_h) <= 23 and 0 <= int(_m) <= 59):
                        raise ValueError
                except Exception:
                    raise HTTPException(status_code=400, detail=tr_lang(lang, "hhmm_invalid", field=field))
            setattr(settings, field, field_type(value))

    await db.commit()
    return {"status": "ok", "message": "设置已更新"}


# ── 主动消息统计 ──


@router.get("/stats")
async def get_proactive_stats(
    character_id: int | None = None,
    days: int = 7,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """主动消息效果统计：触发/发送/拦截数量 + 用户回复率（按消息后该会话是否有用户回复估算）"""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func as sa_func
    from app.models.chat_message import ChatMessage
    from app.models.proactive_settings import ProactiveMessageLog, ProactiveTriggerLog

    days = max(1, min(days, 90))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    cond_log = [ProactiveMessageLog.created_at >= since]
    cond_trig = [ProactiveTriggerLog.created_at >= since]
    if character_id is not None:
        cond_log.append(ProactiveMessageLog.character_id == character_id)
        cond_trig.append(ProactiveTriggerLog.character_id == character_id)
    else:
        cond_trig.append(ProactiveTriggerLog.user_id == user_id)
        cond_log.append(ProactiveMessageLog.user_id == user_id)

    sent_rows = (await db.execute(
        select(ProactiveMessageLog).where(*cond_log)
    )).scalars().all()
    total_sent = len(sent_rows)

    # 回复率：每条主动消息后，同一会话是否出现用户新消息
    replied = 0
    for m in sent_rows[:200]:
        if m.session_id is None:
            continue
        n = (await db.execute(
            select(sa_func.count()).where(
                ChatMessage.session_id == m.session_id,
                ChatMessage.sender_type == "user",
                ChatMessage.created_at > m.created_at,
            )
        )).scalar() or 0
        if n > 0:
            replied += 1
    reply_rate = round(replied / total_sent, 2) if total_sent else 0.0

    trig_rows = (await db.execute(
        select(ProactiveTriggerLog).where(*cond_trig)
    )).scalars().all()
    total_triggered = len(trig_rows)
    total_cancelled = sum(1 for t in trig_rows if t.decision == "rejected")

    type_stats: dict[str, int] = {}
    for t in trig_rows:
        type_stats[t.trigger_type] = type_stats.get(t.trigger_type, 0) + 1

    return {
        "total_triggered": total_triggered,
        "total_sent": total_sent,
        "total_cancelled": total_cancelled,
        "reply_rate": reply_rate,
        "trigger_type_stats": type_stats,
    }


# ── 节日管理 ──


@router.get("/holidays/today")
async def get_today_holidays():
    """获取今天的所有节日"""
    from datetime import date
    holidays = get_holidays(date.today())
    return {
        "date": date.today().isoformat(),
        "holidays": holidays,
    }


@router.get("/holidays/blocked")
async def get_blocked_holidays(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """获取用户屏蔽的节日列表"""
    result = await db.execute(
        select(HolidayPreference).where(
            HolidayPreference.user_id == user_id,
            HolidayPreference.enabled == False,
        )
    )
    blocked = result.scalars().all()
    return {
        "blocked": [b.holiday_name for b in blocked],
        "total": len(blocked),
    }


@router.post("/holidays/block")
async def block_holiday(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """屏蔽某个节日"""
    holiday_name = data.get("holiday_name", "").strip()
    if not holiday_name:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "holiday_name_required"))

    # upsert
    result = await db.execute(
        select(HolidayPreference).where(
            HolidayPreference.user_id == user_id,
            HolidayPreference.holiday_name == holiday_name,
        )
    )
    pref = result.scalar_one_or_none()
    if pref:
        pref.enabled = False
    else:
        pref = HolidayPreference(user_id=user_id, holiday_name=holiday_name, enabled=False)
        db.add(pref)
    await db.commit()
    return {"status": "ok", "holiday_name": holiday_name, "blocked": True}


@router.post("/holidays/unblock")
async def unblock_holiday(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """取消屏蔽某个节日"""
    holiday_name = data.get("holiday_name", "").strip()
    if not holiday_name:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "holiday_name_required"))

    await db.execute(
        delete(HolidayPreference).where(
            HolidayPreference.user_id == user_id,
            HolidayPreference.holiday_name == holiday_name,
        )
    )
    await db.commit()
    return {"status": "ok", "holiday_name": holiday_name, "blocked": False}


# ── 调度器状态 ──


@router.get("/status")
async def get_scheduler_status():
    """获取调度器运行状态"""
    return {
        "running": scheduler_engine.is_running(),
        "active_hours": f"{scheduler_engine.ACTIVE_HOUR_START}:00-{scheduler_engine.ACTIVE_HOUR_END}:00",
        "idle_check_interval_seconds": scheduler_engine.IDLE_CHECK_INTERVAL,
    }



# ── 事件时钟（定时承诺）管理：列表 + 删除（2026-08-15） ──


@router.get("/timers/{character_id}")
async def list_timers(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """列出该角色当前未到期的定时承诺（供私聊右上角「事件时钟」展示）。"""
    await _check_char_owned(db, character_id, user_id, lang)
    from datetime import datetime, timezone, timedelta
    from app.models.scheduled_event import ScheduledEvent as _SE
    result = await db.execute(
        select(_SE).where(
            _SE.character_id == character_id,
            _SE.user_id == user_id,
            _SE.status == "pending",
        ).order_by(_SE.trigger_at.asc())
    )
    events = result.scalars().all()
    now = datetime.now(timezone.utc)
    cn_tz = timezone(timedelta(hours=8))
    out = []
    for e in events:
        ts = e.trigger_at
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts <= now:
            continue
        left_min = max(1, int((ts - now).total_seconds() / 60))
        cn = ts.astimezone(cn_tz)
        out.append({
            "id": e.id,
            "owner": e.owner or "ai",
            "event_type": e.event_type or "back",
            "content_hint": (e.content_hint or "").strip() or None,
            "left_minutes": left_min,
            "due_at": f"{cn.year}-{cn.month:02d}-{cn.day:02d} {cn.hour:02d}:{cn.minute:02d}",
        })
    return {"items": out}


@router.delete("/timers/{character_id}/{event_id}")
async def delete_timer(
    character_id: int,
    event_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除一条定时承诺（用户主动取消不必要的计时）。"""
    await _check_char_owned(db, character_id, user_id, lang)
    from app.models.scheduled_event import ScheduledEvent as _SE
    event = await db.get(_SE, event_id)
    if event is None or event.character_id != character_id or event.user_id != user_id:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "timer_not_found"))
    event.status = "cancelled"
    await db.commit()
    return {"status": "ok"}
