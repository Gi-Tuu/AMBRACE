"""日记生成器 — 每天定时用 LLM 生成 AI 日记"""
from datetime import date, datetime, timezone, timedelta
from sqlalchemy import func, select
from app.db.database import async_session_factory
from app.models.life import AIDiary
from app.models.character import AICharacter
from app.models.chat import ChatSession
from app.models.chat import ChatMessage
from app.models.character import ProactiveSettings
from app.agent.llm_client import chat_completion
from app.utils.logger import get_logger
from app.utils.timeutil import app_local_now

_logger = get_logger("scheduler.diary")


def _day_window(target_date: date, beijing_window: bool = True) -> tuple[datetime, datetime]:
    """某一「北京日记日」对应的 UTC 时刻区间（**同一谓词只有一份出处**）。

    日记日期按北京日算：北京 0 点 = UTC 前一天 16 点。聊天正文取数与「日记是否过期」的
    新消息判定都必须走这一个函数——两边各写一遍减法，改天时区口径时只会红一边。
    """
    day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
    if beijing_window:
        day_start = day_start - timedelta(hours=8)
    return day_start, day_start + timedelta(days=1)


def _as_naive_utc(value) -> datetime | None:
    """库里两个时间列都按 naive UTC 存；万一读到带 tz 的值，先换 UTC 再去 tzinfo（不拿它比大小会 TypeError）。"""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _diary_is_stale(diary_created_at, latest_msg_at, *, now_unused=None) -> bool:
    """「该日之后是否又有新对话」的判据：日记写完**之后**当天又出现消息 ⇒ 这份日记该重写。

    两个时刻都取自库（同一时钟），本函数不碰进程时钟。任一时刻取不到 ⇒ 判**不**过期：
    宁可少重写一次，也不要因为读不出就每拍重跑一遍 LLM（那会把「每天一篇」变成「每小时一篇」）。
    """
    created = _as_naive_utc(diary_created_at)
    latest = _as_naive_utc(latest_msg_at)
    if created is None or latest is None:
        return False
    return latest > created


async def get_today_chat_context(
    character_id: int, target_date: date, beijing_window: bool = True, user_label: str = "用户",
    user_id: int | None = None,
) -> str:
    """获取某天该角色的聊天内容摘要（默认按北京时间窗口；修复旧数据时可用 UTC 窗口）"""
    day_start, day_end = _day_window(target_date, beijing_window)

    async with async_session_factory() as db:
        # 找该角色当天的活跃会话（按最新消息时间，避免 updated_at 污染选错）
        from app.application.chat_service import get_latest_session_id
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
        target_date = app_local_now().date()
    date_str = target_date.strftime("%Y-%m-%d")

    # 检查是否已有日记（A41 补 C30：旧口径「有日记就跳过」会把"写完日记之后又聊的那截"永久丢掉）
    async with async_session_factory() as db:
        result = await db.execute(
            select(AIDiary).where(
                AIDiary.character_id == character_id,
                AIDiary.diary_date == date_str,
            )
        )
        existing = result.scalar_one_or_none()

        # 获取角色信息
        char_result = await db.execute(select(AICharacter).where(AICharacter.id == character_id))
        char = char_result.scalar_one_or_none()
        if not char:
            return None

        # 多账号隔离（C 家族）：与 publish_moment 同口径——无归属不生成（否则日记记忆写进 1 号账号）
        if not char.user_id:
            _logger.warning("Diary skipped: character has no owner char=%d", character_id)
            return None

        if existing and not force:
            day_start, day_end = _day_window(target_date, beijing_window)
            from app.application.chat_service import get_latest_session_id
            sid = await get_latest_session_id(char.user_id, character_id)
            latest = None
            if sid:
                latest = (await db.execute(
                    select(func.max(ChatMessage.created_at)).where(
                        ChatMessage.session_id == sid,
                        ChatMessage.created_at >= day_start,
                        ChatMessage.created_at < day_end,
                    )
                )).scalar()
            if not _diary_is_stale(existing.created_at, latest):
                _logger.debug("Diary already exists for char=%d date=%s", character_id, date_str)
                return None
            # 过期 ⇒ 不提前返回，往下走正常生成流程（保存分支会在同一行上 update，不新增第二条）
            _logger.info("Diary stale for char=%d date=%s（日记写于 %s，该日 %s 又有新消息）⇒ 重写",
                         character_id, date_str, existing.created_at, latest)

    try:
        from app.agent.user_profile import build_user_profile_text, build_relation_line, get_user_nickname
        owner_id = char.user_id
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
        from app.application.weather_service import get_user_weather_line
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


async def generate_missing_diaries() -> dict:
    """补生成最近缺失的日记（昨天及以前 3 天）。返回 counts；**只要有一天失败就抛**。

    A41 补（C30 的后半）：旧写法把每个日期的异常就地吞掉 ⇒ `run_daily_if_due("diary", …)` 看到的是
    "这一拍顺利跑完" ⇒ 记 done、当天不再跑；而补生成窗口只有 3 天，LLM 抖动 40 分钟就足以让某天
    永久出局（用户可见效果＝**日记少一天，且永远不会自己补回来**）。
    现在改成「每个角色每天都照样尝试完，最后汇总抛错」⇒ 台账按失败退避（15/30/60 分钟）当天再试，
    而"该日没有新对话"（skip）不算失败 ⇒ 0 篇仍会正常记 done，不会变成每拍空转。
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(ProactiveSettings).where(ProactiveSettings.diary_enabled == True)
        )
        settings_list = result.scalars().all()

    counts = {"attempted": 0, "written": 0, "skipped": 0, "failed": 0}
    today = app_local_now().date()
    for settings in settings_list:
        for days_ago in range(1, 4):  # 补最近 3 天（昨天及以前）
            target_date = today - timedelta(days=days_ago)
            counts["attempted"] += 1
            try:
                made = await generate_diary_for_character(settings.character_id, target_date)
            except Exception as e:
                counts["failed"] += 1
                _logger.warning("Missing diary gen failed char=%d date=%s: %s",
                                settings.character_id, target_date, e)
                continue
            counts["written" if made else "skipped"] += 1
    if counts["failed"]:
        # 抛的是普通异常（不带敏感数据）：让 run_daily_if_due 走 mark_failed_daily 退避重试
        raise RuntimeError(f"diary catchup incomplete: {counts}")
    _logger.info("Diary catchup: %s", counts)
    return counts
