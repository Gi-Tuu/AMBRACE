"""主动到期复习（P1，2026-08-05）：到期记忆 → 角色自然提起 → 用户回应判定强化。

数据流：
- collect_review_events：arbiter tick 扫描 next_review_at 到期且 importance>=40 的记忆（每角色 1 条候选）
- run_memory_review：生成并发送自然提及（LLM 1 次），记录 ProactiveMessageLog(extra_meta.memory_id)，
  并将 next_review_at 推迟 REVIEW_RETRY_DAYS（等待用户回应窗口）
- maybe_review_success：用户回复时调用，24h 内有 review 记录且回复与记忆内容弱相关 → 强化 S×2 并重排
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.models.character import AICharacter
from app.memory.flags import memory_v2_enabled as _memory_v2_enabled
from app.models.character import ProactiveMessageLog
from app.memory.constants import (
    REVIEW_MIN_IMPORTANCE, REVIEW_MAX_PER_DAY, REVIEW_RETRY_DAYS, REVIEW_SUCCESS_WINDOW_HOURS,
    REVIEW_MIN_INTERVAL_MINUTES, REINFORCE_FACTOR_RETRIEVE, REVIEW_NOSTALGIA_MAX_PER_DAY,
)
from app.memory.tense import classify_tense, is_plan_expired, days_since
from app.utils.logger import get_logger
from app.utils.dnd import user_in_dnd_period as _user_in_dnd_period

_logger = get_logger("scheduler.memory_review")

REVIEW_TYPE = "memory_review"
_TYPE_LABEL = {"user_info": "关于你的事", "preference": "你的喜好", "event": "发生过的事", "insight": "我的一些想法"}

# F1/F4/F5（2026-09-08，Sam 主动消息错接昨晚剧情 P0）：
# 主动复习生成注入"现在"时间锚点 + 最近聊天"场景已结束"标注 + 轻量防复读闸门。
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


def _recent_context_line(recent_context: str, gap_hours: float | None,
                         last_msg_at: datetime | None) -> str:
    """F4：最近聊天注入——距今 >2h 标注「场景已结束」，防止把昨晚哄睡语境当可续写的当下场景。"""
    if not recent_context:
        return ""
    if gap_hours is None or gap_hours <= 2:
        return f"\n你们最近在聊：\n{recent_context}\n"
    n = max(1, int(round(gap_hours)))
    when = ""
    if last_msg_at is not None:
        bj = last_msg_at.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
        when = f"（{bj.month}月{bj.day}日{_cn_noon_label(bj.hour)}）"
    return (
        f"\n你们最近一次聊天是在 {n} 小时前{when}，那段场景已经结束。"
        f"不要接着上次的场景续演，现在自然地开一个新话头：\n{recent_context}\n"
    )


def _is_replay_of_recent(text: str, recent_context: str, memory_content: str) -> bool:
    """F5-b：与最近聊天某句相似 >0.5 且与记忆内容无主题重合 → 判复读不发送。

    主题重合口径与 maybe_review_success 一致（相似 >0.15 或存在 ≥4 字公共子串）；
    recent_context / 生成文本为空时直接放行（防御，不误伤正常对话式复习）。
    """
    from difflib import SequenceMatcher
    if not text or not recent_context:
        return False
    mem = (memory_content or "").strip()
    if mem:
        if SequenceMatcher(None, text[:100], mem[:100]).ratio() > 0.15:
            return False
        for i in range(max(0, len(mem) - 3)):
            if mem[i:i + 4] in text:
                return False
    for line in recent_context.splitlines():
        line = line.strip()
        if ": " in line:
            line = line.split(": ", 1)[1]
        if not line or len(line) < 4:
            continue
        if SequenceMatcher(None, text[:100], line[:100]).ratio() > 0.5:
            return True
    return False


async def _last_message_time(session_id: int) -> datetime | None:
    """F4：会话最近一条消息时间（UTC naive；无消息/异常返回 None，只读查询）。"""
    from app.models.chat import ChatMessage
    async with async_session_factory() as db:
        row = (await db.execute(
            select(ChatMessage.created_at)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )).first()
    return row[0] if row else None


def _exclude_flag_on() -> bool:
    """L1 flag（2026-09-09 主动复习「回忆化」）：选片排除过期计划/瞬时状态（默认开；关=旧选片）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("review_exclude_expired_plan", True))
    except Exception:
        return False


def _life_no_replay_on() -> bool:
    """L4 flag（2026-09-09 主体归属治理）：一次性生活动作不主动复读（默认关；关=维持现选片）。

    与「回忆化」L1 的 review_exclude_expired_plan 同属一批 flag 体系，不另起第二套。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("life_event_no_replay", False))
    except Exception:
        return False


def _reminisce_flag_on() -> bool:
    """L3 flag：复习生成改「回忆框架」hint + 输出闸门（默认开；关=逐字节回旧 hint）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("review_reminisce_framework", True))
    except Exception:
        return False


async def collect_review_events() -> list[dict]:
    """扫描到期记忆 → 每角色 1 条候选（arbiter 事件源，priority=1）。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    from app.memory.service import _active_status_clause  # #70-C：仅 active（flag 关=永真）
    async with async_session_factory() as db:
        mems = (await db.execute(
            select(Memory)
            .where(
                Memory.is_archived == False,
                Memory.is_pinned == False,
                Memory.is_locked == False,
                Memory.importance >= REVIEW_MIN_IMPORTANCE,
                Memory.next_review_at.is_not(None),
                Memory.next_review_at <= now,
                _active_status_clause(),
            )
            .order_by(Memory.next_review_at.asc())
        )).scalars().all()
        # —— L1（2026-09-09 主动复习「回忆化」）：时态过滤 ——
        # 过期计划不再主动当新闻提（仍可检索、降权可怀旧）；瞬时状态不进主动复习（走 world_facts）。
        # flag review_exclude_expired_plan 关 = 逐字节回到旧选片行为。
        try:
            if _exclude_flag_on():
                mems = [m for m in mems
                        if classify_tense(m) != "transient"
                        and not (classify_tense(m) == "plan" and is_plan_expired(m, now))]
            # L4（2026-09-09 主体归属治理）：一次性生活动作（source=life 的 event 记忆，
            # 如「粥在锅里」「桌上粥还温着」）天然短时效、结束即失效，不进主动到期播报
            # ——仍可检索怀旧，只是不由系统主动反复提。与「回忆化」L1 同处一个筛选段。
            if _life_no_replay_on():
                mems = [m for m in mems
                        if not (getattr(m, "source", None) == "life"
                                and (m.memory_type or "") == "event")]
        except Exception as e:
            _logger.warning("review tense filter failed: %s", e)
    # 每角色只取最早到期的一条
    per_char: dict[int, tuple] = {}
    for m in mems:
        if m.user_id and m.character_id not in per_char:
            per_char[m.character_id] = (m.character_id, m.user_id, m.id)
    # 审计 P2-04：无活跃会话的角色不产生复习候选（避免每 tick 空转出候选 + 写 rejected 日志）
    if per_char:
        try:
            from app.models.chat import ChatSession
            async with async_session_factory() as db:
                _sess = (await db.execute(
                    select(ChatSession.character_id).where(
                        ChatSession.character_id.in_(list(per_char.keys())),
                        ChatSession.is_active == True,  # noqa: E712
                    ).distinct()
                )).scalars().all()
            _active_chars = set(_sess)
            per_char = {k: v for k, v in per_char.items() if k in _active_chars}
        except Exception as e:
            _logger.warning("review active-session filter failed: %s", e)
    return [
        {"type": REVIEW_TYPE, "priority": 1, "candidate": {
            "character_id": cid, "user_id": uid, "memory_id": mid,
        }}
        for cid, uid, mid in per_char.values()
    ]


async def _daily_count(db, character_id: int) -> int:
    cn_tz = timezone(timedelta(hours=8))
    today_start = datetime.now(cn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start.astimezone(timezone.utc).replace(tzinfo=None)
    return (await db.execute(
        select(func.count(ProactiveMessageLog.id)).where(
            ProactiveMessageLog.character_id == character_id,
            ProactiveMessageLog.message_type == REVIEW_TYPE,
            ProactiveMessageLog.created_at >= today_start,
        )
    )).scalar() or 0


async def _daily_count_by_tense(db, character_id: int, tense: str = "nostalgia") -> int:
    """L1（2026-09-09）：该角色今天已发的「怀旧式复习」条数（extra_meta.tense 计数，SQLite json_extract）。"""
    cn_tz = timezone(timedelta(hours=8))
    today_start = datetime.now(cn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start.astimezone(timezone.utc).replace(tzinfo=None)
    return (await db.execute(
        select(func.count(ProactiveMessageLog.id)).where(
            ProactiveMessageLog.character_id == character_id,
            ProactiveMessageLog.message_type == REVIEW_TYPE,
            ProactiveMessageLog.created_at >= today_start,
            func.json_extract(ProactiveMessageLog.extra_meta, "$.tense") == tense,
        )
    )).scalar() or 0


def _review_daily_cap() -> int:
    """M1-S7（2026-08-31）：复习日额度 flag 化——review_daily_plus 开=4（默认），关=回退 3。

    纯函数便于单测；90 分钟最小间隔不变（防轰炸靠间隔而非额度）。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return REVIEW_MAX_PER_DAY + 1 if AGENT_FLAGS.get("review_daily_plus", True) else REVIEW_MAX_PER_DAY
    except Exception:
        return REVIEW_MAX_PER_DAY


# M1-S7：敷衍回复词表——极短纯语气词/纯标点不算"接住"（其余实质回复 24h 内均算复习成功）
_NON_SUBSTANTIVE_REPLIES = {
    "哦", "嗯", "啊", "呃", "额", "噢", "哦哦", "嗯嗯", "噢噢", "呃呃",
    "呵呵", "哈哈", "哦了", "嗯了", "行吧", "哦呀",
}


def _is_substantive_reply(text: str) -> bool:
    """M1-S7（纯函数）：实质回复判定——去标点/空白后 ≥2 字且非敷衍词表即算（治 P-E1 误判失败）。"""
    import re as _re
    t = _re.sub(r"[\s。，！？…~、；：,.!?;:()（）\"'“”‘’【】\[\]…]+", "", text or "")
    if not t or len(t) < 2:
        return False
    return t not in _NON_SUBSTANTIVE_REPLIES


async def _last_review_at(db, character_id: int):
    """该角色最近一条复习消息发送时间（最小间隔保护用）。"""
    return (await db.execute(
        select(func.max(ProactiveMessageLog.created_at)).where(
            ProactiveMessageLog.character_id == character_id,
            ProactiveMessageLog.message_type == REVIEW_TYPE,
        )
    )).scalar_one_or_none()


# ── L3（2026-09-09 主动复习「回忆化」）：回忆框架 hint + 输出本地闸门 ──
# 设计意图（用户定调，最高约束）：记忆复习 =「回忆/怀旧」——把旧记忆当往事回味，
# 不当"最近/当前仍成立/即将发生"的状态续写、叮嘱、指挥（治 7018/7021 "电脑记得带"时空错位）。
_TENSE_RULES = {
    "nostalgia": (
        "这是**已经发生过 / 已经过期的往事**，不是现在正在发生、也不是未来还要发生的事。"
        "必须用回忆、回味的口吻（'我记得…''还记得那回…''上次…''那时候…'），用过去时提起；"
        "**禁止**据此提醒、叮嘱、安排 TA 现在或未来去做什么（不要出现'记得带/别忘了/到了报平安/明天…/快…'这类当下或临期指令）；"
        "可以感慨、调侃、回味，或轻轻联系到 TA 现在的状态，但绝不能把旧安排说成马上要发生。"
    ),
    "plan": (
        "这是 TA **之前提过、目前仍在有效期内**的安排；开口要以'你之前说/计划…'引用，"
        "可以做一次轻确认或提醒，但要明确这是早前的约定，不要当成此刻刚发生。"
    ),
    "enduring": (
        "这是你了解的关于 TA 的长期事实 / 偏好 / 关系记忆；可以自然地回忆并顺势带到当下的关心，"
        "但仍是'你一直记得 TA…'的口吻，而不是新发生的事件。"
    ),
}

# 输出闸门词表（零 LLM）：当下/临期指令词 vs 回忆指代词
_DIRECTIVE_HINTS = ("记得带", "别忘了", "别忘", "到了报平安", "报平安", "明天", "后天",
                    "快出门", "赶紧", "别迟到", "记得拿", "带上", "收拾好", "准备出发")
_REMINISCE_HINTS = ("我记得", "还记得", "上次", "那回", "那时候", "当初", "以前", "那会儿", "想起那")


def _is_wrong_tense_directive(text: str, is_nostalgia: bool) -> bool:
    """L3 输出闸门：怀旧/过期计划记忆却生成当下/临期叮嘱、且全句无任何回忆指代词 → 拦截。

    保守设计：三者同时成立才拦（正常"我记得你上次去长沙还…"不会误伤）。
    """
    if not is_nostalgia or not text:
        return False
    if any(k in text for k in _REMINISCE_HINTS):
        return False
    return any(k in text for k in _DIRECTIVE_HINTS)


def _review_phrase(mem, now_naive) -> tuple[str, bool, str]:
    """L3：时态化回忆引导语。返回 (引导语, 是否怀旧, 时态 kind ∈ nostalgia/plan/enduring)。"""
    tense = classify_tense(mem)
    d = days_since(mem, now_naive)
    ago = f"（记录于 {mem.created_at:%m月%d日}，距今约 {d} 天）" if d is not None and d >= 3 else ""
    if tense == "plan":
        if is_plan_expired(mem, now_naive):
            return f"你回忆起一个**已经过去的旧安排**{ago}", True, "nostalgia"
        return f"你想起 TA 之前跟你提过的一个还没到的安排{ago}", False, "plan"
    if tense == "episodic":
        return f"你回忆起一段**往事**{ago}", True, "nostalgia"
    if tense == "transient":
        return f"你想起一条当时的状态{ago}", True, "nostalgia"
    return f"你想起关于 TA 的一件事{ago}", False, "enduring"


async def _current_status_anchor(char_id: int, user_id: int) -> str:
    """L3：当前现状锚点（只读，失败静默返回空串）——防与"早已回家/开学"矛盾。

    C3（2026-09-10）：下沉到 app.memory.current_state（三源聚合：per-char WorldFact + User
    表已授权城市 + GlobalUserFact 启用槽）。旧实现只查 per-char WorldFact subject=user
    （当前无写入点、恒空）；新实现额外在用户已授权位置感知时锚定当前城市。失败静默语义不变。
    """
    try:
        from app.memory.current_state import current_user_state_anchor
        return await current_user_state_anchor(
            character_id=char_id, user_id=user_id, include_profile_location=True)
    except Exception:
        return ""


async def run_memory_review(char_id: int, user_id: int, memory_id: int) -> bool:
    """执行一次主动复习：限额/免打扰/会话检查 → 先占位重排（防失败重试烧 token）→ LLM 生成 → 发送 → 记录。"""
    from app.scheduling.scheduler import send_to_session
    from app.application.chat_service import get_latest_session_id
    from app.scheduling.triggers import memory_review_enabled
    # 0) 主动互动主开关 / 记忆复习子开关 任一关闭则不发送
    if not await memory_review_enabled(char_id):
        return False
    # 1) 无活跃会话直接放弃（不生成、不占位：下个 tick 会再来，但零成本）
    session_id = await get_latest_session_id(user_id, char_id)
    if session_id is None:
        return False

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        if await _daily_count(db, char_id) >= _review_daily_cap():
            _logger.info("Memory review char=%d skipped: daily limit", char_id)
            return False
        last = await _last_review_at(db, char_id)
        if last is not None:
            last = last.replace(tzinfo=None) if last.tzinfo else last
            if now_naive - last < timedelta(minutes=REVIEW_MIN_INTERVAL_MINUTES):
                return False
        if await _user_in_dnd_period(db, user_id):
            return False
        mem = await db.get(Memory, memory_id)
        if mem is None or mem.is_archived or mem.is_pinned or mem.is_locked:
            return False
        # L1/L3（2026-09-09）：时态定位（纯函数）+ 怀旧日额度——往事/过期计划即便被回忆也不刷屏
        phrase, is_nostalgia, tense_kind = _review_phrase(mem, now_naive)
        if is_nostalgia and _exclude_flag_on():
            if await _daily_count_by_tense(db, char_id, "nostalgia") >= REVIEW_NOSTALGIA_MAX_PER_DAY:
                _logger.info("Memory review char=%d skipped: nostalgia daily limit", char_id)
                return False
        char = await db.get(AICharacter, char_id)
        content_src = mem.content
        mem_type = mem.memory_type
        # 先占位：无论后续 LLM/发送是否成功，3 天后才再试（防止无会话/限流导致每 30s 烧一次 LLM）
        mem.next_review_at = now_naive + timedelta(days=REVIEW_RETRY_DAYS)
        await db.commit()

    char_name = char.name if char else "我"
    personality = (char.personality or "友善")[:100] if char else "友善"
    # 注入最近聊天上下文：让记忆自然融入当前话题，避免生硬转折割裂对话
    recent_context = ""
    try:
        from app.scheduling.triggers import get_last_messages
        recent_context = (await get_last_messages(session_id))[:500]
    except Exception:
        pass
    # F4：最近一条消息距今时间差（>2h → 场景已结束标注，防续演昨晚剧情）
    last_msg_at = None
    gap_hours = None
    try:
        last_msg_at = await _last_message_time(session_id)
    except Exception:
        last_msg_at = None
    if last_msg_at is not None:
        last_msg_at = last_msg_at.replace(tzinfo=None) if last_msg_at.tzinfo else last_msg_at
        gap_hours = (now_naive - last_msg_at).total_seconds() / 3600.0
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
        label = _TYPE_LABEL.get(mem_type, "一件事")
        context_line = _recent_context_line(recent_context, gap_hours, last_msg_at)
        # L3：hint 改「回忆框架」——时态化引导语 + 记录日期/距今 + 当前现状锚点 + 时态口吻规则。
        # 保留 F1 时间锚点（_cn_now_prefix）/ F4 场景标注（context_line）/ F5 防复读，只在其上叠加时态定位。
        _reminisce = _reminisce_flag_on()
        status_anchor = ""
        if _reminisce and is_nostalgia:
            status_anchor = await _current_status_anchor(char_id, user_id)
        if _reminisce:
            tense_rule = _TENSE_RULES["nostalgia" if is_nostalgia else tense_kind]
            hint = (
                f"{_cn_now_prefix()}\n"
                f"{identity}\n{persona_block}"
                f"{status_anchor}"
                f"{phrase}：{content_src[:120]}{context_line}\n"
                f"{tense_rule}\n"
                "自然地跟 TA 提一句，像老朋友回忆往事一样（1-2 句话，口语化），"
                "不要生硬转折，不要提'记忆''复习''想起以前记录'这类字眼。"
                "你要说的话必须围绕刚才回忆起的内容展开（可以感慨、确认、调侃），"  # F5(a) 聚焦记忆约束保留
                "必须全程以第一人称'我'说话（你=角色本人），不要以旁观者视角提及你自己的名字或'某人'。"
                "最近聊天只决定你开口的语气，不能只顺着最近聊天接话，更不能复述最近聊天里的句子。"
            )
        else:
            hint = (  # flag 关：逐字节回到旧 hint（回退路径）
                f"{_cn_now_prefix()}\n"
                f"{identity}\n"
                f"{persona_block}"
                f"你想起了{label}：{content_src[:120]}{context_line}"
                "自然地跟用户提一句，像老朋友聊天一样（1-2 句话，口语化），"
                "不要生硬转折，不要提'记忆''复习''想起以前记录'这类字眼。"
                f"你要说的话必须围绕「你想起了{label}」的内容展开（可以是确认、更新、调侃），"
                "最近聊天只决定你开口的语气和方式，不能只顺着最近聊天接话，更不能复述最近聊天里的句子。"
                "必须全程以第一人称'我'说话（你=角色本人），不要以旁观者视角提及你自己的名字或'某人'这类第三人称。"
            )
        from app.agent.llm_client import load_character_reasoning_level
        _rl = await load_character_reasoning_level(char_id)
        _msgs = [
            {"role": "system", "content": "直接输出要说的话，不要加引号和标注。"},
            {"role": "user", "content": hint},
        ]
        # D2-D（2026-08-18）：记忆复习关闭深度思考——统一走挡位 1/0 的 prompt 引导分支
        # （挡位 1 保留「先在心里简短想一下」引导；_review_reasoning 恒为空串，_review_extra 仅保留 memory_id）
        _review_reasoning = ""
        if _rl == 1:
            _msgs[0] = {"role": "system", "content": "先在心里简短想一下怎么说合适，然后直接输出要说的话，不要加引号和标注。"}
        text = await chat_completion(messages=_msgs, temperature=0.85, max_tokens=256,
                                     task="review", user_id=user_id)
        text = (text or "").strip().strip('"').strip("'")
        if not text or len(text) < 2:
            return False
        # F5-b：与最近聊天高度复读且与记忆内容无主题重合 → 不发送
        #（next_review_at 已在占位阶段顺延 3 天，不会烧钱重试）
        if _is_replay_of_recent(text, recent_context, content_src):
            _logger.info("Memory review char=%d mem=%d blocked: replay of recent chat",
                         char_id, memory_id)
            return False
        # L3：输出本地闸门（零 LLM）——怀旧记忆却生成"当下叮嘱式回忆"（如"电脑记得带"）→ 拦截不发送
        #（next_review_at 已在占位阶段顺延 3 天，不会重试烧 token）
        if _is_wrong_tense_directive(text, is_nostalgia):
            _logger.info("Memory review char=%d mem=%d blocked: wrong-tense directive",
                         char_id, memory_id)
            return False
    except Exception as e:
        _logger.warning("Memory review LLM failed char=%d: %s", char_id, e)
        return False

    # 发送（send_to_session 内部已落库 ChatMessage + ProactiveMessageLog[含 extra_meta] + WS 推送）
    # L1 §4.3：extra_meta 记 tense（nostalgia/plan/enduring），供怀旧限频计数与观察；
    # 两个相关 flag 全关时不写（extra_meta 逐字节回到旧行为）
    _review_extra = {"memory_id": memory_id}
    if _exclude_flag_on() or _reminisce_flag_on():
        _review_extra["tense"] = tense_kind
    if _review_reasoning:
        _review_extra["reasoning"] = _review_reasoning
    await send_to_session(
        session_id=session_id, character_id=char_id, user_id=user_id,
        content=text[:500], message_type=REVIEW_TYPE,
        extra_meta=json.dumps(_review_extra, ensure_ascii=False),
    )
    _logger.info("Memory review sent char=%d mem=%d", char_id, memory_id)
    return True


async def maybe_review_success(user_id: int, character_id: int, user_content: str) -> int:
    """用户回复时调用：24h 内有 review 记录且回复"接住"了复习 → 强化 S×4/3（成功复习）。

    返回强化条数。M1-S7 判定（2026-08-31）：字符相似 >0.15 照旧命中；
    相似未命中但属实质回复（_is_substantive_reply，排除哦/嗯等敷衍）也算成功——
    避免"用户明明回应了却被判失败、3 天后才重试"。
    """
    from difflib import SequenceMatcher
    if not user_content or not user_content.strip():
        return 0
    window_start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        hours=REVIEW_SUCCESS_WINDOW_HOURS)
    async with async_session_factory() as db:
        logs = (await db.execute(
            select(ProactiveMessageLog)
            .where(
                ProactiveMessageLog.character_id == character_id,
                ProactiveMessageLog.message_type == REVIEW_TYPE,
                ProactiveMessageLog.created_at >= window_start,
            )
            .order_by(ProactiveMessageLog.id.desc())
        )).scalars().all()
        if not logs:
            return 0
        # 只处理最新一条 review（避免同一记忆反复强化）
        log = logs[0]
        try:
            meta = json.loads(log.extra_meta or "{}")
        except Exception:
            meta = {}
        memory_id = meta.get("memory_id")
        if not memory_id:
            return 0
        mem = await db.get(Memory, memory_id)
        if mem is None or mem.is_archived or mem.is_pinned or mem.is_locked:
            return 0
        a = (user_content or "").strip()[:100]
        b = (mem.content or "").strip()[:100]
        if len(a) < 2 or len(b) < 2:
            return 0
        # M1-S7（2026-08-31）：成功判定放宽——相似命中照旧；非相似但属实质回复（非敷衍）也算"接住"
        #（治"用户明明回应了却被判失败、3 天后才重试"的 P-E1；敷衍词表极短无内容）
        if SequenceMatcher(None, a, b).ratio() < 0.15 and not _is_substantive_reply(a):
            return 0
        mem_id_for_reinforce = mem.id
    from app.memory.service import reinforce_memories
    # L2：主动复习成功通道（channel=review）——一次性事件按 tense 分流收口（S/次数封顶）
    await reinforce_memories([mem_id_for_reinforce], factor=REINFORCE_FACTOR_RETRIEVE * 4 / 3,
                             channel="review")
    _logger.info("Memory review success: char=%d mem=%d user replied related", character_id, mem_id_for_reinforce)
    return 1


# ── 记忆架构 v2.1 Phase 4b：情境驱动复习（与时间驱动并存）──
# 入口：chat_service 感知 deep/emotion 或命中进行中目标 → queue_contextual_review_for
# 执行：入队后延迟 _CONTEXTUAL_DELAY_SECONDS，由 arbiter collect_contextual_events 消费，
#       复用 run_memory_review（限额/间隔/免打扰/开关复检；用户活跃时 arbiter 拦截等待）
_contextual_pending: dict[int, dict] = {}  # char_id -> {user_id, memory_id, due_at}
_CONTEXTUAL_DELAY_SECONDS = 120


async def _msg_hits_goal(character_id: int, user_id: int, user_msg: str) -> bool:
    """用户消息与进行中目标/未完成话题重叠（话题命中）"""
    try:
        from app.models.memory import ConversationTopic
        from app.agent.topic_tracker import _overlap
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(ConversationTopic).where(
                    ConversationTopic.character_id == character_id,
                    ConversationTopic.status == "进行中",
                )
            )).scalars().all()
        return any(r.topic and _overlap(r.topic, user_msg) for r in rows)
    except Exception:
        return False


async def _pick_contextual_memory(character_id: int, user_id: int, user_msg: str) -> int | None:
    """情境复习候选记忆：优先进行中目标/未完成话题关联记忆，其次意义/情绪/关系重要记忆。"""
    try:
        from app.models.memory import ConversationTopic
        from app.models.memory import Memory
        from app.memory.service import _active_status_clause  # #70-C：仅 active（flag 关=永真）
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Memory)
                .where(
                    Memory.character_id == character_id,
                    Memory.is_archived == False,
                    Memory.is_pinned == False,
                    Memory.is_locked == False,
                    Memory.importance >= REVIEW_MIN_IMPORTANCE,
                    _active_status_clause(),
                )
                .order_by(Memory.importance.desc(), Memory.id.desc())
                .limit(20)
            )).scalars().all()
            if not rows:
                return None
            # L1（2026-09-09）：情境复习同样排除过期计划/瞬时状态（深夜/情绪场景不把旧计划翻出当新闻）
            try:
                if _exclude_flag_on():
                    rows = [m for m in rows
                            if classify_tense(m) != "transient"
                            and not (classify_tense(m) == "plan" and is_plan_expired(m))]
            except Exception:
                pass
            if not rows:
                return None
            topics = (await db.execute(
                select(ConversationTopic).where(
                    ConversationTopic.character_id == character_id,
                    ConversationTopic.status == "进行中",
                )
            )).scalars().all()
        from app.agent.topic_tracker import _overlap
        for m in rows:
            if m.why_it_matters is not None:
                return m.id
            if any(t.topic and _overlap(t.topic, m.content or "") for t in topics):
                return m.id
        for m in rows:
            if m.sub_type in ("emotion", "relationship"):
                return m.id
        return rows[0].id
    except Exception:
        return None


async def queue_contextual_review(character_id: int, user_id: int, memory_id: int) -> None:
    """入队情境复习（开关开启 + 未在排队中）；限额/间隔/DND 由 run_memory_review 执行时复检。"""
    try:
        if character_id in _contextual_pending:
            return
        if not await _memory_v2_enabled(character_id):
            return
        import time as _time
        _contextual_pending[character_id] = {
            "user_id": user_id, "memory_id": memory_id,
            "due_at": _time.time() + _CONTEXTUAL_DELAY_SECONDS,
        }
        _logger.info("Contextual review queued char=%d mem=%d", character_id, memory_id)
    except Exception as e:
        _logger.warning("Contextual review queue failed: %s", e)


async def queue_contextual_review_for(
    character_id: int, user_id: int, user_msg: str, perception: dict | None = None,
) -> None:
    """感知情境入口（chat_service 调用）：deep/emotion 意图或命中进行中目标 → 选记忆并入队。"""
    try:
        intent = (perception or {}).get("intent") or ""
        if intent not in ("deep", "emotion"):
            if not await _msg_hits_goal(character_id, user_id, user_msg):
                return
        memory_id = await _pick_contextual_memory(character_id, user_id, user_msg)
        if not memory_id:
            return
        await queue_contextual_review(character_id, user_id, memory_id)
    except Exception as e:
        _logger.warning("Contextual review trigger failed: %s", e)


async def collect_contextual_events() -> list[dict]:
    """arbiter 事件源：到期情境复习候选（priority=2，与状态触发同级）。"""
    import time as _time
    events = []
    for char_id, info in list(_contextual_pending.items()):
        if _time.time() >= info["due_at"]:
            _contextual_pending.pop(char_id, None)
            events.append({
                "type": "memory_review_contextual", "priority": 2,
                "candidate": {
                    "character_id": char_id, "user_id": info["user_id"],
                    "memory_id": info["memory_id"],
                },
            })
    return events
