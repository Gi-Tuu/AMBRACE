"""主动消息的「素材装载器」（A22 第九刀 · ③c 刀1，2026-10-03 自 `scheduling/message_generator.py` 逐字节搬入）。

边界：这里只放 `generate_proactive_event` 前置并发查询的那批 loader——画像 / persona 补充 /
天气 / 查岗 / 近期记忆 / 当前现状锚点。它们原先是**函数内的嵌套 def**（闭包捕获
`character_id` / `user_id` / `current_status` / `outreach_intent` / `outreach_plan`），
③c 把它们提到模块级并改成**显式传参**，为的是给 635 行的 `generate_proactive_event` 减重。
**函数本体与它所在的模块一律不动**：tests 有 2 处字符串路径打桩
（`"app.scheduling.message_generator.generate_proactive_event"`）⇒ 名字一挪就是 R1 那种
「桩静默失效＝测试绿着真调 LLM」。

搬家口径（与 ③a/③b/④a/④b/④c 一致，由 `tests/test_arbiter_seam.py` 的接缝守卫钉住）：
- 函数体**逐字节照搬**：只加参数、不改逻辑、不合并相似 loader；
- **R2**：`_load_recent_memories` 引用的 `RECALL_SHARED` / `format_memory_line` 是
  message_generator 的模块级名字，一律在函数体内 `from app.scheduling import
  message_generator as _mg` 现取 `_mg.<name>`（与 ③a/③b 的 `_mg._logger` 同一口径，
  一个名字只留一个解析点）；
- message_generator 侧**具名重导出**本模块 6 个名字，调用点写法不变；
- ⚠ 这 6 个 loader 在调用侧挤在**同一个 `asyncio.gather`** 里（连同 `_load_recent_reflection`
  等三个模块级 loader 共 **9 个协程**，按位置解包成 9 个名字）。这是 G-P2-2 的并发收益所在
  （原串行约 10 次 DB/外部调用）——**个数与顺序一个都不许变**，串行化＝主动链路时延与
  限流行为都变。守卫第 3 条专门钉这件事。
"""

async def _load_user_profile(user_id: int | None = None) -> str:
    try:
        from app.agent.user_profile import build_user_profile_text
        return await build_user_profile_text(user_id)
    except Exception:
        return ""

async def _load_persona_extra(character_id: int | None = None, user_id: int | None = None, *, outreach_intent: str | None = None, outreach_plan: dict | None = None) -> str:
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

async def _load_weather_line(character_id: int | None = None, user_id: int | None = None) -> str:
    if not character_id:
        return ""
    try:
        from app.application.weather_service import get_user_weather_line
        return await get_user_weather_line(user_id)
    except Exception:
        return ""

async def _load_check_in_line(character_id: int | None = None, user_id: int | None = None) -> str:
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

async def _load_recent_memories(character_id: int | None = None, user_id: int | None = None, current_status: str = "", *, outreach_intent: str | None = None, outreach_plan: dict | None = None) -> str:
    from app.scheduling import message_generator as _mg   # A22 ③c：mg 模块级名字一律调用时刻现取
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
                _line = _mg.format_memory_line(
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
        if outreach_intent == _mg.RECALL_SHARED:
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
            _line = _mg.format_memory_line(
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
async def _load_current_state_anchor(character_id: int | None = None, user_id: int | None = None) -> str:
    if not character_id or not user_id:
        return ""
    try:
        from app.memory.current_state import current_user_state_anchor
        return await current_user_state_anchor(
            character_id=character_id, user_id=user_id, include_profile_location=True)
    except Exception:
        return ""
