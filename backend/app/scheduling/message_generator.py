"""主动消息生成器 — 调用 LLM 生成主动问候/搭话（注入行为类型 + 当前时间）"""
import re
from datetime import datetime, timezone, timedelta

from app.utils.logger import get_logger
from app.agent.llm_client import chat_completion, load_character_reasoning_level
from app.memory.format import format_memory_line  # X-1（2026-08-18）：记忆注入行公共格式化
# B1-③（2026-09-04，方案 §5.3）：主动接触意图层常量——分级/意图/转场句库，纯常量不入库
from app.domain.proactivity.outreach import (
    TIER_RECENT,
    TIER_STALE,
    TIER_COLD,
    CHECK_IN,
    SHARE_SELF,
    RECALL_SHARED,
    FOLLOW_UP,
    INTEREST_HOOK,
)

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

# 生成后规则校验（2026-08-12 → 2026-08-18 G-P2-3）：仅「身份暴露类」完整词组硬拦截，
# 去掉裸词 AI/模型/算法（避免误伤「AI 绘画」「这个模型跑得慢」等生活化表达）
_BANNED_WORDS = ("我是AI", "我是人工智能", "我是模型", "我是大模型", "我是一个AI", "我是一个模型",
                 "作为AI", "作为人工智能", "作为模型", "作为大模型",
                 "AI助手", "AI 助手", "AI模型", "AI 模型", "人工智能助手",
                 "根据系统", "系统判断", "系统提示", "系统指令")
_MAX_SEGMENT_LEN = 80

# ── 自然度评分（#28 ①，2026-08-24）：低优先级主动消息轻量自然度评估（纯规则，不调 LLM）──
# 低于重试阈值 → 追加修正要求重试 1 次（Feature Flag proactive_naturalness_score 开时）；
# 重试后仍低于跳过阈值 → 降级（跳过本次发送/改普通文案之外的最简行为=跳过）。
NATURALNESS_RETRY_THRESHOLD = 0.45
NATURALNESS_SKIP_THRESHOLD = 0.20

# 突兀开口词（仅判定消息开头前两字；命中即压分）——包含单人语气词/口头禅，防止生硬开口
_ABRUPT_OPENING_WORDS = ("哈", "啊", "呢", "哦", "嗯", "诶", "咦", "那个", "就是说", "然后啊")
# 模板/客套句式（命中越多越像模板；每命中一个 -0.35）
_TEMPLATE_PHRASES = (
    "今天天气", "跟你说个", "跟你说", "你知道吗", "我想你",
    "你在干嘛", "在忙吗", "最近怎么样", "分享一下", "记得吗", "猜猜",
)

# ── B1-③（方案 §5.3c）：分级 + 意图引导块 + 多样化转场句库（仅 outreach_intent 非空时启用）──
_TIER_GUIDE = {
    TIER_RECENT: "距上次聊天已过去几个小时：像朋友重新起头，可轻点一下上文，但别假设你们还停在原地。",
    TIER_STALE:  "已经一两天没聊：不要接着旧场景往下演；如要提旧事，只能用回忆口吻并点明是以前的事，主体是开启新的交流。",
    TIER_COLD:   "已经很久没聊：默认全新发起，不要主动续旧话题或旧剧情；除非是用户未兑现的承诺，否则不提旧场景。",
}
_TIER_GUIDE["continue"] = (
    "你们刚聊过不久：可以顺着最后话题，但不要重演已经结束的场景。"
)
_INTENT_GUIDE = {
    CHECK_IN:      "这次是自然关心：结合当前时段问一句他此刻的状态（吃饭/在忙/今天过得怎样），别客套、别查岗。",
    SHARE_SELF:    "这次先讲一件你此刻正在经历的生活小事（1-2句，有细节有感受），然后自然把话头抛给他。",
    RECALL_SHARED: "这次从你记得的、关于他的一件真事切入（必须带时间感，如'我记得你前几天说过…'），由这件事引出一个轻松问题。",
    FOLLOW_UP:     "这次跟进他真正提过且还没了结的计划/约定，只问进展，一句话，不催。",
    INTEREST_HOOK: "这次围绕他的兴趣/喜好开一个新话题，给一个容易接的话头（二选一式小提问也可以）。",
}
# 多样化转场/回忆开头（每次注入两三个并要求换着用，根治"对了，你上次说的"单一模板）
_OPENER_BANK = {
    RECALL_SHARED: ["我突然想起你之前说的…", "前阵子你提到…我刚又想起来了", "对了，记得你那时候…", "你之前不是说…嘛"],
    CHECK_IN:      ["", "这会儿在忙啥呢", "突然想问问你", "话说"],
    INTEREST_HOOK: ["刚看到个东西就想到你", "你不是一直喜欢…嘛", "好奇问下", ""],
    FOLLOW_UP:     ["你之前说的那个…后来怎么样了", "对了，你打算…的事推进了吗", "想起你之前在弄的…"],
    SHARE_SELF:    ["我刚…", "跟你说个小事", "我这边刚刚…", ""],
}

# 轻量"抛回问题"后检（方案 §5.3d）：中文问号/疑问助词判定是否留了话头
_INVITATION_RE = re.compile(r"(吗|嘛|呢|怎么样|如何|要不要|是不是|有没有|哪个|还是)")


def _has_invitation(text: str) -> bool:
    """是否在消息里留了可接的话头/问题（纯函数，可单测）。

    命中任意一个中文问号/英文问号，或含常见疑问助词（吗/嘛/呢/怎么样…）即判定有邀请；
    纯陈述句返回 False。
    """
    if not text:
        return False
    if "？" in text or "?" in text:
        return True
    return bool(_INVITATION_RE.search(text))


def _naturalness_flag() -> bool:
    """Feature Flag：低优先主动消息自然度评分（默认开；可经运行时开关改 False 回退为纯现状）。"""
    try:
        from app.agent import loop as _loop
        return bool(_loop.AGENT_FLAGS.get("proactive_naturalness_score", True))
    except Exception:
        return True


def score_naturalness(segments: "list[str] | str") -> float:
    """纯规则自然度评分（0..1，越高越自然）。不调 LLM。

    低优先级主动消息（motivation/渴望唤醒 等）生成后据此判断是否需要重试/降级。
    基于规则的简化版：长度分档 / 连续复读密度 / 突兀开口 / 模板句式（近似等权）。
    """
    if isinstance(segments, str):
        segments = [segments]
    text = "".join(s or "" for s in segments).strip()
    if not text:
        return 0.0
    n = len(text)

    # 1) 长度分档 0..1：过短/过长都不自然
    if n < 4:
        length = 0.0
    elif n < 10:
        length = 0.4
    elif n <= 160:
        length = 1.0
    elif n <= 220:
        length = 0.7
    elif n <= 300:
        length = 0.4
    else:
        length = 0.2

    # 2) 连续复读密度 0..1：最长连续相同字符段越短越自然（复读/刷屏压分）
    max_run = 1
    cur = 1
    prev_ch = text[0]
    for ch in text[1:]:
        if ch == prev_ch:
            cur += 1
            if cur > max_run:
                max_run = cur
        else:
            cur = 1
        prev_ch = ch
    density = min(1.0, (max_run - 1) / 3.0)
    repeat = 1.0 - density

    # 3) 突兀开头 0..1：命中突兀开口词 → 0
    head = text[:2]
    opening = 0.0 if any(head.startswith(w) for w in _ABRUPT_OPENING_WORDS) else 1.0

    # 4) 模板句式 0..1：命中越少越自然
    template_hits = sum(1 for p in _TEMPLATE_PHRASES if p in text)
    template = max(0.0, 1.0 - 0.35 * template_hits)

    # 加权合并（复读密度权重最高，最影响"像不像真人说话"）
    score = round(0.25 * length + 0.45 * repeat + 0.15 * opening + 0.15 * template, 4)
    # 过短消息（如单个语气词/“在吗”）对主动长文类消息不自然：直接封顶
    if n < 8:
        score = min(score, 0.30)
    return score


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


# A-C（2026-09-01）：字面重合守卫（防「复述上一条」兜底，真机 sam 案——LLM 逐句照抄最近 AI 对话回复）。
_PARROT_OVERLAP_THRESHOLD = 0.7  # 去空白后字符级重合率阈值（>70% 判定复述）
_PARROT_MIN_CHARS = 30           # 守卫适用最小有效字符数（主动消息 3~5 句，短于此不适用防误伤）
def _context_overlap_ratio(text: str, context: str) -> float:
    """segments 文本相对 last_context 的字面重合率（纯函数，便于测试）。

    归一化=去除全部空白字符；分母只取生成文本长度（difflib.SequenceMatcher 匹配块合计），
    衡量「生成文本有多大比例是照抄上下文」——context 更长不会稀释比例。
    """
    import re as _re
    from difflib import SequenceMatcher as _SM
    a = _re.sub(r"\s+", "", text or "")
    b = _re.sub(r"\s+", "", context or "")
    if not a or not b:
        return 0.0
    matched = sum(bl.size for bl in _SM(None, a, b).get_matching_blocks())
    return matched / len(a)


def _parrot_blocked(segments: list[str], last_context: str) -> tuple[bool, float]:
    """字面重合守卫（纯函数）：重合率超阈值且有效字符数达标 → 判定复述上一条。

    返回 (是否拦截, 重合率)；last_context 为空或生成文本有效字符不足 → (False, 0.0)。
    """
    joined = "\n".join(segments or [])
    if not (last_context or "").strip():
        return False, 0.0
    ratio = _context_overlap_ratio(joined, last_context)
    import re as _re
    _chars = len(_re.sub(r"\s+", "", joined))
    if _chars < _PARROT_MIN_CHARS:
        return False, ratio
    return ratio > _PARROT_OVERLAP_THRESHOLD, ratio


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
    outreach_intent: str | None = None,   # B1-③：本次接触意图（None=旧链路，flag 关零行为）
    outreach_plan: dict | None = None,    # B1-③：OutreachPlan 序列化（素材开关/检索 query/必须抛回）
) -> list[str] | tuple[list[str], str]:
    """生成"一次事件"的消息文本，并按自然语句切成多段（按顺序逐条发送）。

    返回分段列表（1~4 段），按发送顺序排列；解析失败时回退为单段。
    return_reasoning=True 时返回 (segments, reasoning)（思考用于气泡折叠展示，2026-08-15）。
    仅用于私信主动消息；朋友圈走独立流程，不受影响。
    """
    scenario = _EVENT_DESC.get(behavior, _EVENT_DESC["default"])
    idle_desc = _describe_idle(idle_minutes, 2)
    # B1-③（方案 §5.3e）：主动接触补"双向"导向（仅 outreach 新链路启用，flag 关零变化）
    if outreach_intent:
        scenario += " 并自然地把话题引向好友/向好友抛一个小问题，不要只自顾自说。"

    # G-P2-2（2026-08-18）：前置查询并行化——画像/persona/天气/查岗/记忆检索/反思 互不依赖，
    # 一次 asyncio.gather 并发执行（原串行约 10 次 DB/外部调用；逐项 try/except 异常隔离，
    # 任一项失败不影响其他项）；输出顺序与语义保持不变（天气/查岗仍走既有开关与缓存）。
    import asyncio as _asyncio

    async def _load_user_profile() -> str:
        try:
            from app.agent.user_profile import build_user_profile_text
            return await build_user_profile_text(user_id or 1)
        except Exception:
            return ""

    async def _load_persona_extra() -> str:
        if not character_id:
            return ""
        if not outreach_intent:
            # ── 旧链路：保持原样 ──
            try:
                from app.agent.persona import assemble_persona_context
                _p = await assemble_persona_context(character_id, user_id or 1)
                if not _p.get("cognitive"):
                    return ""
                _parts = []
                if _p.get("relationship_state"):
                    _parts.append(_p["relationship_state"])
                if _p.get("active_topics"):
                    _parts.append("你们进行中的话题（优先承接进行中的话题，别生硬）：\n" + _p["active_topics"])
                if _p.get("storyline_status") and _p["storyline_status"] != "无":
                    _parts.append(_p["storyline_status"])
                return "\n".join(_parts) if _parts else ""
            except Exception:
                return ""
        # ── B1-③ 新链路：按意图收敛素材（不再无差别注入剧情/进行中话题）──
        try:
            from app.agent.persona import assemble_persona_context
            _p = await assemble_persona_context(character_id, user_id or 1)
            if not _p:
                return ""
            _allow_topics = bool((outreach_plan or {}).get("allow_active_topics"))
            _allow_storyline = bool((outreach_plan or {}).get("allow_storyline"))
            _parts = []
            if _p.get("relationship_state"):
                _parts.append(_p["relationship_state"])
            # 仅 FOLLOW_UP 且话题新鲜才注入"进行中话题"，措辞从"优先承接"改为"可自然问进展"
            if _allow_topics:
                from app.agent.topic_tracker import load_fresh_active_topics_text
                _t = await load_fresh_active_topics_text(character_id, user_id or 1)
                if _t:
                    _parts.append("用户之前提过、且仍在时效内的事（可自然问一句进展，别生硬）：\n" + _t)
            # 仅"分享自己 + 刚分开"才带 AI 剧情状态；其余主动接触不背剧情
            if _allow_storyline and _p.get("storyline_status") and _p["storyline_status"] != "无":
                _parts.append(_p["storyline_status"])
            return "\n".join(_parts) if _parts else ""
        except Exception:
            return ""

    async def _load_weather_line() -> str:
        if not character_id:
            return ""
        try:
            from app.application.weather_service import get_user_weather_line
            return await get_user_weather_line(user_id or 1)
        except Exception:
            return ""

    async def _load_check_in_line() -> str:
        if not character_id:
            return ""
        try:
            from app.models.character import ProactiveSettings
            from sqlalchemy import select as _sa_select
            from app.db.database import async_session_factory as _asf
            async with _asf() as _db:
                _r = await _db.execute(
                    _sa_select(ProactiveSettings).where(ProactiveSettings.character_id == character_id)
                )
                _ps = _r.scalar_one_or_none()
            if _ps is not None and getattr(_ps, "check_in_enabled", False):
                from app.application.phone_service import get_check_in_foreground_app
                _app = await get_check_in_foreground_app(user_id or 1)
                if _app:
                    return (
                        f"你开启了「查岗」：好友现在正在用{_app}，可以像朋友一样自然关心他此刻在做什么"
                        "（不要像监控一样生硬，随口提一句就好）。"
                    )
                # 无新鲜快照：注入「查岗能力」标记说明，由 LLM 自主决定是否查岗（不强制）
                return (
                    "你有一个「查岗」能力：如果你此刻确实好奇好友在用手机做什么，可以在消息末尾单独一行输出 "
                    "[CHECK_IN] 标记（系统会去获取他的最新使用情况）；如果只是随口寒暄就不需要输出。\n"
                    "注意：你现在并不知道他在做什么，禁止编造；决定查岗时消息要自然（比如随口问一句「你在干嘛呢」），"
                    "不要提「查岗/标记/系统」；[CHECK_IN] 是唯一允许输出的标注。"
                )
            return ""
        except Exception:
            return ""

    async def _load_recent_memories() -> str:
        if not character_id:
            return ""
        if not outreach_intent:
            # ── 旧链路：保持原样 ──
            try:
                # B1-② C5（方案 §15）：RECALL_SHARED 改"捞一条链"——依赖第一部分 proactive_outreach_v2
                # + 建链器 memory_chain_builder 都已就绪时，优先用 pick_recall_chain 的链时间线作为回忆
                # 素材（时间锚点天然清晰、有起承）；两 flag 默认关 → 走原语义检索，行为与现状逐字节一致。
                from app.agent.loop import AGENT_FLAGS as _af
                if _af.get("proactive_outreach_v2", False) and _af.get("memory_chain_builder", False):
                    from app.memory.chain_builder import pick_recall_chain
                    _chain = await pick_recall_chain(character_id)
                    if _chain:
                        return _chain
                from app.memory import search_memories
                mems = await search_memories(character_id, query=current_status or "最近发生的事情", limit=4)
                _mem_lines = []
                for _m in mems:
                    # X-1（2026-08-18）：与主链路共用公共格式化函数（max_len=80）；
                    # 仅传既有字段（content/created_at/epistemic_status），不传 reliability_score/
                    # contradiction_count，避免引入主链路才有的 UNVERIFIED/纠正后缀（行为等价替换）
                    _line = format_memory_line(
                        {
                            "content": _m.get("content") or "",
                            "created_at": _m.get("created_at"),
                            "epistemic_status": _m.get("epistemic_status"),
                        },
                        max_len=80,
                    )
                    if _line:
                        _mem_lines.append(_line)
                return "\n".join(_mem_lines) if _mem_lines else ""
            except Exception:
                return ""
        # ── B1-③ 新链路：按意图检索 query（用户导向，而非 AI 状态）──
        try:
            _mem_query = (outreach_plan or {}).get("memory_query") or ""
            if not _mem_query:  # SHARE_SELF 等不需要检索用户记忆
                return ""
            # RECALL_SHARED 捞链衔接：链存在时用链（时间锚点清晰）、无链回退语义检索
            if outreach_intent == RECALL_SHARED:
                from app.agent.loop import AGENT_FLAGS as _af
                if _af.get("memory_chain_builder", False):
                    from app.memory.chain_builder import pick_recall_chain
                    _chain = await pick_recall_chain(character_id)
                    if _chain:
                        return _chain
            from app.memory import search_memories
            mems = await search_memories(character_id, query=_mem_query, limit=3)
            _mem_lines = []
            for _m in mems:
                # format_memory_line 已带 [记录于 YYYY-MM-DD]，确保远期记忆带真实日期，不再谎称"近期"
                _line = format_memory_line(
                    {
                        "content": _m.get("content") or "",
                        "created_at": _m.get("created_at"),
                        "epistemic_status": _m.get("epistemic_status"),
                    },
                    max_len=80,
                )
                if _line:
                    _mem_lines.append(_line)
            return "\n".join(_mem_lines) if _mem_lines else ""
        except Exception:
            return ""

    user_profile, persona_extra, weather_line, check_in_line, recent_memories, reflection_line = (
        await _asyncio.gather(
            _load_user_profile(),
            _load_persona_extra(),
            _load_weather_line(),
            _load_check_in_line(),
            _load_recent_memories(),
            _load_recent_reflection(character_id),
        )
    )

    # 注入当前状态与关系（保持事件连贯，避免与私聊状态矛盾）
    status_line = f"你当前的状态：{current_status}" if current_status else ""
    relation_line = f"你和好友的关系：{relationship_summary}" if relationship_summary else ""

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
    # P0-2（2026-08-24）：主动消息承接强制化——主指令前注入「最近聊了什么」承接块，要求承接现状、避免突兀换话题；
    # last_context 扩容（get_last_messages 现默认 10 条×120 字，此处上限 1200 字），仅有语境才开新话题。
    # B1-③（方案 §5.3c）：outreach 新链路用「分级+意图+多样化转场」块替换强制承接块；旧块（flag 关）原样保留。
    if outreach_intent:
        tier = (outreach_plan or {}).get("tier", TIER_RECENT)
        _openers = [x for x in _OPENER_BANK.get(outreach_intent, []) if x]
        import random as _r
        _opener_hint = (
            "可参考的自然开头（换着用，别每次一样，也可不用）：" + " / ".join(_r.sample(_openers, min(2, len(_openers))))
            if _openers else ""
        )
        prompt += (
            f"你们的最近聊天记录（只是背景，不是必须续写的剧本，且已过去一段时间）：\n{last_context[:800] or '（暂无）'}\n"
            f"{_TIER_GUIDE[tier]}\n{_INTENT_GUIDE[outreach_intent]}\n{_opener_hint}\n"
            "硬约束：①不要假装对话没中断、不要把旧场景当成此刻正在发生；②提到旧事必须用过去时间口吻；"
            "③不要逐字重复你以前说过的话；④这条消息最后要留一个让他容易接的话头/一个轻松问题（只问一个，不连环问、不审问）。\n"
        )
    else:
        prompt += (
            f"先看最近聊了什么：\n{last_context[:1200] or '（暂无最近聊天）'}\n"
            "新消息必须承接最近正在聊的或与当前语境一致；只有确认没有相关语境时才开新话题，"
            "且开头要自然接一句（如'对了，你上次说的……'）。\n"
            "注意：『最近聊了什么』里可能包含你刚回复过的话——承接话题时用自己的话重新说，"
            "绝不逐字重复你上一条消息的任何句子。\n"
        )
    prompt += (
        "请把这一件事写成一条连贯的消息（总共 3~5 句话），描述这件事的经过和你的感受，"
        "像真人发消息一样自然分成几小段，每段 1~2 句话。\n\n"
        "输出要求：每段单独占一行，段与段之间不要有空行，不要加序号、引号或任何标注（"
        "[MEMO]除外，见末尾说明）；段落顺序就是消息的发送顺序。\n"
    )
    # 主动备忘（2026-08-25）：允许 LLM 在主动消息里附带一条内部备忘（[MEMO]内容[/MEMO]，≤80字）。
    # 与主链路 context_builder 的 [MEMO] 口径对齐——AI 注意到值得记住的事/要点时主动记下，不发给好友。
    prompt += (
        "如果这段话里有真正值得记住的事/要点（比如你注意到好友的新动态、后续计划、重要约定），"
        "可在最后单独一行输出 [MEMO]内容[/MEMO]（≤80字，成对闭合，一次最多 1 条，日常闲聊不强制）。"
        "这是内部备忘，不会发给好友；除 [MEMO] 外不要加任何其他标注。\n"
    )
    if previous_messages:
        prompt += (
            f"\n以下是你最近主动发过/回复过的话（仅用来避免重复；除非用户回应了其中的新进展，"
            f"否则不要沿用、更不要照抄；承接同一话题时必须用自己的话重新表达）：\n{previous_messages[:400]}\n"
        )
    prompt += (
        "\n事实与推断：你记得的记忆里带 [INFERRED]/[PLANNED] 标记的属于推测/计划，提到时必须用'可能/好像'等不确定语气，"
        "不能说成已经发生的事实；没有依据的事不要编造。\n"
        "注意：整段内容是同一件事的连贯叙述，开头不要重复之前说过的话；"
        "不要用「作为AI...」这种语气。"
        "\n时空一致性（最重要，必须遵守）：你的叙述必须与「最近聊了什么」的现状一致——"
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
        # #28 ①：低优先级主动消息自然度评分——Flag 开时低于重试阈值 → 追加修正要求重试一次
        nat_low = _naturalness_flag() and score_naturalness(segments) < NATURALNESS_RETRY_THRESHOLD
        # B1-③（方案 §5.3d）：计划要求抛回问题但生成结果没有 → 与自然度低分相同的"追加修正重试一次"
        need_question = bool((outreach_plan or {}).get("must_return_question"))
        no_question = need_question and not _has_invitation("".join(segments))
        if ok and not nat_low and not no_question:
            break
        segments = cleaned or segments
        if attempt == 0:
            if no_question:
                _hint = (
                    "结尾请自然地留一个让好友容易接的话头/一个轻松问题（只问一个），不要自顾自说完，"
                    "直接输出最终内容。"
                )
            elif nat_low:
                _hint = (
                    "上一条输出自然度偏低。请重新生成一条更自然、更像真人随口说的话："
                    "避免复读堆砌语气词、避免模板化客套开头、长度适中（30~150字），"
                    "直接输出最终内容，不要解释。"
                )
            else:
                _hint = "上一条输出未通过校验（出现与AI身份相关的词或单条过长）。请重新生成：每条不超过80字，不要出现任何暴露AI身份的词。"
            messages = messages + [{"role": "user", "content": _hint}]
    # 终检：两轮后若仍含违禁词/超长，绝不再回退原文，用安全占位，保证不发出违规片段
    _ok, _final = _validate_segments(segments)
    if not _ok:
        segments = [s for s in _final if s] or ["……"]
    if not segments:
        segments = ["……"]
    # A-C（2026-09-01）：字面重合守卫——判定复述上一条则丢弃本次生成（fail-open：不发送不重试），
    # 记日志+obs_event；守卫自身异常不阻塞主动链路。
    try:
        _blocked, _ratio = _parrot_blocked(segments, last_context)
        if _blocked:
            _logger.info(
                "Proactive event dropped: %.0f%% overlap with last context char=%d",
                _ratio * 100, character_id or 0,
            )
            try:
                from app.memory.observability import obs_event
                obs_event(character_id, "proactive_parrot_blocked",
                          {"ratio": round(_ratio, 2), "chars": len("\n".join(segments))})
            except Exception:
                pass
            return [] if not return_reasoning else ([], last_reasoning)
    except Exception as _ov_e:
        _logger.warning("Proactive overlap guard failed (fail-open): %s", _ov_e)
    # #28 ①：自然度仍低于跳过阈值 → 降级（跳过本次发送；Flag 开时）
    if _naturalness_flag() and score_naturalness(segments) < NATURALNESS_SKIP_THRESHOLD:
        _logger.info("Proactive event degraded (low naturalness) char=%d", character_id)
        return [] if not return_reasoning else ([], last_reasoning)

    # 查岗自主触发（2026-08-15）：LLM 决定查岗输出 [CHECK_IN] → 登记请求（前端采集新快照），剥离标记
    try:
        if any("[CHECK_IN]" in s for s in segments):
            from app.application.phone_service import request_check_in
            await request_check_in(user_id or 1, character_id)
            _logger.info("Proactive check-in fired char=%d", character_id)
            segments = [s.replace("[CHECK_IN]", "").strip() for s in segments]
            segments = [s for s in segments if s]
            if not segments:
                segments = ["……"]
    except Exception as e:
        _logger.warning("Proactive check-in trigger failed: %s", e)

    # 主动备忘（2026-08-25）：LLM 在主动消息里输出 [MEMO]内容[/MEMO] → 落小手机备忘录并剥离标记。
    # 与主链路 context_builder 的 [MEMO] 口径对齐（记下值得记住的事/要点，不发给好友）；失败静默。
    try:
        from app.agent.actions import extract_memo as _extract_memo
        _joined = "\n".join(segments)
        _memo_text = _extract_memo(_joined)
        if _memo_text:
            from app.application.chat.tools import _execute_note_tool
            await _execute_note_tool("note_memo", {
                "character_id": character_id,
                "text": _memo_text,
                "author": character_name or "",
            }, character_id)
            _logger.info("Proactive memo saved char=%d text=%.30s", character_id, _memo_text)
            from app.agent.actions import strip_actions as _strip_actions
            _seg_stripped = [_strip_actions(s).strip() for s in segments]
            _seg_stripped = [s for s in _seg_stripped if s]
            if _seg_stripped:
                segments = _seg_stripped
    except Exception as e:
        _logger.warning("Proactive memo save failed: %s", e)

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
    if isinstance(response, tuple):  # 2026-08-20 七夕死循环修复：_gen_with_reasoning 恒返回 (content, reasoning)
        response = response[0] or ''
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
    if isinstance(response, tuple):  # 2026-08-20 七夕死循环修复：_gen_with_reasoning 恒返回 (content, reasoning)
        response = response[0] or ''
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
    if isinstance(response, tuple):  # 2026-08-20 七夕死循环修复：_gen_with_reasoning 恒返回 (content, reasoning)
        response = response[0] or ''
    return response.strip().strip('"').strip("'")