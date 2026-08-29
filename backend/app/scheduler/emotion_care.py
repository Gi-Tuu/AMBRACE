"""AI 情绪关怀（2026-08-05）：用户低落 → 角色延迟主动关心。

数据流：
- register_care_task：聊天检测到低落情绪时登记任务（due_at = now + 15~45 分钟随机延迟）
- collect_care_events：arbiter tick 扫描到期 pending 任务（每角色 1 条候选，priority=1）
- run_emotion_care：限额/免打扰/会话检查 → LLM 生成关怀消息 → send_to_session 发送 → 任务置 done
- 护栏：每角色每日 <=2 条、同角色最小间隔 3h、免打扰不发、无活跃会话取消、超 24h 自动作废
"""
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.emotion_care_task import EmotionCareTask
from app.models.proactive_settings import ProactiveMessageLog
from app.utils.logger import get_logger
from app.utils.dnd import user_in_dnd_period as _user_in_dnd_period

_logger = get_logger("scheduler.emotion_care")

CARE_TYPE = "emotion_care"
MAX_PER_DAY = 2
MIN_INTERVAL_HOURS = 3
DELAY_MIN_MINUTES = 15
DELAY_MAX_MINUTES = 45
TASK_TTL_HOURS = 24


async def register_care_task(user_id: int, character_id: int, trigger_msg: str) -> bool:
    """用户发低落消息时登记一条延迟主动关怀任务（同角色已有 pending 任务则跳过）。"""
    from app.scheduler.triggers import proactive_enabled
    if not await proactive_enabled(character_id):
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        existing = (await db.execute(
            select(EmotionCareTask.id).where(
                EmotionCareTask.character_id == character_id,
                EmotionCareTask.user_id == user_id,
                EmotionCareTask.status == "pending",
            ).limit(1)
        )).first()
        if existing:
            return False
        due = now + timedelta(minutes=random.randint(DELAY_MIN_MINUTES, DELAY_MAX_MINUTES))
        db.add(EmotionCareTask(
            user_id=user_id, character_id=character_id,
            trigger_msg=(trigger_msg or "")[:200],
            due_at=due, status="pending",
        ))
        await db.commit()
    _logger.info("Emotion care task registered char=%d", character_id)
    return True


async def collect_care_events() -> list[dict]:
    """arbiter 事件源：到期且未过期的 pending 任务 → 每角色 1 条候选（priority=1）。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_before = now - timedelta(hours=TASK_TTL_HOURS)
    async with async_session_factory() as db:
        # 先作废超 24h 未发送的任务，避免无限重试
        stale = await db.execute(
            select(EmotionCareTask).where(
                EmotionCareTask.status == "pending",
                EmotionCareTask.due_at < stale_before,
            )
        )
        for t in stale.scalars().all():
            t.status = "cancelled"
            t.finished_at = now
        await db.commit()
        rows = (await db.execute(
            select(EmotionCareTask)
            .where(
                EmotionCareTask.status == "pending",
                EmotionCareTask.due_at <= now,
                EmotionCareTask.due_at >= stale_before,
            )
            .order_by(EmotionCareTask.due_at.asc())
        )).scalars().all()
    per_char: dict[int, EmotionCareTask] = {}
    for t in rows:
        if t.character_id not in per_char:
            per_char[t.character_id] = t
    return [
        {"type": CARE_TYPE, "priority": 1, "candidate": {
            "character_id": t.character_id, "user_id": t.user_id, "task_id": t.id,
        }}
        for t in per_char.values()
    ]


async def _daily_count(db, character_id: int) -> int:
    cn_tz = timezone(timedelta(hours=8))
    today_start = datetime.now(cn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start.astimezone(timezone.utc).replace(tzinfo=None)
    return (await db.execute(
        select(func.count(ProactiveMessageLog.id)).where(
            ProactiveMessageLog.character_id == character_id,
            ProactiveMessageLog.message_type == CARE_TYPE,
            ProactiveMessageLog.created_at >= today_start,
        )
    )).scalar() or 0


async def _last_care_at(db, character_id: int):
    return (await db.execute(
        select(func.max(ProactiveMessageLog.created_at)).where(
            ProactiveMessageLog.character_id == character_id,
            ProactiveMessageLog.message_type == CARE_TYPE,
        )
    )).scalar_one_or_none()


async def _finish_task(task_id: int, status: str) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        task = await db.get(EmotionCareTask, task_id)
        if task:
            task.status = status
            task.finished_at = now
            await db.commit()


async def run_emotion_care(char_id: int, user_id: int, task_id: int) -> bool:
    """执行一次关怀：限额/免打扰/会话检查 → LLM 生成 → 发送 → 任务置 done。"""
    from app.scheduler.scheduler import send_to_session
    from app.services.chat_service import get_latest_session_id

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        if await _user_in_dnd_period(db, user_id):
            return False
        if await _daily_count(db, char_id) >= MAX_PER_DAY:
            _logger.info("Emotion care char=%d skipped: daily limit", char_id)
            await _finish_task(task_id, "cancelled")
            return False
        last = await _last_care_at(db, char_id)
        if last is not None:
            last = last.replace(tzinfo=None) if last.tzinfo else last
            if now_naive - last < timedelta(hours=MIN_INTERVAL_HOURS):
                return False
        task = await db.get(EmotionCareTask, task_id)
        if task is None or task.status != "pending":
            return False
        char = await db.get(AICharacter, char_id)
        trigger_msg = task.trigger_msg
    session_id = await get_latest_session_id(user_id, char_id)
    if session_id is None:
        await _finish_task(task_id, "cancelled")
        return False

    char_name = char.name if char else "我"
    personality = (char.personality or "友善")[:100] if char else "友善"
    try:
        from app.agent.llm_client import chat_completion
        from app.agent.user_profile import build_role_prompt_block
        identity = ""
        try:
            identity = await build_role_prompt_block(char, user_id)
        except Exception:
            identity = f"你是{char_name}，性格{personality}。"
        # 认知循环 v2.1：主动通道 persona 统一层（关系温度/剧情状态/进行中话题；开关关=空串）
        active_persona = ""
        try:
            from app.agent.persona import build_active_channel_persona
            active_persona = await build_active_channel_persona(char_id, user_id)
        except Exception:
            active_persona = ""
        persona_block = f"{active_persona}\n" if active_persona else ""
        # 天气注入（关怀时可自然结合当地天气）
        weather_line = ""
        try:
            from app.services.weather_service import get_user_weather_line
            weather_line = await get_user_weather_line(user_id)
        except Exception:
            weather_line = ""
        hint = (
            f"{identity}\n"
            f"{persona_block}"
            + (f"{weather_line}\n" if weather_line else "")
            + f"用户刚才跟你说：「{trigger_msg}」——听起来心情不太好。\n"
            "过了一阵子，你主动关心他一句：1-2 句话，口语化，像真的在意他。\n"
            "多共情、少讲道理；不要出现'检测情绪''系统通知'这类字眼。"
        )
        from app.agent.llm_client import load_character_reasoning_level
        _rl = await load_character_reasoning_level(char_id)
        _msgs = [
            {"role": "system", "content": "直接输出要说的话，不要加引号和标注。"},
            {"role": "user", "content": hint},
        ]
        # D2-C（2026-08-18）：情绪关怀关闭深度思考——统一走挡位 1/0 的 prompt 引导分支
        # （挡位 1 保留「先在心里简短想一下」引导；_emotion_reasoning 恒为空串，extra_meta 不再带 reasoning）
        _emotion_reasoning = ""
        if _rl == 1:
            _msgs[0] = {"role": "system", "content": "先在心里简短想一下怎么说合适，然后直接输出要说的话，不要加引号和标注。"}
        text = await chat_completion(messages=_msgs, temperature=0.9, max_tokens=256,
                                     task="emotion", user_id=user_id)
        text = (text or "").strip().strip('"').strip("'")
        if not text or len(text) < 2:
            await _finish_task(task_id, "cancelled")
            return False
    except Exception as e:
        _logger.warning("Emotion care LLM failed char=%d: %s", char_id, e)
        return False

    _emotion_extra = None
    if _emotion_reasoning:
        import json as _json
        _emotion_extra = _json.dumps({"reasoning": _emotion_reasoning}, ensure_ascii=False)
    await send_to_session(
        session_id=session_id, character_id=char_id, user_id=user_id,
        content=text[:500], message_type=CARE_TYPE,
        extra_meta=_emotion_extra,
    )
    await _finish_task(task_id, "done")
    _logger.info("Emotion care sent char=%d", char_id)
    return True
