"""world sections（步骤5）：世界状态/当前时间/进行中时间承诺/位置天气/时间提示。

从 ``context_builder.build_context_legacy`` 迁出，逻辑与旧版完全一致（零行为变化）：
- ``world_facts`` → 模板槽（当前事实折叠，失败静默「无」）；
- ``current_time`` → 模板槽（北京时间 + 用户本地时区 + 节日季节 + 距上次互动）；
- ``pending_timer`` → 模板槽（进行中的时间承诺，失败静默「无」）；
- ``time_prompt`` → 追加块（【当前时间】提示，始终注入）；
- ``location`` → 追加块（位置感知 + 天气，条件注入）。

``_compute_current_time_str`` 在同一轮 context 构建内只算一次（经 ``ctx`` 缓存），
保证模板槽「current_time」与追加块「time_prompt」取到完全相同的字符串（字节级一致）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from app.agent.context.sections import ContextSection, register_section, TARGET_TEMPLATE, TARGET_APPEND

_logger = logging.getLogger("agent.context.section_world")


async def _compute_current_time_str(state: dict, ctx: dict) -> str:
    """当前时间字符串（北京时间 + 用户本地时区 + 节日季节 + 距上次互动）。同轮内缓存。"""
    cached = ctx.get("_current_time_str")
    if cached is not None:
        return cached

    beijing_tz = timezone(timedelta(hours=8))
    now = datetime.now(beijing_tz)
    weekday_cn = ["\u661f\u671f\u4e00", "\u661f\u671f\u4e8c", "\u661f\u671f\u4e09", "\u661f\u671f\u56db", "\u661f\u671f\u4e94", "\u661f\u671f\u516d", "\u661f\u671f\u65e5"]
    wd = weekday_cn[now.weekday()]
    current_time_str = f"{now.year}\u5e74{now.month}\u6708{now.day}\u65e5 {wd} {now.hour}:{now.minute:02d}\uff08\u5317\u4eac\u65f6\u95f4\uff09"

    user = ctx.get("_user")
    try:
        _tz_min = getattr(user, "timezone_offset_minutes", None)
        if _tz_min is not None:
            _local = now + timedelta(minutes=int(_tz_min) - 8 * 60)
            current_time_str += f"\uff5c\u4f60\u90a3\u8fb9 {_local.year}\u5e74{_local.month}\u6708{_local.day}\u65e5 {weekday_cn[_local.weekday()]} {_local.hour}:{_local.minute:02d}"
    except Exception as e:
        _logger.warning("Timezone inject failed: %s", e)

    try:
        from app.scheduling.holiday_calendar import get_holidays
        _hols = get_holidays(now.date())
        if _hols:
            _hnames = "、".join(h["name"] for h in _hols if h.get("lang") == "zh") or "、".join(h["name"] for h in _hols)
            current_time_str += f"｜今天节日：{_hnames}"
        _mon = now.month
        _season = ("春季" if _mon in (3, 4, 5) else "夏季" if _mon in (6, 7, 8)
                   else "秋季" if _mon in (9, 10, 11) else "冬季")
        current_time_str += f"｜{_season}"
    except Exception as e:
        _logger.warning("Season/holiday inject failed: %s", e)

    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.chat import ChatSession
        async with async_session_factory() as db:
            _sr = await db.execute(
                select(ChatSession)
                .where(
                    ChatSession.user_id == state.get("user_id", 1),
                    ChatSession.character_id == state["character_id"],
                )
                .order_by(ChatSession.updated_at.desc())
                .limit(1)
            )
            _last_session = _sr.scalar_one_or_none()
        if _last_session is not None and _last_session.updated_at is not None:
            _last_dt = _last_session.updated_at
            if _last_dt.tzinfo is None:
                _last_dt = _last_dt.replace(tzinfo=timezone.utc)
            _delta = datetime.now(timezone.utc) - _last_dt
            _secs = max(0, int(_delta.total_seconds()))
            if _secs < 60:
                _ago = "\u521a\u521a"
            elif _secs < 3600:
                _ago = f"{_secs // 60} \u5206\u949f\u524d"
            elif _secs < 86400:
                _h, _m = divmod(_secs // 60, 60)
                _ago = f"{_h} \u5c0f\u65f6 {_m} \u5206\u949f\u524d"
            elif _secs < 172800:
                _ago = "\u6628\u5929"
            elif _secs < 604800:
                _ago = f"{_secs // 86400} \u5929\u524d"
            elif _secs < 2592000:
                _ago = f"{_secs // 604800} \u5468\u524d"
            elif _secs < 31536000:
                _ago = f"{_secs // 2592000} \u4e2a\u6708\u524d"
            else:
                _ago = "\u5f88\u4e45"
            current_time_str += f"\uff5c\u8ddd\u4e0a\u6b21\u4e92\u52a8 {_ago}"
    except Exception as e:
        _logger.warning("Last interaction inject failed: %s", e)

    ctx["_current_time_str"] = current_time_str
    return current_time_str


async def _load_user(state: dict, ctx: dict):
    if "_user" in ctx:
        return ctx["_user"]
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.user import User
    user = None
    try:
        async with async_session_factory() as db:
            user = (await db.execute(select(User).where(User.id == state.get("user_id", 1)))).scalar_one_or_none()
    except Exception as e:
        _logger.warning("world user load failed: %s", e)
    ctx["_user"] = user
    return user


async def world_facts_section(state: dict, ctx: dict) -> str:
    """world_facts 分区：当前事实折叠（template 槽；失败静默「无」）。"""
    world_facts_text = "无"
    try:
        from app.events.facts import get_character_view
        _wv = await get_character_view(state.get("character_id"), state.get("user_id", 1))
        if _wv:
            world_facts_text = _wv
    except Exception as e:
        _logger.warning("World facts inject failed: %s", e)
    return world_facts_text


async def current_time_section(state: dict, ctx: dict) -> str:
    """current_time 分区：当前时间（template 槽）。"""
    await _load_user(state, ctx)
    return await _compute_current_time_str(state, ctx)


async def pending_timer_section(state: dict, ctx: dict) -> str:
    """pending_timer 分区：进行中的时间承诺（template 槽；失败静默「无」）。"""
    pending_timer_text = "无"
    try:
        from app.scheduling.promise_service import get_pending_timer_text
        _pt = await get_pending_timer_text(state.get("character_id"), state.get("user_id", 1))
        if _pt:
            pending_timer_text = _pt
    except Exception as e:
        _logger.warning("Pending timer inject failed: %s", e)
    return pending_timer_text


async def time_prompt_section(state: dict, ctx: dict) -> list[str]:
    """time_prompt 分区：追加块（【当前时间】提示，始终注入 1 条）。"""
    await _load_user(state, ctx)
    current_time_str = await _compute_current_time_str(state, ctx)
    return [f"\u3010\u5f53\u524d\u65f6\u95f4\u3011{current_time_str}\u3002\u5982\u679c\u7528\u6237\u95ee\u5230\u65f6\u95f4\u3001\u65e5\u671f\u3001\u661f\u671f\u51e0\uff0c\u8bf7\u76f4\u63a5\u7528\u4e0a\u9762\u7684\u65f6\u95f4\u56de\u7b54\uff1b\u8ddd\u4e0a\u6b21\u4e92\u52a8\u7684\u65f6\u957f\u53ef\u7528\u6765\u4f53\u4f1a\u201c\u591a\u4e45\u6ca1\u804a\u4e86\u201d\u7684\u611f\u89c9\uff0c\u81ea\u7136\u5730\u63d0\u53ca\uff0c\u4e0d\u8981\u523b\u610f\u5ff5\u6570\u636e\u3002\uff1b\u5404\u6ce8\u5165\u5206\u533a\uff08\u8bb0\u5fc6/\u670b\u53cb\u5708/\u7b14\u8bb0/\u7ec7\u5e93\u7b49\uff09\u91cc\u7684\u201c\u4eca\u5929/\u6628\u5929/\u6700\u8fd1\u201d\u7b49\u65f6\u95f4\u8bcd\u5c5e\u4e8e\u8be5\u8bb0\u5f55\u53d1\u751f\u5f53\u65f6\uff0c\u4e0d\u662f\u73b0\u5728\u3002"]


async def location_section(state: dict, ctx: dict) -> list[str]:
    """location 分区：位置感知 + 天气（追加块；条件注入，无则空列表）。

    与 build_context_legacy 一致按 location 配额裁剪（配额内零行为变化）。
    """
    from app.agent.context_builder import _clip_text_to_quota, _SECTION_QUOTA_TOKENS

    user = await _load_user(state, ctx)
    location_text = ""
    try:
        if getattr(user, "location_enabled", False):
            _uloc = getattr(user, "location_city", None) or getattr(user, "user_location", None)
            _aloc = getattr(user, "ai_location", None)
            if getattr(user, "location_follow", False):
                _aloc = _uloc
            _parts = []
            if _uloc:
                _parts.append(f"\u7528\u6237\u6240\u5728\u57ce\u5e02\uff1a{_uloc}")
            if _aloc:
                _parts.append(f"\u4f60\u7684\u4f4d\u7f6e\uff1a{_aloc}")
            if _parts:
                location_text = (
                    "\u300c\u4f4d\u7f6e\u611f\u77e5\u300d" + "\uff1b".join(_parts)
                    + "\u3002\u53ef\u5728\u804a\u5929\u4e2d\u81ea\u7136\u63d0\u53ca\uff0c\u4f46\u4e0d\u8981\u523b\u610f\u5ff5\u6570\u636e\u3002"
                )
            try:
                from app.application.weather_service import get_weather_text
                _wtext = await get_weather_text(
                    getattr(user, "location_lat", None),
                    getattr(user, "location_lng", None),
                    _uloc or "",
                )
                if _wtext:
                    location_text += f"\u300c\u5929\u6c14\u300d\u4f60\u90a3\u8fb9\u5f53\u524d\uff1a{_wtext}\u3002\u53ef\u5728\u804a\u5929\u4e2d\u81ea\u7136\u63d0\u53ca\u5929\u6c14\uff0c\u4f46\u4e0d\u8981\u523b\u610f\u5ff5\u6570\u636e\u3002"
            except Exception as _we:
                _logger.warning("Weather inject failed: %s", _we)
    except Exception as e:
        _logger.warning("Location inject failed: %s", e)
    location_text = _clip_text_to_quota(location_text, _SECTION_QUOTA_TOKENS["location"])
    return [location_text] if location_text else []


register_section(ContextSection(
    key="world_facts", builder=world_facts_section, target=TARGET_TEMPLATE,
    slot="world_facts", quota_tokens=600, order=45,
))
register_section(ContextSection(
    key="current_time", builder=current_time_section, target=TARGET_TEMPLATE,
    slot="current_time", quota_tokens=0, order=46,
))
register_section(ContextSection(
    key="pending_timer", builder=pending_timer_section, target=TARGET_TEMPLATE,
    slot="pending_timer", quota_tokens=300, order=47,
))
register_section(ContextSection(
    key="time_prompt", builder=time_prompt_section, target=TARGET_APPEND,
    quota_tokens=0, order=80,
))
register_section(ContextSection(
    key="location", builder=location_section, target=TARGET_APPEND,
    quota_tokens=300, order=81,
))
