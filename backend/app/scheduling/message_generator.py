"""主动消息生成器 — 调用 LLM 生成主动问候/搭话（注入行为类型 + 当前时间）"""
import re

from app.utils.logger import get_logger
# A22 ③b：chat_completion / load_character_reasoning_level 定义在 _gen_with_reasoning（已下沉 message_llm）里
# 经 _mg 现取，tests 另有 10 处在 message_generator 上打这两个名字——绑定必须留在此命名空间（锚点，勿删）。
from app.agent.llm_client import chat_completion, load_character_reasoning_level  # noqa: F401
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

# A22 ③a（2026-10-02）：分段/评分等纯函数助手下沉 message_text，此处具名重导出。
from app.scheduling.message_text import (  # noqa: F401
    _apply_segment_guard, _conflicting_segment_indexes, _context_overlap_ratio, _has_invitation,
    _has_unclosed_delimiter, _has_visible_content, _naturalness_flag, _normalize_segments,
    _parrot_blocked, _quote_unbalanced, _scene_is_school, _segment_guard_on, _split_response_lines,
    _validate_segments, score_naturalness,
)
# 常量随迁（A22 ③a 同口径）：定义已搬入 message_text，tests/调用方仍按 message_generator 同名取用。
from app.scheduling.message_text import (  # noqa: F401
    _ABRUPT_OPENING_WORDS, _BANNED_WORDS, _FAMILY_SCENE_WORDS, _INVITATION_RE, _MAX_SEGMENT_LEN,
    _PARROT_MIN_CHARS, _PARROT_OVERLAP_THRESHOLD, _QUOTE_CLOSES, _QUOTE_PAIRS,
    _SCHOOL_SCENE_WORDS, _SEGMENT_MIN_LEN, _TEMPLATE_PHRASES, _VISIBLE_RE,
)

# A22 ③b（2026-10-02）：上下文装配下沉 message_context、生成/节日消息下沉 message_llm，此处具名重导出。
# 搬走代码引用「留原地」的名字（chat_completion / load_character_reasoning_level / _logger）与「同批搬走」的
# 名字（_gen_with_reasoning）时，一律在函数体经 _mg 现取；桩打在 message_generator 上，故此处必须重导出。
from app.scheduling.message_context import (  # noqa: F401
    _describe_now, _describe_idle, _load_recent_reflection, _load_scene_facts,
    _load_authoritative_user_location, two_pass_trace_allowed, _load_state_trace,
    _prepend_state_trace, _note_state_trace_injected, _note_state_trace_gate,
    _predict_notify_surface, _load_identity_block,
)
# 常量随迁（③a 同口径）：定义已搬入 message_context，tests/调用方仍按 message_generator 同名取用。
from app.scheduling.message_context import (  # noqa: F401
    TWO_PASS_TRACE_GRAY_CHARS, TWO_PASS_TRACE_ALL_FLAG, GATE_ROUTE, GATE_NOT_ALLOWED,
    GATE_EMPTY_TRACE, GATE_INJECTED, GATE_TRACE_ERROR,
)
from app.scheduling.message_llm import (  # noqa: F401
    _gen_with_reasoning, _proactive_self_search, generate_birthday_message,
    generate_anniversary_message, generate_holiday_message,
)


# ── 批次四（2026-09-16）：主动消息分块护栏（flag proactive_segment_guard，默认关）──
# 关＝逐字节现状；开＝①过滤残句/空块并合并碎片段 ②未闭合括号/引号不落刀
# ③生成前注入当前场景事实 + 生成后轻量现实校验（家庭场景词 vs 住校场景）。
_SEGMENT_MAX = 4       # 段数上限（与现状一致）


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

# ── 自然度评分（#28 ①，2026-08-24）：低优先级主动消息轻量自然度评估（纯规则，不调 LLM）──
# 低于重试阈值 → 追加修正要求重试 1 次（Feature Flag proactive_naturalness_score 开时）；
# 重试后仍低于跳过阈值 → 降级（跳过本次发送/改普通文案之外的最简行为=跳过）。
NATURALNESS_RETRY_THRESHOLD = 0.45
NATURALNESS_SKIP_THRESHOLD = 0.20

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
    session_id: int | None = None,        # 块 C M1：本轮目标会话（供生成前的通知面探针；None＝旧调用点）
    thought: str | None = None,           # 批 4 M2-b1：念头池取来的一条谈资（素材，None/空＝逐字节旧 prompt）
) -> list[str] | tuple[list[str], str]:
    """生成"一次事件"的消息文本，并按自然语句切成多段（按顺序逐条发送）。

    返回分段列表（1~4 段），按发送顺序排列；解析失败时回退为单段。
    return_reasoning=True 时返回 (segments, reasoning)（思考用于气泡折叠展示，2026-08-15）。
    仅用于私信主动消息；朋友圈走独立流程，不受影响。
    """
    scenario = _EVENT_DESC.get(behavior, _EVENT_DESC["default"])
    idle_desc = _describe_idle(idle_minutes, 2)
    # S1 第二步（2026-09-27）：主动链「角色自主搜索」开关（默认关=逐字节旧行为，读一次即可）。
    # 关 ⇒ 下面既不往 prompt 里开放 [SEARCH]，也不在首轮生成后调用 _proactive_self_search（不读 SEARCH、不加时延）。
    _self_search_on = False
    try:
        from app.agent.loop import AGENT_FLAGS as _af_pss
        _self_search_on = bool(_af_pss.get("proactive_self_search", False))
    except Exception:
        _self_search_on = False
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
            return await build_user_profile_text(user_id)
        except Exception:
            return ""

    async def _load_persona_extra() -> str:
        if not character_id:
            return ""
        if not outreach_intent:
            # ── 旧链路：保持原样 ──
            try:
                from app.agent.persona import assemble_persona_context
                _p = await assemble_persona_context(character_id, user_id)
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
            _p = await assemble_persona_context(character_id, user_id)
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
                _t = await load_fresh_active_topics_text(character_id, user_id)
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
            return await get_user_weather_line(user_id)
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
                _app = await get_check_in_foreground_app(user_id)
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
                from app.application.flag_service import resolve_flag
                # batch G：proactive_outreach_v2 按账号解析（缺 user_id 回落全局，fail-open）；
                # memory_chain_builder 非本批键，保持全局口径不变
                if (await resolve_flag("proactive_outreach_v2", user_id)) and _af.get("memory_chain_builder", False):
                    from app.memory.chain_builder import pick_recall_chain
                    _chain = await pick_recall_chain(character_id)
                    if _chain:
                        return _chain
                from app.memory import search_memories
                mems = await search_memories(character_id, query=current_status or "最近发生的事情", limit=4,
                                            user_id=user_id)  # A2 M0-4：透传调用者（hook ctx）
                _mem_lines = []
                for _m in mems:
                    # X-1（2026-08-18）：与主链路共用公共格式化函数（max_len=80）；
                    # 不传 reliability_score/contradiction_count（避免引入主链路才有的 UNVERIFIED/纠正后缀）。
                    # 2026-09-17 批次一（任务2/3）：补 status/type/sub_type——主动消息是最易「用旧现状续写」的
                    # 通道，必须让 stale 行带［往事/已过时］前缀、天然已发生来源带［往事］。
                    _line = format_memory_line(
                        {
                            "content": _m.get("content") or "",
                            "created_at": _m.get("created_at"),
                            "epistemic_status": _m.get("epistemic_status"),
                            "status": _m.get("status"),
                            "memory_type": _m.get("type"),
                            "sub_type": _m.get("sub_type"),
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
            mems = await search_memories(character_id, query=_mem_query, limit=3,
                                        user_id=user_id)  # A2 M0-4：透传调用者（hook ctx）
            _mem_lines = []
            for _m in mems:
                # format_memory_line 已带 [记录于 YYYY-MM-DD]，确保远期记忆带真实日期，不再谎称"近期"。
                # 2026-09-17 批次一（任务2/3）：补 status/type/sub_type，让 stale 行带［往事/已过时］、
                # 天然已发生来源带［往事］（主动消息不得把旧现状当现行事实续写）。
                _line = format_memory_line(
                    {
                        "content": _m.get("content") or "",
                        "created_at": _m.get("created_at"),
                        "epistemic_status": _m.get("epistemic_status"),
                        "status": _m.get("status"),
                        "memory_type": _m.get("type"),
                        "sub_type": _m.get("sub_type"),
                    },
                    max_len=80,
                )
                if _line:
                    _mem_lines.append(_line)
            return "\n".join(_mem_lines) if _mem_lines else ""
        except Exception:
            return ""

    # C3（2026-09-10）：用户当前现状权威锚点（三源聚合，默认无授权数据=空串、零行为变化）。
    # 主动消息通道没有 section_world.location，这里【带】User 城市（include_profile_location=True）。
    async def _load_current_state_anchor() -> str:
        if not character_id or not user_id:
            return ""
        try:
            from app.memory.current_state import current_user_state_anchor
            return await current_user_state_anchor(
                character_id=character_id, user_id=user_id, include_profile_location=True)
        except Exception:
            return ""

    # 批次四（2026-09-16）：当前场景事实（flag proactive_segment_guard 开才取；关=直接空串零开销）。
    # 现状锚点已在别处注入，这里补 user_facts.slot='location' 与用户作息（活跃时段），
    # 一并用于生成后的住校/家庭场景轻量校验。
    (user_profile, persona_extra, weather_line, check_in_line, recent_memories, reflection_line,
     state_anchor, scene_facts, user_loc_line) = (
        await _asyncio.gather(
            _load_user_profile(),
            _load_persona_extra(),
            _load_weather_line(),
            _load_check_in_line(),
            _load_recent_memories(),
            _load_recent_reflection(character_id),
            _load_current_state_anchor(),
            _load_scene_facts(user_id),
            # 批次二任务2.3/2.4：权威用户现状无条件前置拉取（低活跃角色同样覆盖，不依赖
            # 该角色自己的旧记忆/相似检索），供 prompt 与生成后一致性校验共用。
            _load_authoritative_user_location(user_id),
        )
    )
    # 现实约束校验用场景文本：现状锚点（含 location/living 槽）+ location 事实 + 作息 + 权威位置
    scene_text = "\n".join(b for b in (state_anchor, scene_facts, user_loc_line) if b)

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
    if user_loc_line:  # 批次二任务2.3：权威用户位置先声明（与旧记忆块一正一反）
        prompt += user_loc_line + "\n"
    if state_anchor:  # C3：先声明「TA 现在怎样」，紧接着的记忆块带［往事］标签，一正一反
        prompt += state_anchor
    # 批次四（2026-09-16，flag 开）：生成前注入当前场景事实 + 现实约束（禁止与用户现状冲突的家庭场景）
    if _segment_guard_on():
        _scene_bits = [b for b in (state_anchor, scene_facts) if b]
        if _scene_bits:
            prompt += (
                "\n当前场景事实（务必与之一致，不得与 TA 现在的居住/所处场景矛盾；"
                "例如 TA 住校、宿舍没有厨房，就不要说「锅里给你留着」「回家吃饭」这类家庭场景）：\n"
                + "\n".join(_scene_bits) + "\n"
            )
    if recent_memories:
        prompt += (
            f"\n以下是你与 TA 的过往记忆片段。带［往事］/［当时状态］/［旧安排·已过期］标签的属于过去发生的"
            f"事，不代表 TA 现在的状态；发起话题请基于当前时间与近况（保持这些记忆一致，不要与之矛盾）：\n{recent_memories}\n"
        )
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
    # 批 4 M2-b1（2026-10-01）：念头池素材注入（flag thought_pool_v1 关 ⇒ thought 恒 None ⇒ 本块不执行，
    # prompt 逐字节旧行为）。与 outreach_intent/outreach_plan 同性质——是**素材**不是规则：
    # 只给「另外可自然聊起的一件事」，不改判定、不改条数、不改时机；不写元叙述（无「N 条念头」类实现概念）。
    if thought:
        prompt += (
            f"\n另外，你心里还惦记着一件可以自然聊起的事：{str(thought).strip()[:120]}\n"
            "如果它与当下语境相称，就把它当作这条消息的谈资自然说出来；不相称就放着，别硬提，"
            "也别解释这件事是从哪来的。\n"
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
    # S1 第二步（proactive_self_search 开）：向主动链开放 [SEARCH]——除 [MEMO] 外再允许一个查证标记。
    # 关时不追加这段（逐字节旧 prompt）。
    if _self_search_on:
        prompt += (
            "\n【搜索能力】在 [MEMO] 之外，你还可以额外输出一次 [SEARCH]你想查证的内容[/SEARCH]"
            "（单独占一行）：如果你要说的这件事里有个没把握的事实/说法想先查一下，系统会真实搜索并把结果交回给你，"
            "你再决定怎么说。只在确实想查证时用（一条消息最多 1 次），不需要查证就绝对不要输出该标记；"
            "查到后用不上也可以只字不提，但无论如何不要说『我去搜一下/等着我去查』这类话。\n"
        )

    messages = [
        {"role": "system", "content": "你是一个真实的朋友，正在给好友发消息。按格式输出，每段一行。"},
        {"role": "user", "content": prompt},
    ]
    # ── 批 8 块 C M1（2026-10-01）：生成前的在线探针 ⇒ 本轮是否走通知面 ──
    # 只影响**生成时的 messages**；不改发送链、不落库、不进记忆。
    # flag 关 ⇒ _predict_notify_surface 不调探针、返回 False ⇒ compose 原样返回 ⇒ 逐字节旧 prompt。
    _notify_surface, _probe_source = _predict_notify_surface(session_id)
    _logger.info("notify_shape predicted=%s source=%s char=%s session=%s",
                 _notify_surface, _probe_source, character_id, session_id)
    from app.domain.message_shape import compose_notify_shape_messages

    messages = compose_notify_shape_messages(messages, notify_surface=_notify_surface)
    # two-pass POC（2026-09-23）：生成前拼一块确定性「现状 trace」并**前置**到系统块/长历史之前。
    # 双条件灰度（开关开 + 角色命中白名单）；trace 为空或构造异常 → 原样 messages（逐字旧行为）。
    # 判定点三态留痕（2026-09-26 派单 Part B）：注入留痕只在「命中且有 trace」时才有，缺它分不清
    # 是「没跑到」还是「跑到了但拼空」⇒ 这里每次调用补一条 two_pass_gate，生成结果逐字不变。
    if not two_pass_trace_allowed(character_id):
        _note_state_trace_gate(character_id, GATE_NOT_ALLOWED)
    else:
        trace_text, trace_ms, trace_err = await _load_state_trace(character_id, user_id)
        if trace_text:
            messages = _prepend_state_trace(messages, trace_text)
            _note_state_trace_injected(
                character_id, trace_text,
                sum(len(m.get("content") or "") for m in messages), trace_ms)
            _note_state_trace_gate(character_id, GATE_INJECTED,
                                   trace_len=len(trace_text), elapsed_ms=trace_ms)
        elif trace_err:
            _note_state_trace_gate(character_id, GATE_TRACE_ERROR)
        else:
            _note_state_trace_gate(character_id, GATE_EMPTY_TRACE)
    # 生成 + 规则校验：不通过则追加修正要求重试一次（2026-08-12）
    segments: list[str] = []
    ok = False
    last_reasoning = ""
    _guard_on = _segment_guard_on()   # 批次四：分块护栏（默认关=逐字节现状）
    _reality_conflict = False
    for attempt in range(2):
        response, last_reasoning = await _gen_with_reasoning(
            messages, character_id, user_id, temperature=0.9, max_tokens=512)
        response = (response or "").strip().strip('"').strip("'")

        # S1 第二步：首轮生成后若含 [SEARCH]，走一次角色自主搜索（regen 走 _gen_with_reasoning）。
        # 开关关 ⇒ 整段跳过（逐字节旧行为）；「本轮不产出消息」⇒ 直接 return []（走 segments 为空不发送那条路，
        # 不填占位、不发空串/省略号）；搜索失败/被节流 ⇒ _proactive_self_search 已回原候选，继续往下发原候选。
        if _self_search_on and attempt == 0:
            response, _self_no_msg = await _proactive_self_search(
                response, messages=messages, character_id=character_id, user_id=user_id)
            if _self_no_msg:
                _logger.info("Proactive self search: 角色选择不说，本轮不发消息 char=%s", character_id)
                return [] if not return_reasoning else ([], last_reasoning)

        if _guard_on:
            # 开：未闭合括号/引号不落刀（后续行并入当前段）
            segments = _split_response_lines(response)
        else:
            segments = [ln.strip().strip('"').strip("'") for ln in response.splitlines()]
            segments = [s for s in segments if s]

        # 兜底：模型没分行时按句子切（单段超 50 字触发；每段约 1~2 句，25 字左右）
        if len(segments) == 1 and len(segments[0]) > 50:
            parts = [x for x in _SENT_SPLIT.split(segments[0]) if x and x.strip()]
            merged: list[str] = []
            cur = ""
            for part in parts:
                if cur and len(cur) + len(part) > 25:
                    if _guard_on and _has_unclosed_delimiter(cur):
                        cur += part  # 批次四：切点在未闭合括号/引号里 → 不落刀
                        continue
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

        # 批次四（flag 开）：残句/空块过滤 + 现实约束校验（住校场景命中家庭场景词 → 拦截重试）
        _reality_conflict = False
        if _guard_on:
            segments, _conf = _apply_segment_guard(segments, scene_text)
            _reality_conflict = bool(_conf)
            if len(segments) > _SEGMENT_MAX:  # 段数上限保持 4
                segments = segments[:_SEGMENT_MAX - 1] + ["".join(segments[_SEGMENT_MAX - 1:])[:300]]
            if not segments:
                segments = ["……"]

        ok, cleaned = _validate_segments(segments)
        # #28 ①：低优先级主动消息自然度评分——Flag 开时低于重试阈值 → 追加修正要求重试一次
        nat_low = _naturalness_flag() and score_naturalness(segments) < NATURALNESS_RETRY_THRESHOLD
        # B1-③（方案 §5.3d）：计划要求抛回问题但生成结果没有 → 与自然度低分相同的"追加修正重试一次"
        need_question = bool((outreach_plan or {}).get("must_return_question"))
        no_question = need_question and not _has_invitation("".join(segments))
        # 批次二任务2.3（现状一致性校验）：把用户写到权威位置以外的城市 → 同分数不足一样重试一次
        _loc_conflict = None
        if user_loc_line:
            try:
                from app.memory.location_guard import location_conflict as _loc_conflict_fn
                _loc_conflict = _loc_conflict_fn("".join(segments), user_loc_line)
            except Exception:
                _loc_conflict = None
        if ok and not nat_low and not no_question and not _reality_conflict and not _loc_conflict:
            break
        segments = cleaned or segments
        if attempt == 0:
            if _loc_conflict:
                _auth_city = ""
                try:
                    from app.memory.location_guard import authoritative_city
                    _auth_city = authoritative_city(user_loc_line) or ""
                except Exception:
                    _auth_city = ""
                _hint = (
                    f"上一条输出与用户的真实位置冲突（用户现在在{_auth_city or 'TA 的常住地'}，"
                    f"不要写他在{_loc_conflict}）。请按权威位置重新生成，直接输出最终内容，不要解释。"
                )
            elif _reality_conflict:
                _hint = (
                    "上一条输出与 TA 当前的真实场景冲突（如 TA 住校、宿舍没有厨房，"
                    "就不要写「锅里给你留着」「回家吃饭」这类家庭场景）。请按 TA 的真实场景重新生成，"
                    "直接输出最终内容，不要解释。"
                )
            elif no_question:
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
    # 批次四（flag 开）：重试后仍冲突 → 丢弃冲突段（绝不把穿帮内容发出去）
    if _guard_on:
        segments, _conf_left = _apply_segment_guard(segments, scene_text, drop_conflicts=True)
        if _conf_left:
            _logger.info("Proactive segment guard: %d segment(s) dropped (reality conflict) char=%d",
                         len(_conf_left), character_id or 0)
        if not segments:
            segments = ["……"]
    # 批次二任务2.3（发送前现状一致性校验）：两轮后仍把用户写到别的城市 → 丢弃冲突段；
    # 全冲突则整条不发（宁可少发一条，也绝不把错误现状当此刻事实发出去）。
    if user_loc_line:
        try:
            from app.memory.location_guard import location_conflict as _loc_conflict_final
            _kept_segs = [s for s in segments if not _loc_conflict_final(s, user_loc_line)]
            if len(_kept_segs) != len(segments):
                _logger.info("Proactive location guard: %d segment(s) dropped char=%s",
                             len(segments) - len(_kept_segs), character_id)
            if not _kept_segs:
                try:
                    from app.memory.observability import obs_event
                    obs_event(character_id, "proactive_location_conflict_dropped",
                              {"authoritative": user_loc_line[:60]})
                except Exception:
                    pass
                return [] if not return_reasoning else ([], last_reasoning)
            segments = _kept_segs
        except Exception:
            pass
    # 批次四任务 3（2026-09-16，P1-5）：思考口径统一——主动链路 reasoning 与普通聊天
    # （nodes 挡位 2 / response_parser 挡位 1）走同一条上屏归一管线：第一人称内心独白，
    # 元话语黑名单（策略/长度/我决定加图/本轮提醒/规则说…）与提示词回声整句剔除。
    # 只影响 extra_meta.reasoning 上屏字段；失败静默（归一异常退回原始 reasoning，不阻断发送）。
    try:
        from app.agent.context.reasoning_prompt import normalize_reasoning_for_display as _norm_reasoning
        last_reasoning = _norm_reasoning(last_reasoning, character_name, user_name) or ""
    except Exception as _rnorm_e:
        _logger.warning("Proactive reasoning normalize failed: %s", _rnorm_e)
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
            if not user_id:
                # 多账号隔离（D 家族）：无归属时不登记查岗——check_in_requests.user_id 是 NOT NULL，
                # 旧写法 or 1 会把请求登记到 1 号账号的手机队列（1 号客户端会去采集快照）
                _logger.info("Proactive check-in skipped: no owner char=%d", character_id)
            else:
                await request_check_in(user_id, character_id)
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

    # 2026-09-13 真机反馈：正文只剩标点/省略号（如「……」）时不要发出去——
    # 主动搭话没有"等待中的用户"，发一条空话只会变成噪音和未读红点。
    # （交互回复仍保留「……」+ 灰字提示，走 degraded_reply 标记。）
    _joined_seg = "".join(segments)
    if not _has_visible_content(segments):
        _logger.info("Proactive event dropped: no visible content char=%d", character_id or 0)
        try:
            from app.memory.observability import obs_event
            obs_event(character_id, "proactive_empty_dropped", {"chars": len(_joined_seg)})
        except Exception:
            pass
        return [] if not return_reasoning else ([], last_reasoning)

    _logger.info("Proactive event segments for '%s': %d", character_name, len(segments))
    if return_reasoning:
        return segments, last_reasoning
    return segments
