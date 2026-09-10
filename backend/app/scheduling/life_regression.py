"""AI 生活回归摘要 — arbiter 事件源（Life Engine v2 Phase 2，2026-08-12）

- 用户上线后检索近 24h source=life 且 importance 达标的记忆（<=2 条）→ LLM 自然表达
- 每角色每日 <=1 次；受主动消息频率/免打扰约束（arbiter 统一处理）
- 用户情绪低落/紧急求助时不触发（复用 emotion 规则器）
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.models.character import ProactiveMessageLog
from app.scheduling.triggers import get_active_characters
from app.domain.emotion.model import detect_user_emotion
from app.utils.logger import get_logger

_logger = get_logger("scheduler.life_regression")

# F1（2026-09-08，Sam 主动消息错接昨晚剧情 P0）：主动生成时间锚点——
# 生活回归不再把几天前的素材（如 9 月 3 日事件）说成"昨天/刚刚"。
_CN_WEEKDAYS = "一二三四五六日"


def _cn_noon_label(hour: int) -> str:
    """小时 → 中文午别（凌晨/早上/上午/中午/下午/晚上/深夜）。"""
    if hour < 5:
        return "凌晨"
    if hour < 9:
        return "早上"
    if hour < 11:
        return "上午"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 23:
        return "晚上"
    return "深夜"


def _cn_now_prefix(now: datetime | None = None) -> str:
    """F1：当前时间锚点（应用本地=北京时间），如「现在是北京时间 2026年9月8日 星期二 中午 12:05。」"""
    if now is None:
        from app.utils.timeutil import app_local_now
        now = app_local_now()
    return (f"现在是北京时间 {now.year}年{now.month}月{now.day}日 "
            f"星期{_CN_WEEKDAYS[now.weekday()]} {_cn_noon_label(now.hour)} "
            f"{now.hour:02d}:{now.minute:02d}。")


# ── L4（2026-09-09 主体归属治理）：一次性生活动作不主动复读 ──
# 吃饭/做饭这类动作结束即失效（「桌上粥还温着」过了饭点再提就是复读），给出有效窗口（小时）。
_ONE_OFF_WINDOW_HOURS = {"meal": 6}


def _life_no_replay_on() -> bool:
    """L4 flag（与 memory_review 共用）：life_event_no_replay（默认关；关=维持现选片）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("life_event_no_replay", False))
    except Exception:
        return False


def _one_off_skip(mem, user_texts: list[str], now) -> bool:
    """L4 纯函数：该生活记忆是否不该再主动复述（用户已闭环该主题 / 已超过动作有效窗口）。"""
    try:
        from app.scheduling.proactive_topic_guard import topic_bucket, topic_closed_by_user
    except Exception:
        return False
    bucket = topic_bucket(mem.content or "")
    if not bucket:
        return False
    if topic_closed_by_user(user_texts, bucket):
        return True
    win = _ONE_OFF_WINDOW_HOURS.get(bucket)
    if win and getattr(mem, "created_at", None) is not None:
        try:
            age_hours = (now - mem.created_at).total_seconds() / 3600
            if age_hours > win:
                return True
        except Exception:
            return False
    return False


async def _recent_user_texts(user_id: int, character_id: int, limit: int = 8) -> list[str]:
    """用户最近若干条消息（该角色最新会话，只用于判主题闭环；异常返回空，fail-open）。"""
    from app.models.chat import ChatMessage
    try:
        from app.application.chat_service import get_latest_session_id
        async with async_session_factory() as db:
            session_id = await get_latest_session_id(user_id, character_id)
            if not session_id:
                return []
            rows = (await db.execute(
                select(ChatMessage.content)
                .where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.sender_type == "user",
                )
                .order_by(ChatMessage.id.desc())
                .limit(limit)
            )).all()
        return [r[0] or "" for r in rows]
    except Exception as e:
        _logger.warning("life regression user texts failed: %s", e)
        return []


EVENT_TYPE = "life_regression"
MAX_PER_DAY = 1            # 每角色每日最多 1 次
LOOKBACK_HOURS = 24        # 检索近 24h 的生活记忆
MIN_IMPORTANCE = 3         # importance 达标线（life 记忆 1-4 档）
CHECK_USER_MINUTES = 60    # 用户最近 1 小时消息用于情绪检查


async def _used_today(character_id: int) -> bool:
    """今天（北京时间）该角色是否已发过回归摘要"""
    now_cn = datetime.now(timezone(timedelta(hours=8)))
    start_cn = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_cn.astimezone(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        n = (
            await db.execute(
                select(func.count()).where(
                    ProactiveMessageLog.character_id == character_id,
                    ProactiveMessageLog.message_type == EVENT_TYPE,
                    ProactiveMessageLog.created_at >= start_utc,
                )
            )
        ).scalar() or 0
    return n >= MAX_PER_DAY


async def _user_recent_emotion(user_id: int, character_id: int) -> str:
    """用户最近消息情绪（取最近一条用户消息）"""
    from app.models.chat import ChatMessage
    from app.application.chat_service import get_latest_session_id
    async with async_session_factory() as db:
        session_id = await get_latest_session_id(user_id, character_id)
        if not session_id:
            return ""
        since = datetime.now(timezone.utc) - timedelta(minutes=CHECK_USER_MINUTES)
        row = (
            await db.execute(
                select(ChatMessage.content)
                .where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.sender_type == "user",
                    ChatMessage.created_at >= since,
                )
                .order_by(ChatMessage.created_at.desc())
                .limit(1)
            )
        ).first()
    if not row or not row[0]:
        return ""
    return detect_user_emotion(row[0])


async def collect_life_regression_events() -> list[dict]:
    """arbiter 事件源：近 24h 达标生活记忆 → 回归摘要候选（priority=2）"""
    events = []
    chars = await get_active_characters()
    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    for c in chars:
        try:
            char_id = c["character_id"]
            user_id = c["user_id"]
            if await _used_today(char_id):
                continue
            # 情绪低落/紧急求助时不触发
            emo = await _user_recent_emotion(user_id, char_id)
            if "低落" in emo or "崩溃" in emo or "焦虑" in emo:
                continue
            async with async_session_factory() as db:
                lives = (
                    await db.execute(
                        select(Memory)
                        .where(
                            Memory.user_id == user_id,
                            Memory.character_id == char_id,
                            Memory.source == "life",
                            Memory.importance >= MIN_IMPORTANCE,
                            Memory.delete_at.is_(None),
                            Memory.created_at >= since,
                        )
                        .order_by(Memory.importance.desc(), Memory.created_at.desc())
                        .limit(6)
                    )
                ).scalars().all()
            if not lives:
                continue
            # Disclosure 精细（Phase 3）：命中角色高兴趣关键词的记忆优先（<=2 条）
            from app.models.life import LifeInterest
            async with async_session_factory() as db:
                its = (
                    await db.execute(
                        select(LifeInterest).where(
                            LifeInterest.character_id == char_id,
                            LifeInterest.level >= 40,
                        )
                    )
                ).scalars().all()
            _hot_kw = [it.name for it in its]
            def _hit(m) -> bool:
                return any(kw in (m.content or "") for kw in _hot_kw) if _hot_kw else False
            lives = sorted(lives, key=lambda m: (int(_hit(m)), float(m.importance or 0)), reverse=True)[:2]
            # L4：跳过用户已闭环主题与已过动作有效窗口的一次性生活动作（每日 ≤1 限额不变）
            if _life_no_replay_on():
                _utexts = await _recent_user_texts(user_id, char_id)
                _now = datetime.now(timezone.utc).replace(tzinfo=None)
                lives = [m for m in lives if not _one_off_skip(m, _utexts, _now)]
            if not lives:
                continue
            items = [
                {"id": m.id, "content": (m.content or "")[:200], "sub_type": m.sub_type or "life_event"}
                for m in lives
            ]
            events.append({
                "type": EVENT_TYPE,
                "priority": 2,
                "candidate": {
                    "character_id": char_id,
                    "user_id": user_id,
                    "character_name": c["character_name"],
                    "character_personality": c["character_personality"],
                    "nickname": c["nickname"] or c["username"],
                    "life_items": items,
                    "session_id": None,  # 由 run 时取最新会话
                },
            })
        except Exception as e:
            _logger.warning("life regression collect failed char=%s: %s", c.get("character_id"), e)
    return events


async def run_life_regression(candidate: dict) -> bool:
    """生成自然回归表达并发送；失败静默"""
    char_id = candidate["character_id"]
    user_id = candidate["user_id"]
    items = candidate.get("life_items") or []
    if not items:
        return False
    try:
        from app.agent.llm_client import chat_completion
        from app.models.character import AICharacter
        from app.scheduling import scheduler as engine
        from app.scheduling.message_generator import _validate_segments
        from app.scheduling.triggers import get_latest_session

        async with async_session_factory() as db:
            char = await db.get(AICharacter, char_id)
        char_name = char.name if char else "我"
        session = await get_latest_session(char_id, user_id)
        if not session:
            return False
        identity = ""
        if char:
            try:
                from app.agent.user_profile import build_role_prompt_block
                identity = await build_role_prompt_block(char, user_id) + "\n"
            except Exception:
                identity = f"你是{char_name}，性格{char.personality or '友善'}。\n"
        lines = "\n".join(f"- {it['content']}" for it in items)
        hint = (
            f"{_cn_now_prefix()}\n"
            f"{identity}"
            f"你是{char_name}，最近你的生活里发生了这些事：\n{lines}\n"
            "现在你和用户聊天，请像朋友一样自然地提起其中 1 件（1-2 句话，像随口分享自己的近况，"
            "按你的性格和聊天风格来说，不要说'我最近'太刻意，不要提'AI'，不要加引号标注）。"
            "只从上面列出的事里选，不要编造没发生过的生活经历。"
        )
        msg = await chat_completion(
            messages=[
                {"role": "system", "content": "直接输出内容，不要加引号和标注。"},
                {"role": "user", "content": hint},
            ],
            temperature=0.9,
            max_tokens=256,
            task="life_regression",
            user_id=user_id,
        )
        msg = (msg or "").strip().strip('"').strip("'")
        ok, cleaned = _validate_segments([msg])
        if not ok or not cleaned or len(cleaned[0]) < 2:
            return False
        await engine.send_to_session(
            session["id"], char_id, user_id, cleaned[0], message_type=EVENT_TYPE,
        )
        _logger.info("Life regression sent char=%d items=%d", char_id, len(items))
        return True
    except Exception as e:
        _logger.warning("life regression run failed char=%s: %s", char_id, e)
        return False
