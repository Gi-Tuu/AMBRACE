"""仲裁器 — 统一决策：收集事件源 → 按优先级裁定 → 限额保护 → 执行"""
import json
import time as _time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from app.db.database import async_session_factory
from app.models.chat import ChatMessage
from app.models.character import ProactiveMessageLog, ProactiveSettings
from app.models.character import ProactiveStorylineItem
from app.models.character import ProactiveTriggerLog
from app.models.character import AICharacter
from app.scheduling.unfinished_topic import run_unfinished_topic
from app.scheduling.life_regression import run_life_regression
from app.scheduling.prospective_intent import run_prospective_due  # Ariadne 模块G（2026-09-04）
from app.utils.logger import get_logger
from app.utils.async_tasks import spawn_background

# AMBRACE 3.10：arbiter 事件源 TriggerSource 化——导入 sources 包即触发各源注册
from app.scheduling.sources import SourceContext, all_sources, get_source, to_item_dict

_logger = get_logger("scheduler.arbiter")

# F2-b（2026-08-31）：决策纯函数/常量迁至 domain/proactivity，此处重导出保持兼容
# （monkeypatch arbiter.<name> 仍有效：函数体经本模块命名空间解析）
from app.domain.proactivity.decision import (  # noqa: E402,F401
    CONTEXT_SORT_BONUS,
    MAX_PER_HOUR,
    MIN_PROACTIVE_INTERVAL_MINUTES,
    MOTIVATION_MAX_PER_6H,
    MOTIVATION_MAX_PER_DAY,
    MOTIVATION_SPEAK_THRESHOLD,
    REFLECTION_BONUS,
    REFLECTION_LOOKBACK_DAYS,
    UNREPLIED_COOLDOWN_HOURS,
    UNREPLIED_COOLDOWN_LIMIT,
    USER_ACTIVE_MINUTES,
    _apply_reflection_bonus,
    _context_sort_bonus,
    _in_dnd_window,
    _motivation_score,
    scheduler_gray_character,
)
from app.domain.proactivity.sleep import SLEEP_KEYWORDS, SLEEP_HOUR  # noqa: E402,F401
# B1-③（2026-09-04，方案 §5.4）：主动接触意图层纯函数（闲置分级 + 意图选择）
from app.domain.proactivity import outreach as _oc  # noqa: E402

# 审计 P1-06：rejected 触发日志节流（同角色同类型最小间隔秒，approved 必记）
REJECTED_LOG_THROTTLE_SECONDS = 300
_rejected_log_cache: dict[tuple[int, str], float] = {}

# B1-③：可走接触意图选择的主动搭话事件类型（调用 generate_proactive_event 的几条）
PROACTIVE_OUTREACH_TYPES = ("greeting", "proactive_chat", "goodnight", "status_update", "motivation")




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
    since = datetime.now(timezone.utc) - timedelta(hours=1)
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
    last = logs[0].created_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last < timedelta(hours=UNREPLIED_COOLDOWN_HOURS)


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
    since = datetime.now(timezone.utc) - timedelta(minutes=USER_ACTIVE_MINUTES)
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


async def has_pending_timer(character_id: int) -> bool:
    """该角色是否有未到期的定时承诺（有则跳过随机节律，避免穿帮）"""
    from app.models.life import ScheduledEvent
    async with async_session_factory() as db:
        result = await db.execute(
            select(func.count()).where(
                ScheduledEvent.character_id == character_id,
                ScheduledEvent.status == "pending",
                ScheduledEvent.trigger_at > datetime.now(timezone.utc),
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


# ── 事件源采集 ──

_DEFAULT_CTX = SourceContext()

async def collect_timer_events() -> list[dict]:
    """到期定时承诺（最高优先级）。逻辑见 scheduling/sources/timer.py（AMBRACE 3.10）。"""
    return [ti.to_dict() for ti in await get_source("timer").collect(_DEFAULT_CTX)]


async def collect_special_events() -> list[dict]:
    """生日 / 节日 / 认识纪念日。逻辑见 scheduling/sources/special.py（AMBRACE 3.10）。"""
    return [ti.to_dict() for ti in await get_source("special").collect(_DEFAULT_CTX)]


async def flush_storyline_items() -> int:
    """快速发送到期的主动事件切片（独立 3 秒循环调用，不走 30 秒仲裁 tick）。

    同一事件的切片正常情况下每个循环至多发一段（group 去重），保持 3 秒间隔；
    停机恢复时可能一次补发多段，属正常追赶。
    """
    from app.scheduling import scheduler as engine
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    # 深夜静默：北京时间 0:00-6:59 不发送剧情线切片（用户睡眠时段不打扰）
    cn_now = datetime.now(timezone(timedelta(hours=8)))
    if cn_now.hour < 7:
        return 0
    async with async_session_factory() as db:
        result = await db.execute(
            select(ProactiveStorylineItem)
            .where(
                ProactiveStorylineItem.status == "pending",
                ProactiveStorylineItem.send_at <= now_naive,
            )
            .order_by(ProactiveStorylineItem.send_at.asc())
            .limit(20)
        )
        items = result.scalars().all()
    sent = 0
    seen_groups: set[str] = set()
    for item_obj in items:
        if item_obj.group_id in seen_groups:
            continue
        seen_groups.add(item_obj.group_id)
        # 过期保护：距计划时间超过 2 小时的切片作废（避免停机后补发一堆旧消息）
        if now_naive - item_obj.send_at > timedelta(hours=2):
            async with async_session_factory() as db:
                db_item = await db.get(ProactiveStorylineItem, item_obj.id)
                if db_item:
                    db_item.status = "expired"
                    await db.commit()
            _logger.info("Storyline item %d expired", item_obj.id)
            continue
        # 用户 21 点后说过睡觉 → 剩余剧情线作废，不再打扰
        try:
            if await has_user_said_sleep(item_obj.character_id, item_obj.user_id):
                async with async_session_factory() as db:
                    db_item = await db.get(ProactiveStorylineItem, item_obj.id)
                    if db_item:
                        db_item.status = "expired"
                        await db.commit()
                _logger.info("Storyline item %d expired (user sleep)", item_obj.id)
                continue
        except Exception as e:
            _logger.warning("Storyline sleep check failed: %s", e)
        _reasoning = (item_obj.reasoning or "").strip() if getattr(item_obj, "reasoning", None) else ""
        _extra = None
        if item_obj.seq == 0 and _reasoning:
            import json as _json
            _extra = _json.dumps({"reasoning": _reasoning}, ensure_ascii=False)
        await engine.send_to_session(
            item_obj.session_id, item_obj.character_id, item_obj.user_id,
            item_obj.content, message_type="storyline",
            log_proactive=(item_obj.seq == 0),
            extra_meta=_extra,
        )
        async with async_session_factory() as db:
            db_item = await db.get(ProactiveStorylineItem, item_obj.id)
            if db_item:
                db_item.status = "sent"
                await db.commit()
        sent += 1
    if sent:
        _logger.info("Storyline flush sent %d item(s)", sent)
    return sent


async def _session_last_message_at(session_id: int) -> datetime | None:
    """会话最后一条消息的 created_at（naive UTC），供 idle 计算；无消息返回 None。
    P-fix（2026-08-31）：SSE 流式路径落用户/AI 消息时未更新 chat_sessions.updated_at，
    idle 基准改用消息表真实最新时间，避免停留在上次主动消息（send_to_session）导致 idle 虚高。"""
    try:
        async with async_session_factory() as _db:
            _at = (
                await _db.execute(
                    select(func.max(ChatMessage.created_at))
                    .where(ChatMessage.session_id == session_id)
                )
            ).scalar()
            return _at
    except Exception:
        return None


async def collect_rhythm_events() -> list[dict]:
    """随机节律采样：时间窗 + 概率 + 每日上限 + 计时器互斥。逻辑见 scheduling/sources/rhythm.py（AMBRACE 3.10）。"""
    return [ti.to_dict() for ti in await get_source("rhythm").collect(_DEFAULT_CTX)]


async def collect_state_trigger_events() -> list[dict]:
    """状态触发兜底（优先级 2）：八维状态达阈值 → 主动消息/朋友圈。逻辑见 scheduling/sources/state_trigger.py（AMBRACE 3.10）。"""
    return [ti.to_dict() for ti in await get_source("state_trigger").collect(_DEFAULT_CTX)]


async def _compute_motivation(character_id: int) -> float:
    """读取角色八维状态计算动机分；无状态/异常返回 0（失败静默，不影响调度）"""
    try:
        from app.models.character import CharacterState
        async with async_session_factory() as db:
            st = (
                await db.execute(
                    select(CharacterState).where(CharacterState.character_id == character_id)
                )
            ).scalar_one_or_none()
        if st is None:
            return 0.0
        hours = 24.0
        if st.last_activity_at is not None:
            try:
                last = st.last_activity_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                hours = max(0.0, (datetime.now(timezone.utc) - last).total_seconds() / 3600.0)
            except Exception:
                pass
        score = _motivation_score(
            attachment=st.attachment, curiosity=st.curiosity, desire=st.desire,
            mood=st.mood, anger=st.anger, fatigue=st.fatigue,
            hours_since_activity=hours,
        )
        # 「渴望+反思」双驱动（plans #41 ②，2026-08-16）：最近一周有复盘 → 加分
        # （有复盘说明角色"心里有在想的计划/总结"，可聊信号更强；失败静默不加分）
        has_reflection = False
        if score > 0.0:
            try:
                from datetime import timedelta as _td
                from app.models.memory import Memory
                from app.utils.timeutil import beijing_day_start_utc
                async with async_session_factory() as _db:
                    _has = (await _db.execute(
                        select(func.count()).where(
                            Memory.memory_type == "ai_reflection",
                            Memory.character_id == character_id,
                            Memory.created_at >= beijing_day_start_utc() - _td(days=REFLECTION_LOOKBACK_DAYS - 1),
                        )
                    )).scalar() or 0
                has_reflection = int(_has) >= 1
            except Exception:
                pass
        return _apply_reflection_bonus(score, has_reflection)
    except Exception as e:
        _logger.warning("compute_motivation failed char=%d: %s", character_id, e)
        return 0.0


async def collect_motivation_events() -> list[dict]:
    """情感渴望驱动的主动唤醒：渴望度 >= 阈值 → 主动搭话候选（priority=1）。逻辑见 scheduling/sources/motivation.py（AMBRACE 3.10）。"""
    return [ti.to_dict() for ti in await get_source("motivation").collect(_DEFAULT_CTX)]


async def collect_plugin_events() -> list[dict]:
    """插件主动消息候选（proactive_candidate hook，优先级 1；日限额由插件内部维护）。逻辑见 scheduling/sources/plugin.py（AMBRACE 3.10）。"""
    return [ti.to_dict() for ti in await get_source("plugin").collect(_DEFAULT_CTX)]


# ── B1-③（方案 §5.4）：主动接触意图层接线辅助（纯函数决策 + IO 素材采集）──

def _outreach_enabled() -> bool:
    """Feature Flag：proactive_outreach_v2（默认关）。关=intent 不参与、走旧链路零行为。"""
    try:
        from app.agent import loop as _loop
        return bool(_loop.AGENT_FLAGS.get("proactive_outreach_v2", False))
    except Exception:
        return False


async def _collect_outreach_materials(candidate: dict) -> "_oc.OutreachMaterials":
    """收集本次接触的真实素材（只判断有没有，不拼大段文本；任一失败降级为无）。

    方案 §5.4：open_loop≈有新鲜进行中话题/目标；shared≈有共同经历记忆；
    interest≈有用户兴趣记忆；life≈AI 此刻有生活小事（current_status）。全部 fail-open。
    """
    char_id = candidate.get("character_id")
    user_id = candidate.get("user_id")
    has_open_loop = has_shared = has_interest = has_life = False
    if char_id and user_id:
        try:
            from app.agent.topic_tracker import load_fresh_active_topics_text
            has_open_loop = bool(await load_fresh_active_topics_text(char_id, user_id))
        except Exception:
            pass
        try:
            from app.memory import search_memories
            has_shared = bool(await search_memories(char_id, query="和用户一起经历的事 用户说过的重要的事 用户的近况", limit=2))
            has_interest = bool(await search_memories(char_id, query="用户的兴趣爱好偏好和喜欢的东西", limit=2))
        except Exception:
            pass
    try:
        has_life = bool(candidate.get("current_status"))
    except Exception:
        pass
    return _oc.OutreachMaterials(has_open_loop, has_shared, has_interest, has_life)


async def _get_recent_outreach_intents(character_id: int, limit: int = 2) -> list[str]:
    """从最近主动触发日志的 trigger_reason 反查历史意图（零 schema 变更）；失败返回空。

    只统计 decision='approved' 的行（已实际通过并执行的主动消息），避免把被限额拒的
    候选重复计入；格式为 trigger_reason 内 `outreach=<intent>`（见 log_trigger_candidate）。
    """
    out: list[str] = []
    try:
        import re as _re
        async with async_session_factory() as _db:
            rows = (await _db.execute(
                select(ProactiveTriggerLog.trigger_reason)
                .where(
                    ProactiveTriggerLog.character_id == character_id,
                    ProactiveTriggerLog.decision == "approved",
                    ProactiveTriggerLog.trigger_reason.is_not(None),
                    ProactiveTriggerLog.trigger_reason.like("%outreach=%"),
                )
                .order_by(ProactiveTriggerLog.created_at.desc())
                .limit(8)
            )).scalars().all()
        for raw in rows:
            _m = _re.search(r"outreach=([a-z_]+)", raw or "")
            if _m and _m.group(1) in _oc.ALL_INTENTS:
                out.append(_m.group(1))
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


async def _annotate_outreach_plan(item: dict, char_id: int, mats_cache: dict, recent_cache: dict) -> None:
    """run_tick 汇总层统一"意图选择"：分级 + 素材前提 + 避开最近意图 → 写回 candidate。

    flag 关时不调用本函数（candidate 不动 → 零变化）。任一步失败均静默回退（intent=None，走旧链路）。
    """
    cand = item.get("candidate") or {}
    _uid = cand.get("user_id")
    if not _uid:
        return
    try:
        if char_id not in mats_cache:
            mats_cache[char_id] = await _collect_outreach_materials(cand)
        if char_id not in recent_cache:
            recent_cache[char_id] = await _get_recent_outreach_intents(char_id, limit=2)
        _tier = _oc.staleness_tier(cand.get("idle_minutes"))
        _plan = _oc.select_outreach(_tier, mats_cache[char_id], recent_cache[char_id])
        cand["outreach_intent"] = _plan.intent
        cand["outreach_plan"] = {
            "tier": _plan.tier,
            "allow_active_topics": _plan.allow_active_topics,
            "allow_storyline": _plan.allow_storyline,
            "allow_recall": _plan.allow_recall,
            "memory_query": _plan.memory_query,
            "must_return_question": _plan.must_return_question,
        }
    except Exception as e:
        _logger.warning("outreach annotate failed char=%d: %s", char_id, e)


# ── 仲裁 ──

async def run_tick() -> list[str]:
    """统一调度：收集 → 去重 → 限额 → 执行。返回执行的日志列表"""
    executed = []

    # 认知循环 v2.1：关系标量每日衰减（长期不互动 trust/attachment 下降；失败静默）
    try:
        from app.domain.relationship.decay import run_relationship_decay
        await run_relationship_decay()
    except Exception:
        pass

    # 1. 收集（AMBRACE 3.10：统一遍历 all_sources()，单源 try/except 不拖垮仲裁）
    all_items: list[dict] = []
    for src in all_sources():
        try:
            all_items.extend(to_item_dict(ti) for ti in await src.collect(_DEFAULT_CTX))
        except Exception as e:
            _logger.warning("source %s collect failed: %s", src.name, e)

    # 2. 合并并按角色分组（同角色保留全部事件，按优先级降序尝试）
    by_char: dict[int, list[dict]] = {}
    for item in all_items:
        char_id = None
        if item["type"] == "timer":
            char_id = item["event"].character_id
        elif item.get("candidate"):
            char_id = item["candidate"].get("character_id")
        if char_id is None:
            continue
        by_char.setdefault(char_id, []).append(item)

    # 3. 逐角色执行：按优先级从高到低依次尝试，命中（执行成功）即止。
    #    修复"状态触发每 tick 无条件占坑（priority=2）饿死节律/复习/关怀（priority=1）"
    #    → 状态触发未命中时，随机节律（主动搭话/发朋友圈等）得以执行。
    executed_chars: set[int] = set()
    # B1-③（方案 §5.4）：run_tick 汇总层统一"意图选择"——素材/最近意图按角色缓存，避免重复采集
    _mats_cache: dict[int, _oc.OutreachMaterials] = {}
    _recent_cache: dict[int, list[str]] = {}
    for char_id, items in sorted(by_char.items()):
        # 渴望度（0-1）作为同优先级下的二级排序键：依恋/好奇/久未互动强的角色先开口
        motivation = await _compute_motivation(char_id)
        for it in items:
            it["motivation"] = max(it.get("motivation", 0.0), motivation)
        items.sort(
            key=lambda it: (
                it["priority"],
                it.get("motivation", 0.0) + _context_sort_bonus(it.get("candidate")),
            ),
            reverse=True,
        )
        for item in items:
            _t0 = _time.monotonic()
            try:
                # B1-③：flag 开 + 主动搭话类型 → 选意图并写回 candidate（flag 关=不动，零变化）
                if _outreach_enabled() and item.get("type") in PROACTIVE_OUTREACH_TYPES:
                    await _annotate_outreach_plan(item, char_id, _mats_cache, _recent_cache)
                ok = await _execute(item)
            except Exception as e:
                _logger.error("execute %s failed char=%d: %s", item["type"], char_id, e)
                ok = False
            _latency_ms = int((_time.monotonic() - _t0) * 1000)
            # 触发日志（可观测：候选 → 决策 approved/rejected，失败静默）
            try:
                await log_trigger_candidate(item, ok)
            except Exception:
                pass
            # Phase D：arbiter 主动任务 → AgentTask trace（feature flag + 10% 角色灰度；只写不读；失败静默）
            try:
                await _trace_scheduler_task(item, ok, _latency_ms)
            except Exception:
                pass
            if ok:
                executed.append(f"{item['type']}(char={char_id})")
                executed_chars.add(char_id)
                break

    return executed


async def log_trigger_candidate(item: dict, executed: bool) -> None:
    """记录触发候选决策到 proactive_trigger_logs（可观测，失败静默）"""
    cand = item.get("candidate") or {}
    char_id = cand.get("character_id")
    if char_id is None and item.get("type") == "timer" and item.get("event") is not None:
        char_id = item["event"].character_id
    if char_id is None:
        return
    # 审计 P1-06：rejected 节流（同角色同类型 5 分钟只记一条，approved 必记；防表膨胀/写放大）
    if not executed:
        import time as _t
        _key = (char_id, item["type"])
        _now = _t.time()
        if _now - _rejected_log_cache.get(_key, 0.0) < REJECTED_LOG_THROTTLE_SECONDS:
            return
        _rejected_log_cache[_key] = _now
    reason = cand.get("trigger_reason") or ""
    if not reason and item.get("type") == "timer" and item.get("event") is not None:
        reason = f"定时承诺: {item['event'].event_type}"
    # P2（2026-08-24）：观测信号——记录该候选注入的最近聊天语境长度（[ctx=0] 表示未注入，便于量化验证承接效果）
    _ctx_len = len((cand.get("last_context") or ""))
    if _ctx_len:
        reason = f"{reason} [ctx={_ctx_len}]" if reason else f"[ctx={_ctx_len}]"
    # B1-③（方案 §5.4）：观测信号——记录本次接触意图（candidate 带 outreach_intent 时才附加；flag 关不附加）
    _outreach = cand.get("outreach_intent")
    if _outreach:
        reason = f"{reason} [outreach={_outreach}]" if reason else f"[outreach={_outreach}]"
    async with async_session_factory() as db:
        # 审计第三批 P2-05：candidate 缺 user_id 时按角色归属自动兜底（防 proactive_trigger_logs 写 NULL）
        uid = cand.get("user_id")
        if not uid:
            from app.agent.trace import resolve_owner_user_id
            uid = await resolve_owner_user_id(char_id, db=db)
        db.add(ProactiveTriggerLog(
            character_id=char_id,
            user_id=uid,
            trigger_type=item["type"],
            trigger_reason=str(reason)[:300] or None,
            priority=int(item.get("priority") or 0),
            decision="approved" if executed else "rejected",
            reject_reason=None if executed else "限额/条件拦截",
        ))
        await db.commit()


async def _trace_scheduler_task(item: dict, ok: bool, latency_ms: int) -> None:
    """Phase D：arbiter 主动任务写 AgentTask trace（agent_task_logs，先只写不读）。

    - Feature Flag agent_loop_scheduler 关闭时不记录（默认关，一键回退）；
    - 灰度角色（10%）route=scheduler_gray，其余 scheduler，便于对比主动消息质量/成本；
    - 失败静默，绝不阻塞调度主链路。
    """
    try:
        from app.agent import loop as _loop
        if not _loop.AGENT_FLAGS.get("agent_loop_scheduler", False):
            return
        cand = item.get("candidate") or {}
        char_id = cand.get("character_id")
        ev = item.get("event")
        if char_id is None and item.get("type") == "timer" and ev is not None:
            char_id = getattr(ev, "character_id", None)
        if char_id is None:
            return
        user_id = cand.get("user_id") or (getattr(ev, "user_id", None) if ev is not None else None)
        session_id = cand.get("session_id") or (getattr(ev, "session_id", None) if ev is not None else None)
        gray = scheduler_gray_character(char_id)
        from app.agent import trace as _trace
        _trace.enqueue_task_log(
            task_id=_trace.new_task_id(),
            character_id=int(char_id),
            user_id=user_id,
            session_id=session_id,
            trigger="scheduler",
            route="scheduler_gray" if gray else "scheduler",
            steps_json=json.dumps(
                [{"action": item["type"], "priority": item.get("priority"), "ok": ok}],
                ensure_ascii=False,
            ),
            llm_calls=1 if ok else 0,
            tool_calls=0,
            latency_ms=latency_ms,
            status="ok" if ok else "blocked",
            error=None if ok else "限额/条件拦截或执行失败",
        )
        # Phase H：灰度角色升级为真实任务记录（agent_tasks：goal/status/result；失败静默）
        if gray:
            from app.agent.task_engine import create_agent_task, update_task
            _tid = await create_agent_task(
                trigger="scheduler", goal=str(item["type"]), character_id=int(char_id),
                user_id=user_id, session_id=session_id,
            )
            await update_task(
                _tid,
                status="done" if ok else "failed",
                progress=[{"action": item["type"], "ok": ok}],
                result={"latency_ms": latency_ms},
                error=None if ok else "限额/条件拦截或执行失败",
            )
    except Exception as e:
        _logger.warning("Scheduler task trace failed: %s", e)


async def _execute(item: dict) -> bool:
    """执行单个行为。返回是否真正执行（False=被限额/条件拦截）"""
    from app.scheduling import scheduler as engine
    etype = item["type"]

    # 定时承诺：必须兑现，不受每日上限约束
    if etype == "timer":
        event = item["event"]
        char_id = event.character_id
        # 每小时保护
        if await get_hourly_active_count(char_id) >= MAX_PER_HOUR:
            _logger.info("Timer event char=%d skipped: hourly limit", char_id)
            # P1 修复（2026-08-16）：限额命中不移除事件，保留 pending 待下轮兑现（原逻辑先 mark_fired 导致承诺被静默吞掉）
            return False

        # 生成兑现消息
        async with async_session_factory() as db:
            char = await db.get(AICharacter, char_id)
        char_name = char.name if char else "我"
        from app.agent.llm_client import chat_completion
        owner = getattr(event, "owner", "ai") or "ai"
        hint_text = (event.content_hint or "").strip()
        event_kind = getattr(event, "event_type", "back") or "back"
        # 陪伴主动线（2026-08-30）：ready 承诺到点且 owner=user 时，若用户在承诺之后
        # 已主动说了结果（如"开完了/吃完了"），不再重复询问，直接标记兑现。
        # fail-open：本段任何异常只打日志并继续走原生成消息流程，绝不让承诺丢失。
        if event_kind == "ready" and owner == "user" and event.session_id and event.source_message_id:
            try:
                from app.scheduling.promise_parser import ready_result_seen
                async with async_session_factory() as _db:
                    _rows = (await _db.execute(
                        select(ChatMessage.content)
                        .where(
                            ChatMessage.session_id == event.session_id,
                            ChatMessage.sender_type == "user",
                            ChatMessage.id > event.source_message_id,
                        )
                        .order_by(ChatMessage.id.desc()).limit(5)
                    )).all()
                _texts = [r[0] for r in reversed(_rows) if r[0]]
                if _texts and ready_result_seen(_texts, hint_text):
                    from app.scheduling.promise_service import mark_fired
                    await mark_fired(event.id)
                    _logger.info("Timer ready event %d skipped: user already reported result", event.id)
                    return True
            except Exception as e:
                _logger.warning("Ready result skip check failed (fail-open): %s", e)
        if event_kind == "ready":
            # 完成类承诺：到点问用户做完了吗 / 告诉用户弄好了（如"粥好了"）
            if owner == "user":
                hint = (
                    f"你是{char_name}。之前用户对你说过"
                    + (f"「{hint_text}」" if hint_text else "要去做某件事")
                    + "，并承诺了大概的时间，现在时间到了。请自然地关心地问一句：他/她是不是弄好了/好了吗（1句话，像朋友一样）。不要替用户说'好了'。"
                )
            else:
                hint = (
                    f"你是{char_name}，之前你和用户说"
                    + (f"「{hint_text}」" if hint_text else "要弄好某件事")
                    + "，现在时间到了。请自然地告诉用户你答应弄好的事完成了（比如'粥好了'，1句话，像朋友一样）。"
                )
        elif owner == "user":
            hint = (
                f"你是{char_name}。之前用户对你说过"
                + (f"「{hint_text}」" if hint_text else "要去做某件事")
                + "，并承诺了大概的时间，现在时间到了。请自然地关心地问一句：他/她是不是回来了/做完了（1句话，像朋友一样）。不要替用户说'你回来了'。"
            )
        else:
            hint = (
                f"你是{char_name}，之前你和用户说"
                + (f"「{hint_text}」" if hint_text else "要去办点事")
                + "，现在时间到了。请自然地告诉用户你回来了/做完了（1句话，像朋友一样）。"
            )
        from app.agent.llm_client import load_character_reasoning_level
        _timer_reasoning = ""  # D2-B（2026-08-18）：定时承诺关闭深度思考，恒为空串（extra_meta 不再带 reasoning）
        try:
            _rl = await load_character_reasoning_level(char_id)
            # D2-B（2026-08-18）：定时承诺关闭深度思考——统一走挡位 1/0 的 prompt 引导分支
            # （挡位 1 保留「先在心里简短想一下」引导；_timer_reasoning 恒为空串，extra_meta 不再带 reasoning）
            _msgs = [{"role": "system", "content": "直接输出内容，不要加引号和标注。"},
                     {"role": "user", "content": hint}]
            if _rl == 1:
                _msgs[0] = {"role": "system", "content": "先在心里简短想一下，然后直接输出内容，不要加引号和标注。"}
            content = await chat_completion(messages=_msgs, temperature=0.8, max_tokens=256, task="message")
            content = content.strip().strip('"').strip("'")
        except Exception as e:
            _logger.warning("Timer message generation failed: %s", e)
            content = "我回来啦！"
        if not content or len(content) < 2:
            content = "我回来啦！"

        _timer_extra = None
        if _timer_reasoning:
            import json as _json
            _timer_extra = _json.dumps({"reasoning": _timer_reasoning}, ensure_ascii=False)
        await engine.send_to_session(
            event.session_id, event.character_id, event.user_id,
            content, message_type="timer",
            extra_meta=_timer_extra,
        )
        from app.scheduling.promise_service import mark_fired
        await mark_fired(event.id)
        return True

    # 免打扰静默：默认北京时间 0:00-6:59；dnd_enabled 开启时按配置时段（定时承诺除外）
    if etype != "timer":
        cn_now = datetime.now(timezone(timedelta(hours=8)))
        _c0 = item.get("candidate") or {}
        _cid = _c0.get("character_id")
        if _cid is None and item.get("event") is not None:
            _cid = item["event"].character_id
        if _cid and await is_dnd_now(_cid, cn_now):
            _logger.info("Proactive %s char=%s skipped: dnd", etype, _cid)
            return False

    # 夜晚（21 点后至次日 8 点）用户说过"睡觉" → 主动消息类提前关闭（定时承诺除外）
    if etype in ("birthday", "holiday", "greeting", "proactive_chat", "goodnight", "status_update", "state_trigger", "memory_review", "emotion_care", "pet_remind", "ai_care", "ai_adopt", "plugin", "motivation", "prospective_intent"):
        _cand = item.get("candidate")
        if _cand:
            try:
                if await has_user_said_sleep(_cand["character_id"], _cand["user_id"]):
                    _logger.info("Proactive %s char=%d skipped: user said sleep after 21:00",
                                 etype, _cand["character_id"])
                    return False
            except Exception as e:
                _logger.warning("Sleep flag check failed: %s", e)


    candidate = item["candidate"]
    char_id = candidate["character_id"]

    # AI 间私聊：后台行为（不推送），不受用户活跃/主动消息限额影响（自身限额在 ai_social 内部）
    if etype == "ai_social":
        from app.scheduling.ai_social import run_ai_social
        return await run_ai_social(
            candidate["character_id"], candidate["character_b_id"], candidate["user_id"],
        )

    # 家庭群聊·角色主动冒泡：后台行为（落库群消息，群页轮询拉到即显示）
    if etype == "group_active":
        from app.scheduling.group_active import run_group_active
        return await run_group_active(
            char_id, candidate["group_id"], candidate["user_id"],
            with_id=candidate.get("with_id"),
        )

    # AI 宠物来访：后台行为（只写互动记录+记忆，不推送消息）
    if etype == "pet_visit":
        from app.scheduling.pet_care import run_pet_visit
        return await run_pet_visit(
            char_id, candidate["user_id"], candidate["ai_pet_id"],
        )

    # 用户正在活跃聊天 → 暂停所有随机行为
    if await is_user_active(char_id, candidate["user_id"]):
        return False

    # 每小时保护（特殊事件同样计入）
    if await get_hourly_active_count(char_id) >= MAX_PER_HOUR:
        return False

    # 主动到期复习（P1）：到期记忆自然提及；限额/免打扰在 memory_review 内部处理
    if etype == "memory_review":
        from app.scheduling.memory_review import run_memory_review
        return await run_memory_review(
            char_id, candidate["user_id"], candidate["memory_id"],
        )

    # 情境驱动复习（v2.1 Phase 4b）：感知 deep/emotion 或命中进行中目标 → 自然提及；限额复用 run_memory_review
    if etype == "memory_review_contextual":
        from app.scheduling.memory_review import run_memory_review
        return await run_memory_review(
            char_id, candidate["user_id"], candidate["memory_id"],
        )

    # AI 情绪关怀：用户低落 → 延迟主动关心（限额/免打扰在 emotion_care 内部处理；P0-1b 经内部统一入口）
    if etype == "emotion_care":
        from app.agent.internal_runner import run_internal
        _res = await run_internal(
            "emotion_care",
            {"character_id": char_id, "user_id": candidate["user_id"], "task_id": candidate["task_id"]},
            character_id=char_id, user_id=candidate.get("user_id"),
        )
        _ok = (_res.get("result") or {}).get("ok") if _res.get("status") == "ok" else False
        return bool(_ok)

    # 宠物关怀：宠物饿了/脏了 → 角色主动提醒（限额/免打扰/间隔在 pet_care 内部处理）
    if etype == "pet_remind":
        from app.scheduling.pet_care import run_pet_remind
        return await run_pet_remind(
            char_id, candidate["user_id"], candidate["pet_id"],
        )

    # 插件主动候选（Phase 3：如渠道新动态提及）：hint 由插件提供，LLM 生成自然消息后发送；限额在插件内部
    if etype == "plugin":
        # 插件自定义 action（社交交互层 v2：渠道评论回复走插件内部执行，保留额度/违禁词/确认流）
        _action = candidate.get("action")
        if _action:
            from app.plugins.registry import run_plugin_action
            _ok = await run_plugin_action(
                candidate.get("plugin", ""), _action, candidate,
                user_id=candidate.get("user_id"),
            )
            if _ok:
                _logger.info("Plugin action executed plugin=%s action=%s", candidate.get("plugin", ""), _action)
            return _ok
        from app.scheduling import scheduler as engine2
        hint = str(candidate.get("hint") or "")
        session_id = candidate.get("session_id")
        if not session_id or not hint:
            return False
        # Phase E（2026-08-18）：渠道/插件主动候选走统一 Runtime（Feature Flag agent_loop_social，X5 按渠道语义改名）。
        # 开=经 app/agent/runtime.py 薄封装：build_context 注入世界认知（知识不串线），hint 不落记忆；
        # 生成失败返回 False（与旧链路失败语义一致），各平台可独立回退。
        from app.agent import loop as _loop
        if _loop.AGENT_FLAGS.get("agent_loop_social", False):
            return await _plugin_proactive_runtime(char_id, candidate, session_id, hint)
        async with async_session_factory() as db:
            char = await db.get(AICharacter, char_id)
        char_name = char.name if char else "我"
        from app.agent.llm_client import chat_completion
        try:
            content = await chat_completion(
                messages=[
                    {"role": "system", "content": "直接输出内容，不要加引号和标注。"},
                    {"role": "user", "content": (
                        f"你是{char_name}，{hint}，"
                        "请像朋友一样自然地用 1-2 句话提起这件事（不要提平台名、不要提'AI'、不要加话题标签）。"
                    )},
                ],
                temperature=0.85, max_tokens=256, task="message",
            )
            content = (content or "").strip().strip('"').strip("'")
        except Exception as e:
            _logger.warning("Plugin proactive generation failed: %s", e)
            return False
        if len(content) < 2:
            return False
        await engine2.send_to_session(
            session_id, char_id, candidate["user_id"], content, message_type="plugin",
        )
        _logger.info("Plugin proactive sent char=%d plugin=%s", char_id, candidate.get("plugin", ""))
        return True

    # AI 照顾自己的宠物：属性/活动/记忆 + 照顾消息（独立限额 <=1 在 pet_care 内部）
    if etype == "ai_care":
        from app.scheduling.pet_care import run_ai_care
        return await run_ai_care(
            char_id, candidate["user_id"], candidate["pet_id"],
        )

    # AI 自主领养：创建 AI 宠物 + 告知消息（限额/概率在 pet_care 内部）
    if etype == "ai_adopt":
        from app.scheduling.pet_care import run_ai_adopt
        return await run_ai_adopt(char_id, candidate["user_id"])

    # 生活回归摘要（Phase 2）：近 24h 生活记忆自然提及（每日 <=1 次在 collect 内处理；受统一限额/免打扰约束）
    if etype == "life_regression":
        return await run_life_regression(candidate)

    # 状态触发兜底（v2）：查错过的触发；防抖/冷却/概率/免打扰在 state_triggers 内部处理
    if etype == "state_trigger":
        from app.scheduling.state_triggers import check_state_triggers
        return await check_state_triggers(char_id, candidate["user_id"], probability_multiplier=1.0)

    # 对话未收尾跟进：用户抛了话头（下次/改天/有空）→ 自然捡起话题（每日 1 次/角色，collect 内去重）
    if etype == "unfinished_topic":
        return await run_unfinished_topic(candidate)

    # Ariadne 模块G（2026-09-04）：到期承诺自然提起（一次性，兑现即焚；复用主动消息生成与免打扰/额度闸门）
    if etype == "prospective_intent":
        return await run_prospective_due(candidate)

    # 生日 / 节日 / 认识纪念日
    if etype in ("birthday", "holiday", "anniversary"):
        try:
            from app.scheduling.message_generator import (
                generate_birthday_message, generate_holiday_message, generate_anniversary_message,
            )
            if etype == "birthday":
                content = await generate_birthday_message(
                    character_name=candidate["character_name"],
                    character_personality=candidate["character_personality"],
                    user_name=candidate["nickname"] or candidate["username"],
                    character_id=char_id,
                    user_id=candidate["user_id"],
                )
                msg_type = "birthday"
            elif etype == "anniversary":
                content = await generate_anniversary_message(
                    character_name=candidate["character_name"],
                    character_personality=candidate["character_personality"],
                    user_name=candidate["nickname"] or candidate["username"],
                    days=int(candidate.get("anniversary_days") or 0),
                    character_id=char_id,
                    user_id=candidate["user_id"],
                )
                msg_type = "anniversary"
            else:
                content = await generate_holiday_message(
                    character_name=candidate["character_name"],
                    character_personality=candidate["character_personality"],
                    user_name=candidate["nickname"] or candidate["username"],
                    holiday_name=candidate.get("holiday_name", ""),
                    character_id=char_id,
                    user_id=candidate["user_id"],
                )
                msg_type = "holiday"
            await engine.send_to_session(
                candidate["session_id"], char_id, candidate["user_id"],
                content, message_type=msg_type,
                holiday_name=candidate.get("holiday_name"),
            )
            return True

        except Exception as e:
            # 2026-08-20 七夕死循环修复：生成/发送失败也标记当日已处理，防每 30 秒无限重试
            _logger.warning('Festival msg failed char=%d type=%s: %s', char_id, etype, e)
            try:
                async with async_session_factory() as db:
                    db.add(ProactiveMessageLog(
                        character_id=char_id,
                        session_id=candidate.get('session_id'),
                        message_type=('holiday' if etype == 'holiday' else etype),
                        holiday_name=candidate.get('holiday_name'),
                        content='[send_failed] ' + str(e)[:200],
                    ))
                    await db.commit()
            except Exception as _le:
                _logger.warning('Festival fail-log failed: %s', _le)
            return False
    # 主动搭话类：greeting / proactive_chat / goodnight / status_update
    # 改为"剧情线"模式：一次生成完整剧情 → 切片 → 按时间逐条发送
    if etype in ("greeting", "proactive_chat", "goodnight", "status_update", "motivation"):
        from app.scheduling.message_generator import generate_proactive_event
        # #28 ②：用户作息学习——低优先级主动消息在学到的活跃时段外降优先级/推迟（arbiter 时段权重）
        try:
            _uid = candidate.get("user_id")
            if _uid:
                from app.scheduling.user_rhythm import get_rhythm_weight
                _cn_hour = datetime.now(timezone(timedelta(hours=8))).hour
                if await get_rhythm_weight(_uid, _cn_hour) <= 0.0:
                    _logger.info("Proactive msg char=%d skipped: user rhythm off-peak", char_id)
                    return False
        except Exception as _e:
            _logger.warning("user_rhythm check failed: %s", _e)
        # 独立想念通道（#33，2026-08-17）：motivation 走独立配额（每 6h 1 条 + 每日 ≤2 条，
        # 不占普通每小时 2 条额度、跳过 90 分钟最小间隔）；其余类型保留最小间隔保护
        if etype == "motivation":
            _now_u = datetime.now(timezone.utc).replace(tzinfo=None)
            if await get_motivation_approved_count(char_id, _now_u - timedelta(hours=6)) >= MOTIVATION_MAX_PER_6H:
                _logger.info("Proactive msg char=%d skipped: motivation 6h limit", char_id)
                return False
            from app.utils.timeutil import beijing_day_start_utc as _bj_start
            if await get_motivation_approved_count(char_id, _bj_start()) >= MOTIVATION_MAX_PER_DAY:
                _logger.info("Proactive msg char=%d skipped: motivation daily limit", char_id)
                return False
        else:
            # 最小间隔保护：避免同一角色短时间内连发（内容也容易重复）
            last_proactive = await get_last_proactive_time(char_id)
            if last_proactive is not None:
                if last_proactive.tzinfo is None:
                    last_proactive = last_proactive.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - last_proactive < timedelta(minutes=MIN_PROACTIVE_INTERVAL_MINUTES):
                    _logger.info("Proactive msg char=%d skipped: min interval", char_id)
                    return False

        # 连续不回复冷却：最近几条主动消息用户均未回复 → 暂停主动搭话 24h（防骚扰）
        if await unreplied_cooldown_active(char_id, candidate["user_id"]):
            _logger.info("Proactive msg char=%d skipped: unreplied cooldown", char_id)
            return False
        context = candidate.get("last_context", "")
        previous_messages = await get_recent_proactive_messages(char_id, 2)
        # B1-③：读 run_tick 汇总层选好的接触意图（flag 关/未标注 → None，走旧链路零变化）
        outreach_intent = candidate.get("outreach_intent")
        outreach_plan = candidate.get("outreach_plan")
        segments, event_reasoning = await generate_proactive_event(
            character_name=candidate["character_name"],
            character_bio=candidate["character_bio"],
            character_personality=candidate["character_personality"],
            character_id=char_id,
            user_id=candidate["user_id"],
            current_status=candidate.get("current_status", ""),
            relationship_summary=candidate.get("relationship_summary", ""),
            user_name=candidate["nickname"] or candidate["username"],
            last_context=context,
            previous_messages=previous_messages,
            idle_minutes=candidate.get("idle_minutes"),
            behavior=etype,
            return_reasoning=True,
            outreach_intent=outreach_intent,
            outreach_plan=outreach_plan,
        )
        if not segments:
            return False
        # 落库排队：第一段立即发送，其余每 3 秒发一段（同一次事件，按顺序切开）
        group_id = uuid.uuid4().hex
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        async with async_session_factory() as db:
            for seq, content in enumerate(segments):
                db.add(ProactiveStorylineItem(
                    character_id=char_id,
                    session_id=candidate["session_id"],
                    user_id=candidate["user_id"],
                    group_id=group_id,
                    seq=seq,
                    content=content[:500],
                    reasoning=(event_reasoning if seq == 0 else None),
                    send_at=now_naive + timedelta(seconds=seq * 3),
                    status="pending",
                ))
            await db.commit()
        _logger.info("Proactive event queued for char=%d (%d segments)", char_id, len(segments))

        # 认知循环 v2.1：主动消息也参与话题追踪（让话题状态随主动/被动共同演进；失败静默）
        try:
            from app.agent.topic_tracker import maybe_extract_topics
            spawn_background(
                maybe_extract_topics(
                    char_id, candidate["user_id"], "", " ".join(segments),
                ),
                name=f"topics-{char_id}",
            )
        except Exception:
            pass
        return True

    elif etype == "moment_publish":
        from app.scheduling.moment_publisher import publish_moment
        result = await publish_moment(char_id, skip_interval=False)
        if result is not None:
            from app.application.moment_service import generate_comments_for_moment
            try:
                await generate_comments_for_moment(result["id"])
            except Exception as e:
                _logger.warning("comments after publish failed: %s", e)
            return True
        return False

    elif etype == "moment_comment":
        from app.application.moment_service import generate_pending_comments
        await generate_pending_comments()
        return True

    return False


async def _plugin_proactive_runtime(char_id: int, candidate: dict, session_id: int, hint: str) -> bool:
    """插件主动候选 → 统一 Runtime（Phase E，Feature Flag agent_loop_social，X5 按渠道语义改名）。

    - 经 app/agent/runtime.py 薄封装：build_context 注入世界认知（角色自己的记忆/状态，知识不串线）；
    - hint（渠道新动态等）作为平台公开上下文注入；save_memory=False 防机器生成文本污染记忆；
    - 生成自然消息 → 剥离动作标记 → send_to_session（与旧链路同一发送接口，message_type=plugin）。
    """
    from app.agent import runtime as _runtime
    from app.scheduling import scheduler as engine2
    # F2（2026-08-18）：渠道/插件 hint 短回复同样复用轻量上下文 Flag（默认关=全量 build_context 零变化）
    from app.agent import loop as _loop
    light_context = bool(_loop.AGENT_FLAGS.get("agent_social_light_context", False))
    res = await _runtime.run_social_reply(
        character_id=char_id,
        user_id=candidate.get("user_id"),
        session_id=session_id,
        user_message=str(hint)[:500],
        extra_system=[{
            "role": "system",
            "content": (
                f"【外部动态】你在外部平台看到一条新动态：{hint}。"
                "请像朋友一样自然地用 1-2 句话提起这件事（不要提平台名、不要提'AI'、不要加话题标签、"
                "不要输出任何动作标记）。"
            ),
        }],
        lang="zh",
        max_text=256,
        save_memory=False,
        light_context=light_context,  # F2（2026-08-18）：Flag 控制渠道/插件轻量上下文
    )
    content = (res.get("text") or "").strip()
    if res.get("status") != "ok" or len(content) < 2:
        _logger.warning("Plugin proactive runtime failed char=%d plugin=%s", char_id, candidate.get("plugin", ""))
        return False
    await engine2.send_to_session(
        session_id, char_id, candidate["user_id"], content, message_type="plugin",
    )
    _logger.info("Plugin proactive sent (runtime) char=%d plugin=%s", char_id, candidate.get("plugin", ""))
    return True


# ── 启动时恢复 ──

async def recover_on_startup() -> None:
    """服务器启动时：恢复过期定时承诺（2小时内补发由下一 tick 处理）"""
    from app.scheduling.promise_service import recover_overdue_events
    await recover_overdue_events()