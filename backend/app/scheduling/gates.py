"""节流闸与只读查询（A20 批 1）

本模块自 ``app/scheduling/arbiter.py:67-448`` 逐字节搬入（A20 批 1，2026-10-02）。
边界＝**只读库、只判时间窗，不做决策、不发消息、不写日志链路**。

logger 名故意保留旧名 ``scheduler.arbiter``（D-1 已定）：台账、告警与排障都按
``scheduler.arbiter`` 关键字检索日志，换名等于排障口径全变。
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from app.db.database import async_session_factory
from app.models.chat import ChatMessage
from app.models.character import ProactiveMessageLog, ProactiveSettings
from app.models.character import ProactiveStorylineItem
from app.models.character import ProactiveTriggerLog
from app.utils.logger import get_logger
from app.utils.timeutil import app_day_start_utc, now_naive_utc, to_naive_utc

# 判定所需的常量 / 纯函数沿用 arbiter 原来源（不新增依赖）
from app.domain.proactivity.decision import (
    UNREPLIED_COOLDOWN_HOURS,
    UNREPLIED_COOLDOWN_LIMIT,
    USER_ACTIVE_MINUTES,
    _in_dnd_window,
)
from app.domain.proactivity.sleep import SLEEP_KEYWORDS, SLEEP_HOUR
# B1-③：闲置停发判据（inactive_char_skip 用）
from app.domain.proactivity import outreach as _oc

_logger = get_logger("scheduler.arbiter")

# ── 统计辅助 ──



async def has_user_said_sleep(character_id: int, user_id: int) -> bool:
    """夜晚时段（北京时间 21:00-次日 8:00）内，用户最近一条消息是否说"睡觉"。
    夜晚起算点为"最近一个 21:00"（凌晨跨天也生效）；若之后又发了消息（如"睡不着/又起来了"），自动恢复。"""
    cn_tz = timezone(timedelta(hours=8))
    now_cn = datetime.now(cn_tz)
    # 白天（8:00-21:00）不静默
    if 8 <= now_cn.hour < SLEEP_HOUR:
        return False
    # 夜晚起算点：>=21 点 → 今天 21:00；凌晨（<8 点） → 昨天 21:00
    ref = now_cn.replace(hour=SLEEP_HOUR, minute=0, second=0, microsecond=0)
    if now_cn.hour < SLEEP_HOUR:
        ref -= timedelta(days=1)
    since_utc = ref.astimezone(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        from app.application.chat_service import get_latest_session_id
        session_id = await get_latest_session_id(user_id, character_id)
        if not session_id:
            return False
        msg_result = await db.execute(
            select(ChatMessage.content)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.sender_type == "user",
                ChatMessage.created_at >= since_utc,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        last_content = msg_result.scalar_one_or_none()
    return bool(last_content and any(kw in last_content for kw in SLEEP_KEYWORDS))


async def get_hourly_active_count(character_id: int) -> int:
    """最近 1 小时该角色发出的主动消息数"""
    since = now_naive_utc() - timedelta(hours=1)
    async with async_session_factory() as db:
        result = await db.execute(
            select(func.count()).where(
                ProactiveMessageLog.character_id == character_id,
                ProactiveMessageLog.created_at >= since,
            )
        )
        return result.scalar() or 0


async def get_last_proactive_time(character_id: int) -> datetime | None:
    """该角色最近一条主动消息的发送时间（用于最小间隔保护）"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(ProactiveMessageLog.created_at)
            .where(ProactiveMessageLog.character_id == character_id)
            .order_by(ProactiveMessageLog.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def get_motivation_approved_count(character_id: int, since) -> int:
    """独立想念通道计数：motivation 成功执行的次数。

    用 ProactiveTriggerLog(trigger_type=motivation, decision=approved) 统计——
    storyline 落库 message_type 统一为 storyline，无法区分 motivation 类型。"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(func.count()).where(
                ProactiveTriggerLog.character_id == character_id,
                ProactiveTriggerLog.trigger_type == "motivation",
                ProactiveTriggerLog.decision == "approved",
                ProactiveTriggerLog.created_at >= since,
            )
        )
        return result.scalar() or 0


# ── outreach 投放口径三闸（2026-09-13 交接 §②③）——「已发送」计数 IO ──


def _cn_hour_now() -> int:
    """当前北京时间小时（时段窗口闸用；单独成函数便于测试注入，锁定时段）。"""
    return datetime.now(timezone(timedelta(hours=8))).hour


async def get_daily_sent_count(character_id: int, message_type: str) -> int:
    """该角色**应用时区当日已发送**的某类型主动消息数（类型配比闸 ②）。

    统计口径（交接 §②③，与项目既有「已发送」口径一致，勿改成候选口径）：
    - 只算 ``proactive_message_logs``（``send_to_session`` 落库的「已发送」行，log_proactive=True），
      **不统计候选/审批流水**（``proactive_trigger_logs`` 的 approved/rejected 都不算）；
    - 同一 storyline 事件的后续切片不重复计数（``send_to_session`` 不再落 log），
      与 ``get_hourly_active_count`` / ``MAX_PER_HOUR`` 同源同口径；
    - 日期边界 = 应用时区当天 00:00（复用 ``utils.timeutil.app_day_start_utc``，
      与 ``triggers.get_daily_count`` 同源；默认 +8 时与旧北京口径同值）。
    """
    since = app_day_start_utc()
    async with async_session_factory() as db:
        result = await db.execute(
            select(func.count()).where(
                ProactiveMessageLog.character_id == character_id,
                ProactiveMessageLog.message_type == message_type,
                ProactiveMessageLog.created_at >= since,
            )
        )
        return result.scalar() or 0


async def get_session_daily_sent_count(character_id: int, session_id: int) -> int:
    """同一 (character_id, session_id) 当日**已发送**主动消息数（单会话限频闸 ③，口径同 ②）。"""
    since = app_day_start_utc()
    async with async_session_factory() as db:
        result = await db.execute(
            select(func.count()).where(
                ProactiveMessageLog.character_id == character_id,
                ProactiveMessageLog.session_id == session_id,
                ProactiveMessageLog.created_at >= since,
            )
        )
        return result.scalar() or 0


async def get_session_last_sent_at(character_id: int, session_id: int) -> datetime | None:
    """同一 (character_id, session_id) 最近一条**已发送**主动消息时间（最小间隔闸 ③，口径同上）。"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(ProactiveMessageLog.created_at)
            .where(
                ProactiveMessageLog.character_id == character_id,
                ProactiveMessageLog.session_id == session_id,
            )
            .order_by(ProactiveMessageLog.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def get_recent_proactive_messages(character_id: int, limit: int = 2) -> str:
    """该角色最近主动消息 + 最近 AI 对话回复（用于生成时防重复）。

    A（2026-09-01）：并入该角色最近 24h 的 3 条 AI 对话回复——此前 previous_messages 只取
    ProactiveMessageLog（主动消息日志），普通对话里 AI 自己刚回复过的话不在防重复范围，
    导致生成主动消息时 LLM 逐句照抄上一条对话回复（真机 sam 案）；对话回复失败仅记日志
    （fail-open，不影响主动链路）。合并去重、按时间倒序、整体截断约 400 字。
    """
    from datetime import datetime as _dt
    from app.utils.timeutil import now_naive_utc
    items: list[tuple[object, str]] = []  # (created_at, content)
    async with async_session_factory() as db:
        result = await db.execute(
            select(ProactiveMessageLog.created_at, ProactiveMessageLog.content)
            .where(ProactiveMessageLog.character_id == character_id)
            .order_by(ProactiveMessageLog.created_at.desc())
            .limit(limit)
        )
        for _ts, _text in result.all():
            if _text:
                items.append((_ts, _text))
    try:
        from datetime import timedelta as _timedelta
        from app.models.chat import ChatMessage, ChatSession
        _since = now_naive_utc() - _timedelta(hours=24)
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(ChatMessage.created_at, ChatMessage.content)
                .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                .where(
                    ChatSession.character_id == character_id,
                    ChatMessage.sender_type == "ai",
                    ChatMessage.created_at >= _since,
                )
                .order_by(ChatMessage.id.desc())
                .limit(3)
            )).all()
            for _ts, _text in rows:
                if _text:
                    items.append((_ts, _text))
    except Exception as e:
        _logger.warning("recent AI chat replies load failed char=%s: %s", character_id, e)
    seen: set[str] = set()
    uniq: list[str] = []
    for _ts, _text in sorted(items, key=lambda x: (x[0] is not None, x[0] or _dt.min), reverse=True):
        if _text not in seen:
            seen.add(_text)
            uniq.append(_text)
    return "\n".join(uniq)[:400]


# 免打扰窗口缓存：{character_id: (expire_ts, (start_min, end_min) | None)}（60s 过期）
_dnd_cache: dict[int, tuple[float, tuple[int, int] | None]] = {}


async def unreplied_cooldown_active(character_id: int, user_id: int) -> bool:
    """连续 UNREPLIED_COOLDOWN_LIMIT 条主动消息用户均未回复，且最近一条在冷却时长内 → 冷却中"""
    async with async_session_factory() as db:
        logs = (
            await db.execute(
                select(ProactiveMessageLog)
                .where(
                    ProactiveMessageLog.character_id == character_id,
                    ProactiveMessageLog.session_id.is_not(None),
                )
                .order_by(ProactiveMessageLog.created_at.desc())
                .limit(UNREPLIED_COOLDOWN_LIMIT)
            )
        ).scalars().all()
    if len(logs) < UNREPLIED_COOLDOWN_LIMIT:
        return False
    for log in logs:
        async with async_session_factory() as db:
            replied = (
                await db.execute(
                    select(func.count()).where(
                        ChatMessage.session_id == log.session_id,
                        ChatMessage.sender_type == "user",
                        ChatMessage.created_at > log.created_at,
                    )
                )
            ).scalar() or 0
        if replied > 0:
            return False  # 最近这些消息里有用户回复 → 不冷却
    return now_naive_utc() - to_naive_utc(logs[0].created_at) < timedelta(hours=UNREPLIED_COOLDOWN_HOURS)


async def get_dnd_window(character_id: int) -> tuple[int, int] | None:
    """该角色免打扰窗口（分钟制起止）。dnd_enabled=False → None（沿用硬编码 0-7 点）。
    结果缓存 60 秒，避免每 tick 查库。"""
    import time as _time
    now_ts = _time.time()
    cached = _dnd_cache.get(character_id)
    if cached and now_ts - cached[0] < 60:
        return cached[1]
    window: tuple[int, int] | None = None
    try:
        async with async_session_factory() as db:
            st = (
                await db.execute(
                    select(ProactiveSettings).where(ProactiveSettings.character_id == character_id)
                )
            ).scalar_one_or_none()
        if st and st.dnd_enabled:
            def _parse(t: str) -> int:
                try:
                    h, m = (t or "00:00").split(":")
                    return int(h) * 60 + int(m)
                except Exception:
                    return 0
            window = (_parse(st.dnd_start), _parse(st.dnd_end))
    except Exception:
        window = None
    _dnd_cache[character_id] = (now_ts, window)
    return window


async def is_dnd_now(character_id: int, cn_now: datetime) -> bool:
    """是否处于免打扰：dnd_enabled 开启用配置时段；未开启沿用硬编码深夜 0-7 点"""
    window = await get_dnd_window(character_id)
    cn_minute = cn_now.hour * 60 + cn_now.minute
    if window is not None:
        return _in_dnd_window(cn_minute, window)
    return cn_now.hour < 7


async def is_user_active(character_id: int, user_id: int) -> bool:
    """用户最近是否在活跃聊天（有用户消息）"""
    since = now_naive_utc() - timedelta(minutes=USER_ACTIVE_MINUTES)
    async with async_session_factory() as db:
        from app.application.chat_service import get_latest_session_id
        session_id = await get_latest_session_id(user_id, character_id)
        if not session_id:
            return False
        msg_result = await db.execute(
            select(func.count()).where(
                ChatMessage.session_id == session_id,
                ChatMessage.sender_type == "user",
                ChatMessage.created_at >= since,
            )
        )
        return (msg_result.scalar() or 0) > 0


async def get_hours_since_last_user_message(character_id: int) -> float | None:
    """该角色最近一条用户消息距今小时数；一条都没有 → None。

    跨该角色**全部会话**统计（不按 user 细分）：outreach 是角色维度行为，任一用户近期
    说过话即视为该角色活跃；从未对话的角色 → None（按停发处理）。
    F-6（v3.4.6 审查）：查询失败直接上抛（由调用方 inactive_char_skip 的 fail-open 捕获），
    不再与「无消息」的 None 混淆——否则查询失败轮活跃角色被误停发。
    """
    try:
        from app.models.chat import ChatSession

        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(ChatMessage.created_at)
                    .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                    .where(
                        ChatSession.character_id == character_id,
                        ChatMessage.sender_type == "user",
                    )
                    .order_by(ChatMessage.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
    except Exception as e:
        _logger.warning("last user message load failed char=%s: %s", character_id, e)
        raise
    if row is None:
        return None
    return (now_naive_utc() - to_naive_utc(row)).total_seconds() / 3600.0


async def inactive_char_skip(character_id: int) -> bool:
    """非活跃角色停发门控（B1-③ 配额让位，2026-09-08）：应停发 → True。

    flag ``proactive_inactive_char_skip`` 关 → 恒 False（零行为变化）；判据为纯函数
    ``outreach.skip_inactive_char``（近 INACTIVE_CHAR_WINDOW_HOURS 无用户消息 → 停发）。
    异常静默 False（fail-open：门控异常不误伤活跃角色；含 F-6 查询失败上抛，此处兜住）。
    """
    try:
        from app.agent.loop import AGENT_FLAGS as _af

        if not _af.get("proactive_inactive_char_skip", False):
            return False
        return _oc.skip_inactive_char(await get_hours_since_last_user_message(character_id))
    except Exception as e:
        _logger.warning("inactive char skip check failed char=%s: %s", character_id, e)
        return False


async def has_pending_timer(character_id: int) -> bool:
    """该角色是否有未到期的定时承诺（有则跳过随机节律，避免穿帮）"""
    from app.models.life import ScheduledEvent
    async with async_session_factory() as db:
        result = await db.execute(
            select(func.count()).where(
                ScheduledEvent.character_id == character_id,
                ScheduledEvent.status == "pending",
                ScheduledEvent.trigger_at > now_naive_utc(),
            )
        )
        return (result.scalar() or 0) > 0


async def has_pending_storyline(character_id: int) -> bool:
    """该角色是否还有未发送完的主动剧情切片（有则跳过随机节律，避免剧情重叠）"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(func.count()).where(
                ProactiveStorylineItem.character_id == character_id,
                ProactiveStorylineItem.status == "pending",
            )
        )
        return (result.scalar() or 0) > 0


async def get_active_characters() -> list[dict]:
    """获取所有启用了主动行为的活跃角色（复用 triggers 逻辑）"""
    from app.scheduling.triggers import get_active_characters as _get
    return await _get()
