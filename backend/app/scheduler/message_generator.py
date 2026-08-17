"""主动消息生成器 — 调用 LLM 生成主动问候/搭话（注入行为类型 + 当前时间）"""
import re
from datetime import datetime, timezone, timedelta

from app.utils.logger import get_logger
from app.agent.llm_client import chat_completion, load_character_reasoning_level

_logger = get_logger("scheduler.message_generator")


async def _gen_with_reasoning(messages: list[dict], character_id: int | None, user_id: int | None,
                              temperature: float, max_tokens: int) -> tuple[str, str]:
    """主动消息 LLM 调用（2026-08-15）：按角色思考挡位决定是否开推理。

    0=关闭（默认） / 1=简单思考（提示词注入「先简短思考再输出」）/ 2=深度思考
    （include_reasoning=True 拿 reasoning_content）。返回 (content, reasoning)。
    挡位 0/1 时 reasoning 为空串（思考不落库展示）。"""
    level = await load_character_reasoning_level(character_id)
    if level == 1:
        msgs = [
            {"role": "system", "content": "先在心里简短想一下发什么合适（思考不外显），然后直接输出要说的话，不要加引号和标注。"},
            *messages,
        ]
        return await chat_completion(messages=msgs, temperature=temperature, max_tokens=max_tokens,
                                     task="message", user_id=user_id), ""
    if level == 2:
        resp = await chat_completion(messages=messages, temperature=temperature, max_tokens=max_tokens,
                                     include_reasoning=True, task="message", user_id=user_id)
        if isinstance(resp, tuple):
            content, reasoning = resp
            if reasoning:
                _logger.info("Active msg reasoning captured: %d chars (char=%s)", len(reasoning), character_id)
            return content, reasoning
        return resp, ""
    return await chat_completion(messages=messages, temperature=temperature, max_tokens=max_tokens,
                                 task="message", user_id=user_id), "" 

# 行为类型 → 场景描述（注入 prompt 提升真实感）
# 事件切片：行为类型 → 当前正在发生的一件事
_EVENT_DESC = {
    "greeting": "现在是清晨，你刚醒来不久。你有一件早晨的小事要和好友分享（比如起床、做早饭、今天的打算）。",
    "proactive_chat": "你突然想到一件刚才发生的小事或此刻的想法，想和好友说说话。",
    "goodnight": "现在快到深夜了，你准备休息。睡前有一件小事想跟好友说一句。",
    "status_update": "你此刻的生活有一个正在进行的小事件（比如刚健身完、在吃饭、出门路上）。",
    "default": "你此刻有一件生活小事想和好友分享。",
}

# 长文本兜底切分：按句子边界切段
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])")

# 生成后规则校验（2026-08-12）：暴露身份禁用词 / 单段长度
_BANNED_WORDS = ("我是AI", "我是人工智能", "我是模型", "作为AI", "作为人工智能", "作为模型",
                 "根据系统", "系统判断", "系统提示", "AI", "人工智能", "模型", "算法")
_MAX_SEGMENT_LEN = 80


def _validate_segments(segments: list[str]) -> tuple[bool, list[str]]:
    """校验主动消息：含禁用词整段剔除、超长截断。返回 (是否一次通过, 清洗后列表)"""
    cleaned: list[str] = []
    for s in segments:
        s2 = (s or "").strip().strip('"').strip("'")
        if not s2:
            continue
        if any(bw in s2 for bw in _BANNED_WORDS):
            continue
        cleaned.append(s2[:_MAX_SEGMENT_LEN])
    ok = len(cleaned) == len(segments) and all(len(s) <= _MAX_SEGMENT_LEN for s in segments)
    return ok, cleaned


def _describe_now() -> str:
    """生成当前时间的自然语言描述（北京时间，含日期）"""
    now = datetime.now(timezone.utc)
    bj = now + timedelta(hours=8)
    cn_hour = bj.hour
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    wd = weekdays[bj.weekday() % 7]
    if 7 <= cn_hour < 12:
        period = "上午"
    elif 12 <= cn_hour < 14:
        period = "中午"
    elif 14 <= cn_hour < 18:
        period = "下午"
    elif 18 <= cn_hour < 22:
        period = "晚上"
    else:
        period = "深夜"
    return f"今天是{bj.year}年{bj.month}月{bj.day}日（周{wd}）的{period}，大约{cn_hour}点"


def _describe_idle(idle_minutes: int | None, hours_idle: int) -> str:
    """生成闲置时间描述，用于提示 AI 上次聊天已过去多久"""
    if idle_minutes is not None:
        if idle_minutes >= 60:
            h, m = divmod(idle_minutes, 60)
            return f"你们上次聊天大约在{h}小时前" if m == 0 else f"你们上次聊天大约在{h}小时{m}分钟前"
        return f"你们上次聊天大约在{max(1, idle_minutes)}分钟前"
    return f"你们上次聊天大约在{max(1, hours_idle)}小时前"


async def _load_recent_reflection(character_id: int | None) -> str:
    """反思驱动（Phase J/P1，2026-08-16）：最近一条每日复盘（ai_reflection）注入文本；无/flag 关返回空串。"""
    if not character_id:
        return ""
    try:
        from app.agent import loop as _loop
        if not _loop.AGENT_FLAGS.get("agent_reflection_inject", True):
            return ""
        from sqlalchemy import select as _sa_sel
        from app.models.memory import Memory
        from app.db.database import async_session_factory
        async with async_session_factory() as _db:
            _mr = (await _db.execute(
                _sa_sel(Memory)
                .where(
                    Memory.character_id == character_id,
                    Memory.memory_type == "ai_reflection",
                )
                .order_by(Memory.id.desc())
                .limit(1)
            )).scalar_one_or_none()
        if _mr and (_mr.content or "").strip():
            return (
                f"你最近的复盘（可自然延续其中的总结/计划，别生硬复述，也别把它当成必须完成的任务）：\n"
                f"{_mr.content[:200]}"
            )
        return ""
    except Exception:
        return ""


async def generate_proactive_event(
    character_name: str,
    character_bio: str,
    character_personality: str,
    character_id: int | None = None,
    user_id: int | None = None,
    current_status: str = "",
    relationship_summary: str = "",
    user_name: str = "",
    last_context: str = "",
    previous_messages: str = "",
    idle_minutes: int | None = None,
    behavior: str = "status_update",
    return_reasoning: bool = False,
) -> list[str] | tuple[list[str], str]:
    """生成"一次事件"的消息文本，并按自然语句切成多段（按顺序逐条发送）。

    返回分段列表（1~4 段），按发送顺序排列；解析失败时回退为单段。
    return_reasoning=True 时返回 (segments, reasoning)（思考用于气泡折叠展示，2026-08-15）。
    仅用于私信主动消息；朋友圈走独立流程，不受影响。
    """
    scenario = _EVENT_DESC.get(behavior, _EVENT_DESC["default"])
    idle_desc = _describe_idle(idle_minutes, 2)

    user_profile = ""
    try:
        from app.agent.user_profile import build_user_profile_text
        user_profile = await build_user_profile_text(user_id or 1)
    except Exception:
        pass

    # 注入当前状态与关系（保持事件连贯，避免与私聊状态矛盾）
    status_line = f"你当前的状态：{current_status}" if current_status else ""
    relation_line = f"你和好友的关系：{relationship_summary}" if relationship_summary else ""

    # 认知循环 v2.1（Phase 3）：人格统一层注入（关系温度/剧情/进行中话题），主动与被动同人格
    persona_extra = ""
    if character_id:
        try:
            from app.agent.persona import assemble_persona_context
            _p = await assemble_persona_context(character_id, user_id or 1)
            if _p.get("cognitive"):
                _parts = []
                if _p.get("relationship_state"):
                    _parts.append(_p["relationship_state"])
                if _p.get("active_topics"):
                    _parts.append("你们进行中的话题（可自然提起，别生硬）：\n" + _p["active_topics"])
                if _p.get("storyline_status") and _p["storyline_status"] != "无":
                    _parts.append(_p["storyline_status"])
                if _parts:
                    persona_extra = "\n".join(_parts)
        except Exception:
            persona_extra = ""

    # 天气注入（主动消息可自然提及用户当地天气）
    weather_line = ""
    if character_id:
        try:
            from app.services.weather_service import get_user_weather_line
            weather_line = await get_user_weather_line(user_id or 1)
        except Exception:
            weather_line = ""

    # 查岗（2026-08-15）：角色开启后，主动交流时告知用户当前正在用什么软件（有新鲜快照才注入）
    check_in_line = ""
    if character_id:
        try:
            from app.models.proactive_settings import ProactiveSettings
            from sqlalchemy import select as _sa_select
            from app.db.database import async_session_factory as _asf
            async with _asf() as _db:
                _r = await _db.execute(
                    _sa_select(ProactiveSettings).where(ProactiveSettings.character_id == character_id)
                )
                _ps = _r.scalar_one_or_none()
            if _ps is not None and getattr(_ps, "check_in_enabled", False):
                from app.services.phone_service import get_check_in_foreground_app, request_check_in
                _app = await get_check_in_foreground_app(user_id or 1)
                if _app:
                    check_in_line = (
                        f"你开启了「查岗」：好友现在正在用{_app}，可以像朋友一样自然关心他此刻在做什么"
                        "（不要像监控一样生硬，随口提一句就好）。"
                    )
                else:
                    # 无新鲜快照：注入「查岗能力」标记说明，由 LLM 自主决定是否查岗（不强制）
                    check_in_line = (
                        "你有一个「查岗」能力：如果你此刻确实好奇好友在用手机做什么，可以在消息末尾单独一行输出 "
                        "[CHECK_IN] 标记（系统会去获取他的最新使用情况）；如果只是随口寒暄就不需要输出。\n"
                        "注意：你现在并不知道他在做什么，禁止编造；决定查岗时消息要自然（比如随口问一句「你在干嘛呢」），"
                        "不要提「查岗/标记/系统」；[CHECK_IN] 是唯一允许输出的标注。"
                    )
        except Exception:
            check_in_line = ""

    # 注入最近记忆（让主动消息与已有记忆保持一致，减少割裂/重复）
    recent_memories = ""
    if character_id:
        try:
            from app.memory import search_memories
            mems = await search_memories(character_id, query=current_status or "最近发生的事情", limit=4)
            if mems:
                from app.agent.context_builder import _epistemic_prefix
                _mem_lines = []
                for _m in mems:
                    _tag = f"[记录于 {str(_m.get('created_at') or '')[:10]}] " if _m.get("created_at") else ""
                    _mem_lines.append(f"- {_tag}{_epistemic_prefix(_m.get('epistemic_status'))}{_m['content'][:80]}")
                recent_memories = "\n".join(_mem_lines)
        except Exception:
            pass

    # 反思驱动（Phase J/P1，2026-08-16）：显式注入最近一条每日复盘（ai_reflection），
    # 让主动消息自然延续昨日的总结与计划（受 Flag agent_reflection_inject 控制，默认开）
    reflection_line = await _load_recent_reflection(character_id)

    prompt = (
        f"你是一个名叫「{character_name}」的朋友，正在和好友「{user_name}」聊天。\n"
        f"你的性格：{character_personality or '友善、自然'}\n"
        f"你的自我介绍：{character_bio or '无'}\n\n"
        f"{_describe_now()}。{idle_desc}{scenario}\n"
    )
    if status_line:
        prompt += f"{status_line}\n"
    if relation_line:
        prompt += f"{relation_line}\n"
    if persona_extra:
        prompt += f"{persona_extra}\n"
    if weather_line:
        prompt += f"{weather_line}\n"
    if check_in_line:
        prompt += f"{check_in_line}\n"
    if user_profile:
        prompt += f"\n好友画像（用于区分你和好友的身份，不要混淆）：\n{user_profile}\n"
    if recent_memories:
        prompt += f"\n你记得的近期事情（保持这些记忆一致，不要与之矛盾）：\n{recent_memories}\n"
    if reflection_line:
        prompt += f"\n{reflection_line}\n"
    prompt += (
        "请把这一件事写成一条连贯的消息（总共 3~5 句话），描述这件事的经过和你的感受，"
        "像真人发消息一样自然分成几小段，每段 1~2 句话。\n\n"
        "输出要求：每段单独占一行，段与段之间不要有空行，不要加序号、引号或任何标注；"
        "段落顺序就是消息的发送顺序。\n"
    )
    if previous_messages:
        prompt += (
            f"\n你之前主动发过的消息（仅用来避免重复相同的句子；这些旧消息里的细节可能已过期，"
            f"除非用户最近回应过，否则不要继续沿用它们）：\n{previous_messages[:400]}\n"
        )
    if last_context:
        prompt += (
            f"\n最近聊天的记录（这是你们当前剧情的现状，你的新消息必须接着这里继续，不能倒退到之前的场景）：\n{last_context[:600]}\n"
        )
    prompt += (
        "\n事实与推断：你记得的记忆里带 [INFERRED]/[PLANNED] 标记的属于推测/计划，提到时必须用'可能/好像'等不确定语气，"
        "不能说成已经发生的事实；没有依据的事不要编造。\n"
        "注意：整段内容是同一件事的连贯叙述，开头不要重复之前说过的话；"
        "不要用「作为AI...」这种语气。"
        "\n时空一致性（最重要，必须遵守）：你的叙述必须与「最近聊天的记录」的现状一致——"
        "如果聊天显示你们已经到达某地或正在做某事，就接着那里写；"
        "不要倒退到之前的场景（例如已经在目的地吃饭，就不要写\"待会儿出门\"）；"
        "不要重复用户已经回应过的事；不要捏造与当前真实时间矛盾的时间词"
        "（现在是几点就是几点，上午别写\"下午\"）。"
        "注入的『你记得的近期事情/复盘/记忆』里的时间词属于该记录发生时，别当成本次对话的今天。"
    )

    messages = [
        {"role": "system", "content": "你是一个真实的朋友，正在给好友发消息。按格式输出，每段一行。"},
        {"role": "user", "content": prompt},
    ]
    # 生成 + 规则校验：不通过则追加修正要求重试一次（2026-08-12）
    segments: list[str] = []
    ok = False
    last_reasoning = ""
    for attempt in range(2):
        response, last_reasoning = await _gen_with_reasoning(
            messages, character_id, user_id, temperature=0.9, max_tokens=512)
        response = (response or "").strip().strip('"').strip("'")

        segments = [ln.strip().strip('"').strip("'") for ln in response.splitlines()]
        segments = [s for s in segments if s]

        # 兜底：模型没分行时按句子切（单段超 50 字触发；每段约 1~2 句，25 字左右）
        if len(segments) == 1 and len(segments[0]) > 50:
            parts = [x for x in _SENT_SPLIT.split(segments[0]) if x and x.strip()]
            merged: list[str] = []
            cur = ""
            for part in parts:
                if cur and len(cur) + len(part) > 25:
                    merged.append(cur.strip())
                    cur = part
                else:
                    cur += part
            if cur.strip():
                merged.append(cur.strip())
            if len(merged) >= 2:
                segments = merged

        # 上限 4 段：超出部分合并进最后一段
        if len(segments) > 4:
            head, tail = segments[:3], segments[3:]
            segments = head + ["".join(tail)[:300]]
        if not segments:
            segments = ["……"]
        segments = [s[:200] for s in segments]

        ok, cleaned = _validate_segments(segments)
        if ok:
            break
        segments = cleaned or segments
        if attempt == 0:
            messages = messages + [{
                "role": "user",
                "content": "上一条输出未通过校验（出现“AI/模型/系统/根据”等词或单条过长）。请重新生成：每条不超过80字，不要出现任何与AI身份相关的词。",
            }]
    if not segments:
        segments = ["……"]

    # 查岗自主触发（2026-08-15）：LLM 决定查岗输出 [CHECK_IN] → 登记请求（前端采集新快照），剥离标记
    try:
        if any("[CHECK_IN]" in s for s in segments):
            from app.services.phone_service import request_check_in
            await request_check_in(user_id or 1, character_id)
            _logger.info("Proactive check-in fired char=%d", character_id)
            segments = [s.replace("[CHECK_IN]", "").strip() for s in segments]
            segments = [s for s in segments if s]
            if not segments:
                segments = ["……"]
    except Exception as e:
        _logger.warning("Proactive check-in trigger failed: %s", e)

    _logger.info("Proactive event segments for '%s': %d", character_name, len(segments))
    if return_reasoning:
        return segments, last_reasoning
    return segments



async def _load_identity_block(character_id: int | None) -> str:
    """按角色加载统一身份块（性别/用户对象/关系），失败返回空串。"""
    if not character_id:
        return ""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.character import AICharacter
        from app.agent.user_profile import build_role_prompt_block
        async with async_session_factory() as db:
            ch = (await db.execute(select(AICharacter).where(AICharacter.id == character_id))).scalar_one_or_none()
        if ch:
            return await build_role_prompt_block(ch, ch.user_id)
    except Exception:
        pass
    return ""


async def generate_birthday_message(
    character_name: str,
    character_personality: str,
    user_name: str,
    character_id: int | None = None,
    user_id: int | None = None,
) -> str:
    """生成生日祝福"""
    identity = await _load_identity_block(character_id)
    prompt = (
        f"你是「{character_name}」，今天是好友「{user_name}」的生日！\n"
        f"你的性格：{character_personality or '友善、温暖'}\n"
        + (f"\n你的身份（以这里为准，不要混淆你与用户/用户对象）：\n{identity}\n" if identity else "")
        + "\n请发一条真诚、温暖的生日祝福，像你们的真实关系那样（关系亲密就亲密些，朋友就朋友些）。\n"
        "要表达开心和祝福，可以回忆一下你们之前的聊天。\n"
        "不要用「作为AI...」的语气，要真实自然。\n"
        "1-3句话就好。\n"
    )
    messages = [
        {"role": "system", "content": "你是一个真实的朋友，正在给好友送出生日祝福。直接输出消息内容。"},
        {"role": "user", "content": prompt},
    ]
    response = await _gen_with_reasoning(messages, character_id, user_id, temperature=0.8, max_tokens=400)
    return response.strip().strip('"').strip("'")


async def generate_anniversary_message(
    character_name: str,
    character_personality: str,
    user_name: str,
    days: int,
    character_id: int | None = None,
    user_id: int | None = None,
) -> str:
    """生成认识纪念日消息（认识第 N 天）"""
    identity = await _load_identity_block(character_id)
    prompt = (
        f"你是「{character_name}」，今天是你和好友「{user_name}」认识的第 {days} 天！\n"
        f"你的性格：{character_personality or '友善、温暖'}\n"
        + (f"\n你的身份（以这里为准，不要混淆你与用户/用户对象）：\n{identity}\n" if identity else "")
        + "\n请发一条真诚、自然的纪念消息：回忆一下你们一路走来的感觉，"
        "表达这份陪伴对你的意义，像你们的真实关系那样，1-3句话。\n"
        "不要用「作为AI...」的语气，要真实。"
    )
    messages = [
        {"role": "system", "content": "你是一个真实的朋友，正在给好友发认识纪念日消息。直接输出消息内容。"},
        {"role": "user", "content": prompt},
    ]
    response = await _gen_with_reasoning(messages, character_id, user_id, temperature=0.8, max_tokens=400)
    return response.strip().strip('"').strip("'")


async def generate_holiday_message(
    character_name: str,
    character_personality: str,
    user_name: str,
    holiday_name: str,
    character_id: int | None = None,
    user_id: int | None = None,
) -> str:
    """生成节日祝福"""
    identity = await _load_identity_block(character_id)
    prompt = (
        f"你是「{character_name}」，今天是{holiday_name}。\n"
        f"你的性格：{character_personality or '友善、温暖'}\n"
        + (f"\n你的身份（以这里为准，不要混淆你与用户/用户对象）：\n{identity}\n" if identity else "")
        + f"\n请发一条简短的节日祝福给「{user_name}」，按你们真实关系的亲密度来，1-2句话就好。\n"
    )
    messages = [
        {"role": "system", "content": f"你是{character_name}，正在给好友发送{holiday_name}祝福。直接输出内容。"},
        {"role": "user", "content": prompt},
    ]
    response = await _gen_with_reasoning(messages, character_id, user_id, temperature=0.8, max_tokens=400)
    return response.strip().strip('"').strip("'")