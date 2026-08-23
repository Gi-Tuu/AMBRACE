"""日记生成器 — 每天定时用 LLM 生成 AI 日记"""
from datetime import date, datetime, timezone, timedelta
from sqlalchemy import select
from app.db.database import async_session_factory
from app.models.diary import AIDiary
from app.models.character import AICharacter
from app.models.chat_session import ChatSession
from app.models.chat_message import ChatMessage
from app.models.proactive_settings import ProactiveSettings
from app.agent.llm_client import chat_completion
from app.utils.logger import get_logger

_logger = get_logger("scheduler.diary")


async def get_today_chat_context(
    character_id: int, target_date: date, beijing_window: bool = True, user_label: str = "用户",
    user_id: int | None = None,
) -> str:
    """获取某天该角色的聊天内容摘要（默认按北京时间窗口；修复旧数据时可用 UTC 窗口）"""
    # 日记日期是北京日期：北京 0 点 = UTC 前一天 16 点
    day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
    if beijing_window:
        day_start = day_start - timedelta(hours=8)
    day_end = day_start + timedelta(days=1)

    async with async_session_factory() as db:
        # 找该角色当天的活跃会话（按最新消息时间，避免 updated_at 污染选错）
        from app.services.chat_service import get_latest_session_id
        session_id = await get_latest_session_id(user_id, character_id)
        session = await db.get(ChatSession, session_id) if session_id else None
        if not session:
            return ""

        msgs_result = await db.execute(
            select(ChatMessage).where(
                ChatMessage.session_id == session.id,
                ChatMessage.created_at >= day_start,
                ChatMessage.created_at < day_end,
            ).order_by(ChatMessage.created_at.asc())
        )
        msgs = msgs_result.scalars().all()
        if not msgs:
            return ""

        lines = []
        for m in msgs:
            content = (m.content or "").strip()
            if not content:
                continue
            role = "我" if m.sender_type == "ai" else user_label
            lines.append(f"{role}: {content[:200]}")
        return "\n".join(lines)


async def generate_diary_for_character(
    character_id: int,
    target_date: date | None = None,
    force: bool = False,
    beijing_window: bool = True,
) -> dict | None:
    """为角色生成指定日期的日记"""
    if target_date is None:
        target_date = date.today()
    date_str = target_date.strftime("%Y-%m-%d")

    # 检查是否已有日记
    async with async_session_factory() as db:
        result = await db.execute(
            select(AIDiary).where(
                AIDiary.character_id == character_id,
                AIDiary.diary_date == date_str,
            )
        )
        existing = result.scalar_one_or_none()
        if existing and not force:
            _logger.debug("Diary already exists for char=%d date=%s", character_id, date_str)
            return None

        # 获取角色信息
        char_result = await db.execute(select(AICharacter).where(AICharacter.id == character_id))
        char = char_result.scalar_one_or_none()
        if not char:
            return None

    try:
        from app.agent.user_profile import build_user_profile_text, build_relation_line, get_user_nickname
        owner_id = char.user_id or 1
        user_profile = await build_user_profile_text(owner_id)
        relation_line = await build_relation_line(char)
        user_nickname = await get_user_nickname(owner_id)
    except Exception:
        user_profile = ""
        relation_line = ""
        user_nickname = "用户"

    chat_context = await get_today_chat_context(
        character_id, target_date, beijing_window=beijing_window, user_label=user_nickname,
        user_id=char.user_id,
    )
    if not chat_context:
        _logger.debug("No chat context for char=%d date=%s", character_id, date_str)
        return None

    weekday_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    wd = weekday_cn[target_date.weekday()]
    date_cn = f"{target_date.year}年{target_date.month}月{target_date.day}日 {wd}"

    # 天气注入（用户开启位置信息时，写日记可自然提到当天天气）
    weather_line = ""
    try:
        from app.services.weather_service import get_user_weather_line
        weather_line = await get_user_weather_line(owner_id)
    except Exception:
        weather_line = ""

    prompt = (
        f"你是{char.name}，请以第一人称写一篇日记，记录{date_cn}发生的事。\n"
        f"{('今日天气：' + weather_line + '\n') if weather_line else ''}"
        f"你的性格：{char.personality or '友善'}\n"
        f"你的聊天风格：{char.chat_style or '自然'}\n\n"
        f"你和用户的关系：{relation_line or '普通朋友'}\n\n"
        f"用户画像（用于区分你和用户的身份，不要混淆）：\n{user_profile or '用户昵称: 用户'}\n\n"
        f"今天的聊天记录（『我』指你{char.name}，『{user_nickname}』指用户）：\n{chat_context[:1500]}\n\n"
        f"请以你的视角写一篇自然、口语化的日记，像真人写的那样。\n"
        f"记录你和{user_nickname}的互动：他做了什么、说了什么，你的回应和感受。\n"
        f"注意：你是{char.name}，不是{user_nickname}，不要把他的经历和说的话安在自己身上；"
        f"{user_nickname}的对象是谁以用户画像为准，不要默认是异性。\n"
        f"事实规则：只写今天真实发生的事；推测/计划用'可能/打算'表达，不要编造没发生的事；"
        f"剧情/角色扮演的内容不写进日记。\n"
        f"时间规则：日记涉及日期用具体表述（记录的是{date_cn}），不要用'最近/前几天'等模糊时间词。\n"
        f"100-200字左右。不要加标题，直接开始写日记内容。"
    )

    messages = [
        {"role": "system", "content": f"你是{char.name}，正在写私人日记。用第一人称，语气自然真实。"},
        {"role": "user", "content": prompt},
    ]
    response = await chat_completion(messages=messages, temperature=0.8, max_tokens=512,
                                     task="diary", user_id=(char.user_id if char else 1))
    diary_content = response.strip().strip('"').strip("'")

    # 保存日记（force 时在同一 session 内重新查询再更新，避免 detached 对象不落库）
    async with async_session_factory() as db:
        result = await db.execute(
            select(AIDiary).where(
                AIDiary.character_id == character_id,
                AIDiary.diary_date == date_str,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.content = diary_content
            await db.commit()
            entry = existing
        else:
            entry = AIDiary(
                character_id=character_id,
                diary_date=date_str,
                content=diary_content,
            )
            db.add(entry)
            await db.commit()
            await db.refresh(entry)

    # 自动存入记忆
    try:
        from app.memory import save_memory
        await save_memory(
            user_id=char.user_id or 1,
            character_id=character_id,
            memory_type="insight",
            content=f"日记: {diary_content[:200]}",
            importance=2,
            sub_type="diary",
            source="diary",
            speaker_type="character", speaker_id=character_id,
            epistemic_status="FACT",
        )
    except Exception as e:
        _logger.warning("Failed to save diary as memory: %s", e)

    _logger.info("Diary generated for char=%d date=%s (%d chars)", character_id, date_str, len(diary_content))
    return {"id": entry.id, "diary_date": date_str, "content": diary_content}


async def generate_missing_diaries():
    """服务器启动时补生成最近缺失的日记"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(ProactiveSettings).where(ProactiveSettings.diary_enabled == True)
        )
        settings_list = result.scalars().all()

    today = date.today()
    for settings in settings_list:
        for days_ago in range(1, 4):  # 补最近 3 天（昨天及以前）
            target_date = today - timedelta(days=days_ago)
            try:
                await generate_diary_for_character(settings.character_id, target_date)
            except Exception as e:
                _logger.warning("Missing diary gen failed char=%d date=%s: %s",
                                settings.character_id, target_date, e)
