"""build_context_legacy —— 上下文最终组装器（F3，2026-08-31 自 context_builder.py 迁入）。

- 现状（A22 ⑤-b/⑤-c，2026-10-03）：注册表是唯一装配入口，本函数接收它算好的分区值做最终组装；
  `agent_context_registry` flag 与「注册表未产出则在此重算一遍」的 13 段内联兜底均已删除（判据与
  语义变化见 build_context_legacy 函数体注释）。`_section_values=None` 时仍走自算路径
  （tests 的 `_run_legacy_pure*` 夹具这样调用）。
- 接缝已摘除（2026-09-02）：本模块改为显式 import 依赖（见下方），不再经 _sync_seams
  把 context_builder 命名空间同步进 globals；裸名字静态可解析，可被 ruff 检查。
- 整体退场（本文件 + context_builder 的薄壳委托）尚未拍板，勿据此删除。
"""
from datetime import datetime, timezone
from sqlalchemy import select

from app.utils.logger import get_logger
from app.agent.context_builder import (
    AICharacter,
    ProactiveSettings,
    SYSTEM_PROMPT_TEMPLATE,
    _EST_CHARS_PER_TOKEN,
    _SECTION_QUOTA_TOKENS,
    _apply_system_total_quota,
    _build_mcp_resources_text,
    _build_mcp_tools_text,
    _build_retrieved_memory_lines,
    _build_user_info,
    _bump_memory_round,
    _clip_text_to_quota,
    _enforce_user_message_last,
    _inject_core_anchors_loops,
    _is_hot_character,
    _trim_limits,
    async_session_factory,
    gender_cn,
)

_logger = get_logger("context.legacy")

# 宿主 context_inject 拿不到 caller 的告警去重（与 registry._warned_no_caller 同法，防 hot path 刷屏）
_warned_inject_no_caller: set[str] = set()


def _warn_context_inject_no_caller_once() -> None:
    """装配链缺 user_id → 不向插件分发 context_inject；每个进程只告警一次（不静默丢注入）。"""
    if "context_inject" in _warned_inject_no_caller:
        return
    _warned_inject_no_caller.add("context_inject")
    _logger.warning(
        "宿主 context_inject 拿不到调用者（state 无 user_id）→ 不向插件分发（fail-closed，"
        "不再兜底成 1 号账号）；若属误伤请修上游 state 透传，勿在此补默认值"
    )


async def build_context_legacy(state: dict, *, stream: bool | None = None, _section_values: dict | None = None, _trim: dict | None = None) -> dict:
    """上下文最终组装器：构建完整的上下文 prompt（近1天完整消息 + 更早日概要 + 朋友圈）。

    `stream`（P2-A）：显式标记流式模式；None 时从 state 推断（state["stream_sink"] 非空 = 流式）。
    流式模式下 MCP 工具声明不注入（见 _build_mcp_tool_declarations）。

    `_section_values`（注册表内部）：由 context.build_context 走注册表算出的分区值（template 槽 /
    append 块），含**所有已执行** section 的键（结果为空的键也写入，可能为空串/空列表）。提供时
    这些键的值由下方「步骤5」覆盖块填充；未执行的键（section 抛异常/被关闭）不写入，落各段的默认值
    （⑤-c 起不再由这里重算）。记忆轮次 +1 已在入口由注册表路径执行。

    `_trim`（注册表内部）：热度裁剪参数（含 _is_hot_character 近 7 天消息数查询）。注册表路径
    已通过 `_resolve_trim` 算好并注入，此处直接复用，避免注册表与组装器各查一次；缺省（None，
    即 `_section_values` 也没传的自算调用）时照常自算。

    本函数为聚合组装：占位符语义保持（moments 无内容仍「暂无」、pets 仍「无」、world_facts 仍「无」），
    注册表正常产出时装配结果与 ⑤-c 之前逐字节一致；某段未产出时该段落默认值，缺口由 WARNING
    日志与 `context_section_failed` 留痕（fail-visible）。
    """
    # 接缝已摘除（2026-09-02）：依赖名字已显式 import，无需 _sync_seams 自同步。
    # P2-A：流式模式判定（LangGraph 只传 state，从 state["stream_sink"] 推断；可显式覆盖）
    _is_stream_ctx = bool(state.get("stream_sink")) if stream is None else bool(stream)
    # P3-1（2026-08-31）：注册表已执行的 section 键集合（含结果为空的键）。已执行键的值由下方覆盖块
    # 用注册表结果填充；未执行键（section 抛异常/被关闭）不写入，落各段的默认值。
    # _sv 提前计算，供各处「key in _sv」判断（template/append 一致）。
    _sv = _section_values or {}
    _registry_done = set(_sv.keys())
    # ⑤-c（2026-10-03）：本函数原有 13 段「〔key〕未被注册表执行则在此重算一遍」的内联兜底已删除。
    # 判据＝为「删 legacy」而埋的前置观测（提交 61ad71de，2026-09-01）在 4.5 周内 A/B 双 0 命中，
    #   而观测通道本身活跃（memory_obs 59,005 行 / 最近 7 天 52,988 行）⇒ 兜底从未被触发。
    # 语义变化（**有意为之，fail-visible**）：某个 section 抛异常时，该段不再由这里重算，
    #   而是落到下面的**默认值**（"无" / "暂无" / 空串），缺哪一段从日志与 context_section_failed
    #   留痕里能看出来；代价是少了一层纵深，收益是「按 caller 过滤的查询」从此只有一份实现
    #   （B 家族 43 处审计当初就是为了抓这份重复实现里漏传 caller）。
    # 上面各段的默认值赋值行**全部保留**——B 类覆盖块与尾部装配仍会读这些名字。
    # 已随之删除的测试：test_context_no_caller_failclosed.py 的 3 例装配级内联对照
    #   （它们是这 10 处内联实现的唯一覆盖；生产路径的 caller 隔离由该文件里逐个查询点的
    #   section 级用例继续钉，装配层则新增一例钉「段未产出 → 落默认值且不重算」的新契约）。
    async with async_session_factory() as db:
        result = await db.execute(
            select(AICharacter).where(AICharacter.id == state["character_id"])
        )
        char = result.scalar_one_or_none()

    if char is None:
        state["ai_response"] = "\u89d2\u8272\u4e0d\u5b58\u5728"
        return state

    # P1 修复（2026-08-16）：填充角色自述供 response_parser 自述删除分支使用（此前恒空导致功能永不生效）
    state["character_info"] = {"self_statement": char.self_statement or ""}

    # 热度裁剪（2026-08-16，方案 B）：低频角色缩小日摘要/织库注入（Feature Flag agent_context_trim 默认开）
    # P3-1：注册表路径已用 _resolve_trim 算好同一 trim（含 _is_hot_character 近 7 天消息数查询）并注入，
    # 此处直接复用，避免注册表与组装器各查一次；未注入（自算调用）时照常自算。
    if _trim is None:
        hot = True
        try:
            from app.agent.loop import AGENT_FLAGS
            if AGENT_FLAGS.get("agent_context_trim", True):
                hot = await _is_hot_character(state["character_id"], state.get("user_id"))
        except Exception:
            hot = True
        _trim = _trim_limits(hot)

    # X-4（2026-08-18）：检索区轮次 +1（每轮上下文构建计一轮；进程内状态，重启清零）
    # P3-5（2026-08-25）：注册表路径已在其入口（context.build_context）先 bump，此处不再重复；
    # 纯 legacy 路径（_section_values is None）仍照常在此 bump —— 保证两条路径用同一轮次做记忆
    # N 轮去重 / Lorebook sticky-cooldown 判定（消除 off-by-one）。
    if _section_values is None:
        _bump_memory_round(state["character_id"])

    # 用户信息
    from app.models.user import User
    async with async_session_factory() as db:
        u_result = await db.execute(select(User).where(User.id == state.get("user_id")))
        user = u_result.scalar_one_or_none()
    user_name = user.nickname or user.username or "\u7528\u6237" if user else "\u7528\u6237"

    char_name = char.name
    # 思考第一人称化（2026-09-10）：角色名/对方昵称写入 state，供内心活动指令注入与上屏人称归一取用
    state["user_name"] = user_name
    state["character_name"] = char_name
    gender_info = f"你的性别: {gender_cn(char.gender)}"
    personality_info = f"\u4eba\u683c: {char.personality}" if char.personality else ""
    style_info = f"\u804a\u5929\u98ce\u683c: {char.chat_style}" if char.chat_style else ""
    # 认知循环 v2.1（Phase 3）：人格上下文统一层（聊天与主动消息共用）
    # P3-1：注册表已执行 persona section（relationship 即代表整组 persona 槽已算）时不重复
    # assemble_persona_context（含角色/记忆/关系温度等 DB 查询）；各槽由下方覆盖块用注册表值填充。
    # 未执行（section 抛异常）时照常内联兜底。
    if "relationship" in _registry_done:
        _persona = {
            "relationship": "", "current_status": "", "relationship_state": "",
            "character_feelings": "", "storyline_recall": "", "storyline_status": "",
            "recent_emotion": "", "active_topics": "", "identity_profile": "",
        }
    else:
        from app.agent.persona import assemble_persona_context
        _persona = await assemble_persona_context(state["character_id"], state.get("user_id"))
    relationship = _persona["relationship"]
    current_status = _persona["current_status"]

    # ⑤-c 起本函数不再自算下面这些段：值只有「默认值」与「步骤5 覆盖块填入的注册表值」两种来源，
    # 段未产出（抛异常/被关闭）就保持默认值。
    # 最近1天完整消息
    chat_history = ""

    # P4：世界状态（当前事实折叠，失败静默缺省"无"）
    world_facts_text = "无"

    # P1：核心记忆 + 关系锚点 + 开放循环（World & Cognition；失败静默，缺省"无"；
    # X-4：核心/锚点注入上限按热度裁剪，复用 _trim_limits）
    # 记忆文本（X-4：检索区 N 轮去重——同一记忆最近 5 轮内不重复注入；核心记忆/锚点等长期画像不受限）
    if _section_values is not None:
        memories_text = _section_values.get("memories", "\u6682\u65e0")
        core_text = _section_values.get("core_memories", "\u65e0")
        anchors_text = _section_values.get("anchors", "\u65e0")
        loops_text = _section_values.get("open_loops", "\u65e0")
    else:
        core_text, anchors_text, loops_text = await _inject_core_anchors_loops(
            state.get("character_id"), state.get("user_id"), _trim,
        )
        memory_lines = _build_retrieved_memory_lines(state["character_id"], state.get("retrieved_memories", []))
        memories_text = "\n".join(memory_lines) if memory_lines else "\u6682\u65e0"

    # 朋友圈：角色自己最近 1 条 + 用户最近 3 条（近 7 天），让角色记得用户发过的内容（零额外 LLM）
    moments_text = "\u6682\u65e0"

    # 宠物信息（只注入：用户养的宠物 + 当前角色自己养的 AI 宠物；
    # 其他角色养的 AI 宠物不注入，防止"别人的宠物被算作自己/用户养的"；只读注入不落库）
    pets_text = "无"

    storyline_recall = _persona["storyline_recall"]

    character_feelings = _persona["character_feelings"]

    storyline_status = _persona["storyline_status"]

    # 用户情绪感知（P2-1）：轻量规则器，零 token；认知循环开启时优先用感知层结果（等价回退）
    user_emotion = "无"

    recent_emotion = _persona["recent_emotion"]

    # 用户八维可视化状态（用户手动设置）：全 50=未设置则跳过；有非默认值才注入（控 token）。
    # G-P2-4（2026-08-18）：独立分区（不再混入「用户情绪」区），与规则器情绪提示分离、各自独立配额
    user_manual_state = ""

    # 手机感知（用户授权采集的屏幕/剪贴板/相册快照，仅注入文本）
    phone_perception = "无"

    # 小手机（2026-08-11）：角色日历备注 + 浏览器搜索历史（仅文本注入）
    phone_desktop = "无"

    # 进行中的时间承诺（防剧情穿帮：AI 承诺未到期时不得提前演"回来了"；2026-08-14 修复）
    pending_timer_text = "无"

    # 时间感知（2026-08-08）：北京时间兜底 + 用户本地时区（若上报）+ 距上次互动时长
    current_time_str = ""

    # 位置感知 + 天气（2026-08-08）：用户开启位置信息后注入城市（GPS 反查优先）+ 当前天气（Open-Meteo，30 分钟缓存，失败静默）
    location_text = ""

    # 组装 context_messages（注入用户画像：性别/对象/关系，消除刻板印象）
    user_profile_text = ""
    user_notes_text = ""
    relationship_state = _persona["relationship_state"]

    # 认知循环 v2.1：感知注入 + 规划指令（开关关闭时为空，走旧 prompt）
    cognitive_plan = ""

    active_topics_text = _persona["active_topics"]
    identity_profile = _persona.get("identity_profile") or ""

    # 步骤5（注册表）：分区值已由注册表 section 算出，此处把对应名字填成注册表值；未产出的段保持上面的默认值
    # （⑤-c 起不再重算）。值为未裁剪原始值，后续裁剪块统一处理。
    _sv = _section_values or {}
    if _sv:
        if "chat_history" in _sv: chat_history = _sv["chat_history"]
        if "world_facts" in _sv: world_facts_text = _sv["world_facts"]
        if "moments" in _sv: moments_text = _sv["moments"]
        if "pets" in _sv: pets_text = _sv["pets"]
        if "phone_perception" in _sv: phone_perception = _sv["phone_perception"]
        if "phone_desktop" in _sv: phone_desktop = _sv["phone_desktop"]
        if "pending_timer" in _sv: pending_timer_text = _sv["pending_timer"]
        if "current_time" in _sv: current_time_str = _sv["current_time"]
        # persona 槽
        if "relationship" in _sv: relationship = _sv["relationship"]
        if "current_status" in _sv: current_status = _sv["current_status"]
        if "relationship_state" in _sv: relationship_state = _sv["relationship_state"]
        if "character_feelings" in _sv: character_feelings = _sv["character_feelings"]
        if "storyline_recall" in _sv: storyline_recall = _sv["storyline_recall"]
        if "recent_emotion" in _sv: recent_emotion = _sv["recent_emotion"]
        if "storyline_status" in _sv: storyline_status = _sv["storyline_status"]
        if "active_topics" in _sv: active_topics_text = _sv["active_topics"]
        if "identity_profile" in _sv: identity_profile = _sv["identity_profile"]
        if "user_emotion" in _sv: user_emotion = _sv["user_emotion"]
        if "user_manual_state" in _sv: user_manual_state = _sv["user_manual_state"]
        if "cognitive_plan" in _sv: cognitive_plan = _sv["cognitive_plan"]

    # user_info（特殊：user_profile + user_notes 拼接后整体裁剪；注册表提供时直接使用最终值）
    if _sv and "user_info" in _sv:
        user_info_resolved = _sv["user_info"]
    else:
        user_info_resolved = _build_user_info(user_profile_text, user_notes_text)

    # P0-1 分区 Token 配额：统一裁剪（超配额才截断，配额内零行为变化）
    _qt = _SECTION_QUOTA_TOKENS
    chat_history = _clip_text_to_quota(chat_history, _qt["chat_history"])
    world_facts_text = _clip_text_to_quota(world_facts_text, _qt["world_facts"])
    core_text = _clip_text_to_quota(core_text, _qt["core_memories"])
    anchors_text = _clip_text_to_quota(anchors_text, _qt["anchors"])
    loops_text = _clip_text_to_quota(loops_text, _qt["open_loops"])
    # #70 方案A：memories 配额按 flag 动态——关=400（旧链路一致），开=500（分层注入受益）
    _memories_quota = _qt["memories"]
    try:
        from app.agent.loop import AGENT_FLAGS
        if AGENT_FLAGS.get("memory_tiered_inject", False):
            _memories_quota = 520  # M1-S1：随 base 400->420 同步 +20（不增 9000 总顶）
    except Exception:
        pass
    memories_text = _clip_text_to_quota(memories_text, _memories_quota)
    moments_text = _clip_text_to_quota(moments_text, _qt["moments"])
    pets_text = _clip_text_to_quota(pets_text, _qt["pets"])
    phone_perception = _clip_text_to_quota(phone_perception, _qt["phone_perception"])
    phone_desktop = _clip_text_to_quota(phone_desktop, _qt["phone_desktop"])
    pending_timer_text = _clip_text_to_quota(pending_timer_text, _qt["pending_timer"])  # G-P1-2：改用独立配额键（此前误用 storyline）
    location_text = _clip_text_to_quota(location_text, _qt["location"])
    user_profile_text = _clip_text_to_quota(user_profile_text, _qt["user_profile"])
    user_notes_text = _clip_text_to_quota(user_notes_text, _qt["user_notes"])
    storyline_status = _clip_text_to_quota(storyline_status, _qt["storyline"])
    # 哨兵归一（2026-09-19 落位配套）：无进行中剧情线时 persona.py:99 给哨兵值「无」，
    # 模板落位后会凭空多出一行「无」→ 空串/「无」统一归一为空串，不注入；有剧情线时原样保留。
    if isinstance(storyline_status, str) and storyline_status.strip() in ("", "无"):
        storyline_status = ""
    character_feelings = _clip_text_to_quota(character_feelings, _qt["feelings"])
    recent_emotion = _clip_text_to_quota(recent_emotion, _qt["recent_emotion"])
    user_emotion = _clip_text_to_quota(user_emotion, _qt["user_emotion"])
    user_manual_state = _clip_text_to_quota(user_manual_state, _qt["user_manual_state"])
    identity_profile = _clip_text_to_quota(identity_profile, _qt["user_profile"])
    # MCP 工具声明注入（Phase 2）：enabled 且非 FORBID 的 mcp.* 工具。P1 归属过滤 + P2-A 流式
    # 不注入在 _build_mcp_tool_declarations 内处理；P4-A 在此按工具粒度裁剪（quota_chars 传字符预算）
    if _section_values is not None:
        mcp_tools_blocks = _section_values.get("mcp_tools") or []
        if isinstance(mcp_tools_blocks, str):
            mcp_tools_blocks = [mcp_tools_blocks] if mcp_tools_blocks else []
        mcp_resources_blocks = _section_values.get("mcp_resources") or []
        if isinstance(mcp_resources_blocks, str):
            mcp_resources_blocks = [mcp_resources_blocks] if mcp_resources_blocks else []
        # MCP 资源摘要（Phase 4，2026-08-28）：无资源/流式时为空 → 不追加块（零行为变化）。
        if mcp_resources_blocks:
            mcp_resources_blocks = [_clip_text_to_quota(b, _qt["mcp_resources"]) for b in mcp_resources_blocks]
    else:
        mcp_tools_blocks = []
        mcp_tools_text = await _build_mcp_tools_text(
            state.get("user_id"),
            stream=_is_stream_ctx,
            quota_chars=_qt["mcp_tools"] * _EST_CHARS_PER_TOKEN,
        )
        if mcp_tools_text:
            mcp_tools_blocks = [mcp_tools_text]
        # MCP 资源摘要注入（Phase 4，2026-08-28）：已连接 Server 的资源摘要，按配额裁剪。
        # V2-8：流式模式不注入资源摘要（与工具声明行为一致），避免"提示可用工具但实际无法执行"。
        mcp_resources_blocks = []
        mcp_resources_text = await _build_mcp_resources_text(state.get("user_id"), stream=_is_stream_ctx)
        if mcp_resources_text:
            mcp_resources_blocks = [_clip_text_to_quota(mcp_resources_text, _qt["mcp_resources"])]

    state["context_messages"] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT_TEMPLATE.format(
                name=char_name,
                gender_info=gender_info,
                personality_info=personality_info,
                style_info=style_info,
                relationship=relationship,
                current_status=current_status,
                chat_history=chat_history,
                world_facts=world_facts_text,
                core_memories=core_text,
                anchors=anchors_text,
                open_loops=loops_text,
                memories=memories_text,
                bio=char.bio or "\u6682\u65e0",
                self_statement=char.self_statement or "\u6682\u65e0",
                current_time=current_time_str,
                pending_timer=pending_timer_text,
                moments=moments_text,
                storyline_recall=storyline_recall,
                character_feelings=character_feelings,
                storyline_status=storyline_status,
                user_emotion=user_emotion,
                user_manual_state=user_manual_state,
                recent_emotion=recent_emotion,
                pets_info=pets_text,
                phone_perception=phone_perception,
                phone_desktop=phone_desktop,
                relationship_state=relationship_state,
                cognitive_plan=cognitive_plan,
                active_topics=active_topics_text,
                identity_profile=identity_profile,
                user_info=user_info_resolved,  # G-P1-2：user_notes 空时不重复拼接 + 整体 500 token 裁剪（注册表/内联一致）
            ),
        },
    ]

    # MCP 工具声明注入（Phase 2，2026-08-26）：仅在存在 enabled 且非 FORBID 的 mcp.* 工具时
    # 追加一条 system 块（JSON 工具声明 + 调用标记格式）；无 MCP 工具时零行为变化。
    for _mcp_b in mcp_tools_blocks:
        state["context_messages"].append({"role": "system", "content": _mcp_b})

    # MCP 资源摘要注入（Phase 4，2026-08-28）：已连接 Server 的资源摘要（uri/name/mimeType）；
    # 无资源时零行为变化。
    for _mcp_b in mcp_resources_blocks:
        state["context_messages"].append({"role": "system", "content": _mcp_b})

    # ── 现状三连（2026-09-18 挂载）：C3 用户当前现状锚点 → 用户最新状态 → 工作记忆 ──
    # 三者此前只注册未挂载（builder 每轮都跑、结果无人消费 = 永不注入，见
    # docs/context-order-convention.md §2.4）。此处只做挂载：沿用 builder 返回的 list[str]，
    # 不改任何闸门/灰度/预算判据；闸门关或无数据时 builder 返回空列表 → 追加零条（零行为变化）。
    # 落位：紧邻主模板记忆区（memories/core_memories）之后、织库等素材块之前；MCP 能力声明
    # 按 §2.2 约定仍保持最前，故本组排在其后。
    if _sv and "current_state_anchor" in _sv:
        for _b in _sv["current_state_anchor"]:
            state["context_messages"].append({"role": "system", "content": _b})

    if _sv and "user_now" in _sv:
        for _b in _sv["user_now"]:
            state["context_messages"].append({"role": "system", "content": _b})

    if _sv and "working_state" in _sv:
        for _b in _sv["working_state"]:
            state["context_messages"].append({"role": "system", "content": _b})

    # location（2026-09-19 顺序审计）：location 属状态类，从 append 链尾上移并入现状组
    # （current_state_anchor / user_now / working_state 之后、织库等素材块之前）；
    # 零 token，纯位移，不改文本/配额/闸门（见 docs/context-order-convention.md §2.2）。
    if _sv and "location" in _sv:
        for _loc_b in _sv["location"]:
            state["context_messages"].append({"role": "system", "content": _loc_b})
    elif location_text:
        state["context_messages"].append({"role": "system", "content": location_text})

    if _sv and "weave_full" in _sv:
        for _b in _sv["weave_full"]:
            state["context_messages"].append({"role": "system", "content": _b})
    else:
        # 织库全注入（角色设置-社交开关，2026-08-12）：开启后把该角色织库卡片注入上下文
        # （卡片为 LLM 整理后的全景记忆，为未来「全注入对话」提供结构化数据）
        try:
            from app.models.character import ProactiveSettings as _PS
            from app.models.memory import WeaveCard, WeaveCardCharacter
            from sqlalchemy import or_ as _or_

            async with async_session_factory() as db:
                _ps_row = (
                    await db.execute(select(_PS).where(_PS.character_id == state["character_id"]))
                ).scalar_one_or_none()
                _full_inject = bool(getattr(_ps_row, "weave_full_inject_enabled", False)) if _ps_row else False
                _cards = []
                if _full_inject:
                    _cards = (
                        await db.execute(
                            select(WeaveCard)
                            .where(
                                _or_(
                                    WeaveCard.character_id == state["character_id"],
                                    WeaveCard.id.in_(
                                        select(WeaveCardCharacter.card_id).where(
                                            WeaveCardCharacter.character_id == state["character_id"]
                                        )
                                    ),
                                ),
                                WeaveCard.is_stale.is_(False),
                            )
                            .order_by(WeaveCard.importance.desc())
                            .limit(_trim["weave_limit"])
                        )
                    ).scalars().all()
            if _cards:
                _lines = [f"- 【{c.title}】[记录于 {str(c.created_at)[:10]}] {c.summary[:120]}" for c in _cards]
                _weave_full = _clip_text_to_quota(
                    "【全景记忆·织库】以下是你们之间重要经历的全景卡片（全注入对话已开启，按重要度排序）：\n"
                    + "\n".join(_lines),
                    _SECTION_QUOTA_TOKENS["weave_full"],
                )
                state["context_messages"].append({
                    "role": "system",
                    "content": _weave_full,
                })
        except Exception as e:
            _logger.warning("weave full inject failed: %s", e)

    if _sv and "lorebook" in _sv:
        for _b in _sv["lorebook"]:
            state["context_messages"].append({"role": "system", "content": _b})
    else:
        # P1-2 Lorebook 关键词触发表（2026-08-16）：用户消息命中关键词 → 确定性注入（受配额裁剪，防注入膨胀）
        try:
            from app.memory.lorebook import load_matching_entries
            _lb_text_input = (state.get("user_message") or "").strip()
            _lb_hits = await load_matching_entries(state["character_id"], _lb_text_input)
            if _lb_hits:
                _lb_lines = [f"- 【{e.title}】{e.content[:150]}" for e in _lb_hits]
                _lb_inject = _clip_text_to_quota(
                    "【设定·Lorebook】用户提到了相关设定，请按以下条目理解（这些是既定设定，不要与其冲突）：\n"
                    + "\n".join(_lb_lines),
                    _SECTION_QUOTA_TOKENS["lorebook"],
                )
                state["context_messages"].append({"role": "system", "content": _lb_inject})
        except Exception as e:
            _logger.warning("Lorebook inject failed: %s", e)

    if _sv and "life_share" in _sv:
        for _b in _sv["life_share"]:
            state["context_messages"].append({"role": "system", "content": _b})
    else:
        # 私·织库「AI 生活」注入（角色设置-社交「AI 生活分享」开关，2026-08-12）：
        # 信任机制与隐私上锁同源——trust≥60 有概率提及、≥70 高概率、<60 不提及（角色有权交流自己的私生活）
        try:
            from app.models.character import CharacterState as _CS
            from app.models.character import ProactiveSettings as _PS
            import random as _rnd

            # M1-S10（2026-08-31）：trust 复用本轮 character_states_snapshot（chat_service 一次带出，
            # 含八维+trust）；无快照（群聊/runtime 等其他调用方）回退自行查询，行为不变
            _snap = state.get("character_states_snapshot")
            if isinstance(_snap, dict) and _snap.get("trust") is not None:
                _trust = int(_snap["trust"] or 50)
                async with async_session_factory() as db:
                    _ps_row2 = (
                        await db.execute(select(_PS).where(_PS.character_id == state["character_id"]))
                    ).scalar_one_or_none()
                    _share = bool(getattr(_ps_row2, "life_share_enabled", True)) if _ps_row2 is not None else True
            else:
                async with async_session_factory() as db:
                    _cs_row = (
                        await db.execute(select(_CS).where(_CS.character_id == state["character_id"]))
                    ).scalar_one_or_none()
                    _trust = int(getattr(_cs_row, "trust", 50) or 50) if _cs_row is not None else 50
                    _ps_row2 = (
                        await db.execute(select(_PS).where(_PS.character_id == state["character_id"]))
                    ).scalar_one_or_none()
                    _share = bool(getattr(_ps_row2, "life_share_enabled", True)) if _ps_row2 is not None else True
            _life_lines = []
            if _share and _trust >= 60:
                _prob = 0.60 if _trust >= 70 else 0.30
                if _rnd.random() < _prob:
                    from app.models.memory import Memory as _MemL
                    # 2026-09-17 批次一（任务2）：AI 生活注入 = 现状面 → 恒 active 新口径
                    from app.memory.service import current_facts_status_clause

                    async with async_session_factory() as db:
                        _lives = (
                            await db.execute(
                                select(_MemL)
                                .where(
                                    _MemL.user_id == state.get("user_id"),
                                    _MemL.character_id == state["character_id"],
                                    _MemL.source == "life",
                                    _MemL.delete_at.is_(None),
                                    current_facts_status_clause(),
                                )
                                .order_by(_MemL.importance.desc(), _MemL.created_at.desc())
                                .limit(2)
                            )
                        ).scalars().all()
                    _life_lines = [
                        f"[记录于 {str(m.created_at)[:10]}] {(m.content or "").strip()[:100]}"
                        for m in _lives if (m.content or "").strip()
                    ]
            if _life_lines:
                state["context_messages"].append({
                    "role": "system",
                    "content": (
                        "【AI 生活】你最近的生活点滴（可以自然提起，不必刻意说明）：\n- "
                        + "\n- ".join(_life_lines)
                    ),
                })
        except Exception as e:
            _logger.warning("life share inject failed: %s", e)

    if _sv and "shared_memory" in _sv:
        for _b in _sv["shared_memory"]:
            state["context_messages"].append({"role": "system", "content": _b})
    else:
        # Shared Memory（Phase C，2026-08-14）：共同经历注入（AI 自然引用，防编造：只从记录检索）
        try:
            async with async_session_factory() as db:
                from app.memory.shared_events import recall_text as _shared_recall
                _shared = await _shared_recall(db, state["user_id"], state["character_id"], limit=2)
            if _shared:
                state["context_messages"].append({
                    "role": "system",
                    "content": "【共同经历】你们一起经历过的特别时刻（可以自然提起，不要生硬复述）：\n" + _shared,
                })
        except Exception as e:
            _logger.warning("shared recall inject failed: %s", e)

    if _sv and "search_capability" in _sv:
        for _b in _sv["search_capability"]:
            state["context_messages"].append({"role": "system", "content": _b})
    else:
        # AI 自主搜索能力（2026-08-16）：browser_mcp 插件启用时，允许 LLM 输出 [SEARCH] 标记查证
        try:
            import sys as _sys
            if _sys.modules.get("ai_plugin_browser_mcp") is not None:
                state["context_messages"].append({
                    "role": "system",
                    "content": (
                        "【搜索能力】如果你遇到不懂的知识、不确定的事实、或想查证具体做法（例如：这个梗是什么意思、"
                        "怎么劝对象少打游戏、头发油怎么办、怎么写情书），可以在回复中输出 "
                        "[SEARCH]你想搜索的内容[/SEARCH]（系统会自动搜索并把结果告诉你，再基于结果回复）。\n"
                        "使用原则：只在真需要查证时用（一轮最多 1 次），不要编造你不确定的信息；"
                        "不需要查证时绝对不要输出该标记。"
                    ),
                })
                # 强意图兜底：用户明确要求搜索/查证时，追加本轮提醒确保输出标记
                _um = (state.get("user_message") or "").strip()
                _search_intent = any(k in _um for k in (
                    "查查", "搜搜", "查一下", "搜一下", "上网查", "去查", "去搜", "百度一下",
                    "帮我查", "帮我搜", "查查资料", "搜一搜", "查一下资料", "查查这个", "这个是什么梗",
                )) or bool(__import__("re").search(r"(?:查|搜|百度|谷歌|上网|看看|知乎).{0,4}(?:什么|怎么|为什么|是谁|是啥|一下|一查|一搜|梗|新闻|信息|做法|方法)", _um))
                if _search_intent:
                    state["context_messages"].append({
                        "role": "system",
                        "content": (
                            "【本轮提醒】用户刚才明确要求你去搜索/查证，请务必在本轮回复末尾另起一行输出 "
                            "[SEARCH]你想搜索的内容[/SEARCH] 标记（说“我去搜”不算数——系统只认标记，"
                            "检测到标记才会真正搜索并带着结果回来）。正文照常自然回应（如“等着，我去查查”）。"
                        ),
                    })
        except Exception:
            pass

    # Ariadne 模块 B（2026-09-18 挂载）：[RECALL] 记忆调取规则声明，紧随 search_capability
    # （同为「能力声明」类，相邻落位）。flag memory_recall_second_hop 默认关 → builder 返回
    # 空列表 → 追加零条（零行为变化）；只挂载，不改闸门。
    if _sv and "recall_capability" in _sv:
        for _b in _sv["recall_capability"]:
            state["context_messages"].append({"role": "system", "content": _b})

    if _sv and "group_dynamics" in _sv:
        for _b in _sv["group_dynamics"]:
            state["context_messages"].append({"role": "system", "content": _b})
    else:
        # 家庭群聊动态（Phase 3，2026-08-15）：角色可回忆所在群最近发生的事
        # 数据源 = chat_group_messages 共享表（天然符合知识边界：只知道群里公开说过的），零额外 LLM
        try:
            from app.models.chat import ChatGroup as _CG, ChatGroupMember as _CGM, ChatGroupMessage as _CGMsg
            async with async_session_factory() as db:
                _gids = (
                    await db.execute(
                        select(_CGM.group_id).where(_CGM.character_id == state["character_id"])
                    )
                ).scalars().all()
                _group_lines = []
                if _gids:
                    _grows = (await db.execute(
                        select(_CG.id, _CG.name).where(_CG.id.in_(set(_gids)))
                    )).all()
                    _gname = {row[0]: (row[1] or "家庭群聊") for row in _grows}
                    for _gid in _gids:
                        _msgs = (await db.execute(
                            select(_CGMsg)
                            .where(_CGMsg.group_id == _gid, _CGMsg.msg_type == "normal")
                            .order_by(_CGMsg.id.desc())
                            .limit(4)
                        )).scalars().all()
                        if not _msgs:
                            continue
                        _member_ids = (await db.execute(
                            select(_CGM.character_id).where(_CGM.group_id == _gid)
                        )).scalars().all()
                        _names = {}
                        if _member_ids:
                            _nrows = (await db.execute(
                                select(AICharacter.id, AICharacter.name).where(AICharacter.id.in_(_member_ids))
                            )).all()
                            _names = {r[0]: r[1] for r in _nrows}
                        _lines = []
                        for _m in reversed(_msgs):
                            _who = _names.get(_m.character_id, "用户") if _m.character_id else "用户"
                            _mtag = ""
                            try:
                                if _m.created_at is not None:
                                    from app.utils.timeutil import shift_utc_naive
                                    _mtag = f" {shift_utc_naive(_m.created_at, 8):%m-%d %H:%M}"
                            except Exception:
                                _mtag = ""
                            _lines.append(f"[{_who}{_mtag}] {(_m.content or '')[:60]}")
                        _group_lines.append(f"【{_gname.get(_gid, '家庭群聊')}】" + "；".join(_lines))
                if _group_lines:
                    state["context_messages"].append({
                        "role": "system",
                        "content": "【群聊动态】你在家庭群聊里和大家聊过的事（可以自然提起，不要生硬复述）：\n- " + "\n- ".join(_group_lines),
                    })
        except Exception as e:
            _logger.warning("group recall inject failed: %s", e)

    # #72 PR-C P3（2026-09-18 挂载）：逐角色群聊私有认知，紧随 group_dynamics（群聊语境相邻）。
    # 两级闸 group_cognition_enabled_for 关 / 无 group_id / 无认知 → builder 返回空列表 → 零行为变化。
    if _sv and "group_char_cognition" in _sv:
        for _b in _sv["group_char_cognition"]:
            state["context_messages"].append({"role": "system", "content": _b})

    if _sv and "image_gen" in _sv:
        for _b in _sv["image_gen"]:
            state["context_messages"].append({"role": "system", "content": _b})
    else:
        # 生图开关（角色级）：开启时注入"聊天内AI发图"指令，LLM 按需输出 [GEN_IMAGE] 标记
        try:
            async with async_session_factory() as db:
                _ps = await db.execute(
                    select(ProactiveSettings).where(ProactiveSettings.character_id == state["character_id"])
                )
                _psobj = _ps.scalar_one_or_none()
                if _psobj is not None and _psobj.image_gen_enabled:
                    _active_img = bool(getattr(_psobj, "active_image_gen_enabled", False))
                    if _active_img:
                        _img_content = (
                            "【生图指令】你可以在合适的时机主动生成图片分享（比如描绘眼前场景、用画面表达心情、送对方一张小画、情绪到位时配图），"
                            "也可以在用户要求画图／生成图片／配图／自拍时画图。需要发图时，在回复末尾另起一行输出标记 [GEN_IMAGE] 画面描述 [/GEN_IMAGE]，画面描述写清主体、风格、颜色等供生图服务使用；"
                            "不要过于频繁（每次会话最多 1-2 次），没有合适的画面灵感时不要强行输出。"
                            "当用户明确要求你生成图片、画图、自拍、配图时，必须输出 [GEN_IMAGE] 标记，绝不能只回复文字假装发了图。"
                            "示例：用户说“给我画只猫”→ 正文回复“行，等着。”后另起一行输出 [GEN_IMAGE] 一只橘色小猫坐在窗台上，插画风格，暖色调 [/GEN_IMAGE]。\n"
                            "发图时同时输出图片消息文案：在 [GEN_IMAGE] 标记前另起一行输出 [IMG_TEXT] 符合你性格的一句话（12字内，如“……就这一张。”）[/IMG_TEXT]，不要用“给你画好啦～”这种通用口吻。"
                        )
                    else:
                        _img_content = (
                            "【生图指令】当用户要求你画图／生成图片／配图／自拍（如“画一只猫”“给我画张图”“生成你的自拍”）时，"
                            "必须在回复末尾另起一行输出标记 [GEN_IMAGE] 画面描述 [/GEN_IMAGE]，画面描述写清主体、风格、颜色等供生图服务使用；"
                            "正文可以自然衔接（如“等着。”），绝不能只回复文字假装发了图。"
                            "用户没有要求画图时不要输出该标记。\n"
                            "发图时同时输出图片消息文案：在 [GEN_IMAGE] 标记前另起一行输出 [IMG_TEXT] 符合你性格的一句话（12字内，如“……就这一张。”）[/IMG_TEXT]，不要用“给你画好啦～”这种通用口吻。"
                        )
                    state["context_messages"].append({
                        "role": "system",
                        "content": _img_content,
                    })
                    # 强意图兜底：用户消息含明确画图/自拍意图时，追加本轮提醒，确保 LLM 输出标记
                    _um = (state.get("user_message") or "").strip()
                    _img_intent = (
                        ("自拍" in _um) or ("配图" in _um)
                        or bool(__import__("re").search(r"(?:画|生成|做|来|发).{0,8}(?:图|图片|照片|壁纸|头像|图集)", _um))
                        or bool(__import__("re").search(r"(?:给我|帮我|给我画|帮我画).{0,10}(?:图|画|照片|自拍)", _um))
                    )
                    if _img_intent:
                        state["context_messages"].append({
                            "role": "system",
                            "content": (
                                "【本轮提醒】用户刚才明确要求生成图片／自拍／画图，请务必在本轮回复末尾另起一行输出 [GEN_IMAGE] 画面描述 [/GEN_IMAGE] 标记，"
                                "正文照常对话并自然衔接（如“等着。”）；自拍类画面描述可参考上面的角色外貌人设。"
                            ),
                        })
                    # 主动生图概率兜底（2026-08-14）：开关开启 + 用户未明确要求 + 距上次生图任务 >= 4h + 随机 30% → 注入本轮提醒
                    elif _active_img:
                        try:
                            from app.models.life import ImageGenTask as _ImgTask
                            async with async_session_factory() as _dbg:
                                _last_task = (
                                    await _dbg.execute(
                                        select(_ImgTask)
                                        .where(_ImgTask.user_id == state["user_id"])
                                        .order_by(_ImgTask.created_at.desc())
                                        .limit(1)
                                    )
                                ).scalar_one_or_none()
                            _last_at = _last_task.created_at if _last_task is not None else None
                            _age_h = 999.0
                            if _last_at is not None:
                                _last_naive = _last_at.replace(tzinfo=None) if _last_at.tzinfo else _last_at
                                _now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
                                _age_h = (_now_naive - _last_naive).total_seconds() / 3600
                            import random as _rnd
                            if _age_h >= 4 and _rnd.random() < 0.30:
                                state["context_messages"].append({
                                    "role": "system",
                                    "content": (
                                        "【本轮提醒】本次对话氛围合适，你可以在回复末尾另起一行主动输出 [GEN_IMAGE] 画面描述 [/GEN_IMAGE] 标记"
                                        "（描绘此刻场景／用画面表达心情／送对方一张小画），并按生图指令要求同时输出 [IMG_TEXT] 文案；"
                                        "若你确实没有合适的画面灵感，可以省略。"
                                    ),
                                })
                        except Exception as _e:
                            _logger.warning("Active image gen boost failed: %s", _e)
        except Exception as e:
            _logger.warning("Image gen instruction inject failed: %s", e)

    if _sv and "reasoning_instruction" in _sv:
        for _b in _sv["reasoning_instruction"]:
            state["context_messages"].append({"role": "system", "content": _b})
    else:
        # 内心活动指令（思考第一人称化，2026-09-10）：挡位 1（正文开头【推理：…】）与挡位 2
        # （原生 thinking 通道）统一注入——自称「我」、称对方昵称、不写后台字段；挡位 0 不注入
        try:
            from app.agent.context.reasoning_prompt import reasoning_instructions_for
            for _ins in reasoning_instructions_for(
                int(state.get("reasoning_level", 0) or 0),
                name=char_name, user=user_name,
            ):
                state["context_messages"].append({"role": "system", "content": _ins})
        except Exception as e:
            _logger.warning("Reasoning instruction inject failed: %s", e)

    if _sv and "lang_instruction" in _sv:
        for _b in _sv["lang_instruction"]:
            state["context_messages"].append({"role": "system", "content": _b})
    else:
        # i18n 语言软约束：跟随前端界面语言（zh/en），角色人设优先、不强转
        lang = (state.get("lang") or "zh").strip().lower()
        if lang == "en":
            lang_instruction = (
                "\u3010\u8bed\u8a00\u3011\u5f53\u524d\u754c\u9762\u8bed\u8a00\uff1aEnglish\u3002\u8bf7\u4e3b\u8981\u7528\u82f1\u6587\u56de\u590d\uff1b"
                "\u82e5\u7528\u6237\u7528\u4e2d\u6587\u63d0\u95ee\uff0c\u53ef\u5c0a\u91cd\u7528\u6237\u4f7f\u7528\u4e2d\u6587\u3002"
            )
        else:
            lang_instruction = (
                "\u3010\u8bed\u8a00\u3011\u5f53\u524d\u754c\u9762\u8bed\u8a00\uff1a\u4e2d\u6587\u3002\u8bf7\u4e3b\u8981\u7528\u4e2d\u6587\u56de\u590d\uff1b"
                "\u82e5\u7528\u6237\u7528\u82f1\u6587\u63d0\u95ee\uff0c\u53ef\u8ddf\u968f\u7528\u6237\u4f7f\u7528\u82f1\u6587\u3002"
            )
        state["context_messages"].append({"role": "system", "content": lang_instruction})


    # P3-2 温度/长度自适应：按聊天状态调整 temperature（倾诉 0.9 / 日常 0.8 / 敷衍 0.7）
    try:
        _intent = (state.get("perception") or {}).get("intent") or ""
        if ("低落" in user_emotion or "长篇倾诉" in user_emotion
                or "情绪激动" in user_emotion or "困惑" in user_emotion or _intent == "deep"):
            state["temperature"] = 0.9
        elif "简短回应" in user_emotion:
            state["temperature"] = 0.7
        else:
            state["temperature"] = 0.8
    except Exception:
        state["temperature"] = 0.8

    _logger.debug("Build context done: %d history msgs, %d memory entries",
                  len(chat_history.split("\n")) if chat_history else 0,
                  len(state.get("retrieved_memories", [])))

    # 追加时间提示 + 用户消息（location 已于 2026-09-19 上移并入现状组，见上方三连之后）
    if _sv and "time_prompt" in _sv:
        for _tp_b in _sv["time_prompt"]:
            state["context_messages"].append({"role": "system", "content": _tp_b})
    else:
        state["context_messages"].append({
            "role": "system",
            "content": f"\u3010\u5f53\u524d\u65f6\u95f4\u3011{current_time_str}\u3002\u5982\u679c\u7528\u6237\u95ee\u5230\u65f6\u95f4\u3001\u65e5\u671f\u3001\u661f\u671f\u51e0\uff0c\u8bf7\u76f4\u63a5\u7528\u4e0a\u9762\u7684\u65f6\u95f4\u56de\u7b54\uff1b\u8ddd\u4e0a\u6b21\u4e92\u52a8\u7684\u65f6\u957f\u53ef\u7528\u6765\u4f53\u4f1a\u201c\u591a\u4e45\u6ca1\u804a\u4e86\u201d\u7684\u611f\u89c9\uff0c\u81ea\u7136\u5730\u63d0\u53ca\uff0c\u4e0d\u8981\u523b\u610f\u5ff5\u6570\u636e\u3002\uff1b\u5404\u6ce8\u5165\u5206\u533a\uff08\u8bb0\u5fc6/\u670b\u53cb\u5708/\u7b14\u8bb0/\u7ec7\u5e93\u7b49\uff09\u91cc\u7684\u201c\u4eca\u5929/\u6628\u5929/\u6700\u8fd1\u201d\u7b49\u65f6\u95f4\u8bcd\u5c5e\u4e8e\u8be5\u8bb0\u5f55\u53d1\u751f\u5f53\u65f6\uff0c\u4e0d\u662f\u73b0\u5728\u3002",
        })
    # Ariadne 模块F/G（2026-09-04）：curated 编纂知识层 + prospective cue 线索命中。
    # flag 关 / 无内容时分区返回空列表 → 追加零条（与现状逐字节一致，零行为变化）。
    if _sv and "curated_knowledge" in _sv:
        for _ck_b in _sv["curated_knowledge"]:
            state["context_messages"].append({"role": "system", "content": _ck_b})
    if _sv and "prospective_cue" in _sv:
        for _pc_b in _sv["prospective_cue"]:
            state["context_messages"].append({"role": "system", "content": _pc_b})
    # 插件系统：context_inject / inject_prompt_skill（2026-09-18 位移 + 护栏）
    # 两处调用由「user 消息之后」整体前移到 continue_payload 与 user 消息**之前**：插件自行 append
    # 的块因此天然落在宿主 user 之前，不再破坏红线②「user 消息恒为最后一条」（见
    # docs/context-order-convention.md §0.1/§2.3）。插件可见契约（ctx 字段、append 写法）不变，
    # 异常隔离 try/except: pass 保持原样。

    # 插件系统：context_inject（启用插件可向上下文追加内容；异常隔离）
    # A2-M0 收尾（2026-09-21）：**缺 caller 不再兜底成 1 号账号**。旧写法 state.get("user_id", 1) 会把
    # 别人的这一轮当成 1 号账号：①插件按 user_id 读写自己的数据（快照/账号）会串到 1 号；②runtime
    # scope 开时可见性过滤也拿 1 号的可见集去分发。无 caller = 无法判定归属 = 不注入（fail-closed），
    # 与 M0 插件侧口径一致（test_browser_inject_按账号且无user_id不注入 同族）。
    try:
        _plugin_uid = state.get("user_id")
        if not _plugin_uid:
            _warn_context_inject_no_caller_once()
        else:
            from app.plugins.registry import run_hook
            await run_hook("context_inject", {
                "user_id": _plugin_uid,
                "character_id": state.get("character_id"),
                "session_id": state.get("session_id"),
                "user_message": state.get("user_message", ""),
                "context_messages": state["context_messages"],
            },
                # A2 M4：显式带调用者（ctx 已有 user_id）→ flag 开时只分发给本账号可见插件
                user_id=_plugin_uid,
                callsite="agent/context/legacy.py:context_inject",
            )
    except Exception:
        pass

    # 48c：配置驱动零代码技能注入（type=prompt 插件触发匹配后追加 system 消息；异常隔离不阻断主链路）
    try:
        from app.plugins.config_hooks import inject_prompt_skill
        await inject_prompt_skill(state)
    except Exception:
        pass

    if _sv and "continue_payload" in _sv:
        for _b in _sv["continue_payload"]:
            state["context_messages"].append({"role": "system", "content": _b})
    else:
        # 继续指令场景（用户点「继续」）：user 位是占位，真正指令注入 system 区并显式引用上一条内容
        _cont = state.get("continue_payload")
        if isinstance(_cont, dict) and (_cont.get("last_ai_content") or "").strip():
            _last_ai = str(_cont["last_ai_content"]).strip()[:500]
            _cont_instr = (
                "【系统指令】用户没有说话，你是在继续自己刚才的话。"
                "你上一条说的是：“" + _last_ai + "”"
                "请顺着这句话自然向前推进（补充细节、继续行动或开启下一步），"
                "不要重复上述已说过的内容或措辞，"
                "不要提到这条指令，不要替用户说话。"
                "内容长度自然，避免过短。直接输出要说的内容。"
            )
            state["context_messages"].append({"role": "system", "content": _cont_instr})

    state["context_messages"].append({
        "role": "user",
        "content": state["user_message"],
    })
    # 宿主 user 消息下标：红线②护栏锚点（legacy 装配尾部 + nodes.generate_response 的
    # before_generate 之后各用一次；不依赖「最后一条 role=user」，防插件伪造 user 把锚点带偏）
    _host_user_idx = len(state["context_messages"]) - 1
    state["_host_user_msg_index"] = _host_user_idx


    # ── 红线②宿主不变式（2026-09-18）：任何落在宿主 user 消息之后的块归位到 user 之前 ──
    # 正常路径（插件块已由位移落在 user 之前）零移动 → 与改动前逐字节一致；仅在真越位时告警 + 埋点。
    try:
        _moved_after_user = _enforce_user_message_last(
            state["context_messages"], user_index=state.get("_host_user_msg_index")
        )
        if _moved_after_user:
            _logger.warning(
                "context: 归位 %d 个落在宿主 user 之后的块（红线②护栏）", _moved_after_user
            )
            try:
                from app.memory.observability import obs_event

                obs_event(
                    state.get("character_id"),
                    "context_user_last_enforced",
                    {"moved": _moved_after_user},
                )
            except Exception:
                pass
    except Exception:
        pass

    # G-P1-2（2026-08-18）：system 整体 token 硬顶——所有分区 + 追加 system 块组装完成后，
    # 超限时从尾部裁剪各 system 块（追加块同样生效）；只截断文本、保留消息结构。
    _apply_system_total_quota(state["context_messages"], character_id=state.get("character_id"))
    # T5 M0 项2（2026-09-27，A4 批 6）：装配尾部留痕「裁剪后 system 总字符 + 本次生效预算」。
    # 为什么需要：流式路径拿不到 provider usage 时 token 记 0（agent/llm_client.py:648-656），
    # 「上下文 token/轮」这个指标失真；本条不依赖 provider，每轮真装配只写一条。
    # 约束：只留痕、不改任何装配结果；异常一律吞掉（仅 WARNING），不新增开关。
    try:
        from app.agent import context_builder as _cb
        from app.memory.observability import obs_event as _obs_event

        _sys_msgs = [m for m in state["context_messages"] if m.get("role") == "system"]
        _obs_event(
            state.get("character_id"),
            "system_total_chars",
            {
                "system_chars": sum(len(m.get("content") or "") for m in _sys_msgs),
                "budget_tokens": _cb._effective_system_budget_tokens(),
                "reserve_on": _cb.context_budget_reserve_enabled(),
            },
        )
    except Exception as _e:
        _logger.warning("system total chars obs failed: %s", _e)
    return state
