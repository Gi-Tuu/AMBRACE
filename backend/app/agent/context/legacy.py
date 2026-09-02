"""build_context_legacy —— 旧实现单独安置（F3，2026-08-31 自 context_builder.py 迁入）。

- flag `use_legacy_context` 关闭（默认）后此函数仍作为注册表路径的委托后端；
  稳定一版本、trace 无回退命中后整体删除（预计净删约 1100 行）。
- 接缝已摘除（2026-09-02）：本模块改为显式 import 依赖（见下方），不再经 _sync_seams
  把 context_builder 命名空间同步进 globals；裸名字静态可解析，可被 ruff 检查。
- 稳定后删除本文件时，同步删除 context_builder 的薄壳委托与 use_legacy_context 开关。
"""
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from app.utils.logger import get_logger
from app.agent.context_builder import (
    AICharacter,
    AIMoment,
    ChatMessage,
    ChatSession,
    MAX_RECENT_MESSAGES,
    ProactiveSettings,
    SYSTEM_PROMPT_TEMPLATE,
    _EST_CHARS_PER_TOKEN,
    _SECTION_QUOTA_TOKENS,
    _apply_system_total_quota,
    _build_mcp_resources_text,
    _build_mcp_tools_text,
    _build_older_summaries,
    _build_retrieved_memory_lines,
    _build_user_info,
    _build_user_manual_state_text,
    _bump_memory_round,
    _clip_text_to_quota,
    _inject_core_anchors_loops,
    _is_hot_character,
    _trim_limits,
    async_session_factory,
    gender_cn,
)

_logger = get_logger("context.legacy")


async def build_context_legacy(state: dict, *, stream: bool | None = None, _section_values: dict | None = None, _trim: dict | None = None) -> dict:
    """旧实现（回退函数）：构建完整的上下文 prompt（近1天完整消息 + 更早日概要 + 朋友圈）。

    `stream`（P2-A）：显式标记流式模式；None 时从 state 推断（state["stream_sink"] 非空 = 流式）。
    流式模式下 MCP 工具声明不注入（见 _build_mcp_tool_declarations）。

    `_section_values`（注册表内部）：由 context.build_context 走注册表算出的分区值（template 槽 /
    append 块），含**所有已执行** section 的键（结果为空的键也写入，可能为空串/空列表）。提供时
    对已执行键跳过对应内联计算（含 DB 查询），用注册表值覆盖；section 抛异常未执行的键不写入，
    照常内联计算兜底。记忆轮次 +1 已在入口由注册表路径执行。

    `_trim`（注册表内部）：热度裁剪参数（含 _is_hot_character 近 7 天消息数查询）。注册表路径
    已通过 `_resolve_trim` 算好并注入，此处直接复用，避免注册表与 legacy 各查一次；缺省（None）
    时照常自算——Feature Flag 关闭（纯 legacy）路径零行为变化。

    本函数为聚合组装：占位符语义（moments 无内容仍「暂无」、pets 仍「无」、world_facts 仍「无」等）
    与内联一致，输出与现状逐字节一致。
    """
    # 接缝已摘除（2026-09-02）：依赖名字已显式 import，无需 _sync_seams 自同步。
    # P2-A：流式模式判定（LangGraph 只传 state，从 state["stream_sink"] 推断；可显式覆盖）
    _is_stream_ctx = bool(state.get("stream_sink")) if stream is None else bool(stream)
    # P3-1（2026-08-31）：注册表已执行的 section 键集合（含结果为空的键）。已执行键在下方跳过
    # 对应内联计算（含 DB 查询），值由覆盖块用注册表值填充；未执行键（section 抛异常/关闭）不写入，
    # 照常内联计算兜底。_sv 提前计算，供各处「key in _sv」判断（template/append 一致）。
    _sv = _section_values or {}
    _registry_done = set(_sv.keys())
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
    # 此处直接复用，避免注册表与 legacy 各查一次；未注入（flag-off 纯 legacy）照常自算，零行为变化。
    if _trim is None:
        hot = True
        try:
            from app.agent.loop import AGENT_FLAGS
            if AGENT_FLAGS.get("agent_context_trim", True):
                hot = await _is_hot_character(state["character_id"], state.get("user_id", 1))
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
        u_result = await db.execute(select(User).where(User.id == state.get("user_id", 1)))
        user = u_result.scalar_one_or_none()
    user_name = user.nickname or user.username or "\u7528\u6237" if user else "\u7528\u6237"

    char_name = char.name
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
        _persona = await assemble_persona_context(state["character_id"], state.get("user_id", 1))
    relationship = _persona["relationship"]
    current_status = _persona["current_status"]

    # 最近1天完整消息
    # P3-1：注册表已执行 chat_history 时跳过内联（含最近/更早消息 DB 查询 + 日摘要补生成），
    # 值由覆盖块用注册表值填充；未执行（section 抛异常）时照常内联兜底。
    chat_history = ""
    if "chat_history" not in _registry_done:
        one_day_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)

        async with async_session_factory() as db:
            recent_result = await db.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.session_id == state["session_id"],
                    ChatMessage.created_at >= one_day_ago,
                )
                .order_by(ChatMessage.created_at.asc())
            )
            recent_msgs = list(recent_result.scalars().all())

        # 限制注入条数：最近的 MAX_RECENT_MESSAGES 条完整注入，更早部分并入日摘要
        older_extra = []
        if len(recent_msgs) > MAX_RECENT_MESSAGES:
            older_extra = recent_msgs[:-MAX_RECENT_MESSAGES]
            recent_msgs = recent_msgs[-MAX_RECENT_MESSAGES:]

        import json as _json
        # 代词锚（P1-2）：历史行标注"用户(昵称/他)"与"你(角色名)"，长上下文指代不漂移
        _gender_cn_user = "他" if (user and (user.gender or "").strip().lower() in ("male", "男")) else ("她" if (user and (user.gender or "").strip().lower() in ("female", "女")) else "TA")
        chat_history_lines = []
        _bj_today = datetime.now(timezone(timedelta(hours=8))).date()
        for msg in recent_msgs:
            sender = f"用户({user_name}/{_gender_cn_user})" if msg.sender_type == "user" else f"你({char_name})"
            # 历史行时间戳（P2，2026-08-17）：同天标 [HH:MM]，跨天标 [MM-DD HH:MM]，防相对时间词漂移
            _ts = ""
            try:
                if msg.created_at is not None:
                    from app.utils.timeutil import shift_utc_naive
                    _mt_bj = shift_utc_naive(msg.created_at, 8)
                    _hhmm = f"{_mt_bj.hour:02d}:{_mt_bj.minute:02d}"
                    _ts = f"[{_hhmm}] " if _mt_bj.date() == _bj_today else f"[{_mt_bj.month:02d}-{_mt_bj.day:02d} {_hhmm}] "
            except Exception:
                _ts = ""
            content = msg.content[:200] if len(msg.content) > 200 else msg.content
            # 图片消息：用 extra_meta 里的图片描述 + 配文组装（用户端只显示图片+配文，描述仅 AI 可见）
            if msg.image_url:
                desc_text = ""
                try:
                    meta = _json.loads(msg.extra_meta or "{}")
                    desc_text = (meta.get("image_desc") or {}).get("text", "") or ""
                except Exception:
                    desc_text = ""
                if desc_text:
                    line = f"[\u56fe\u7247\uff0c\u5185\u5bb9\uff1a{desc_text[:120]}]"
                    if content:
                        line += f"\uff08\u7528\u6237\u8bf4\uff1a{content[:80]}\uff09"
                    content = line
                else:
                    content = f"[\u56fe\u7247] {content}" if content else "[\u56fe\u7247]"
            else:
                # 文件/语音消息：extra_meta 摘要/转写文本进 AI 上下文（用户端显示卡片/音频）
                try:
                    meta = _json.loads(msg.extra_meta or "{}")
                except Exception:
                    meta = {}
                if meta.get("file"):
                    f_meta = meta["file"]
                    summary = (f_meta.get("summary") or "").strip()
                    fname = f_meta.get("name") or ""
                    if summary:
                        content = f"[\u6587\u4ef6\u300a{fname}\u300b\uff0c\u5185\u5bb9\u6458\u8981\uff1a{summary[:2000]}]"
                    else:
                        fsize = f_meta.get("size") or ""
                        ftype = f_meta.get("type") or ""
                        content = f"[\u6587\u4ef6\u300a{fname}\u300b\uff08{ftype}{fsize}\uff09]"
                elif meta.get("voice"):
                    v_meta = meta["voice"]
                    tr = (v_meta.get("transcript") or "").strip()
                    if tr:
                        content = f"[\u8bed\u97f3\u6d88\u606f\uff0c\u7528\u6237\u8bf4\uff1a{tr[:200]}]"
                    else:
                        content = "[\u8bed\u97f3\u6d88\u606f\uff08\u6682\u65e0\u6cd5\u8f6c\u5199\uff09]"
            # 完整引用消息 v2.0.0：用户消息若带引用，附加被引用内容供 AI 理解
            try:
                _qmeta = _json.loads(msg.extra_meta or "{}").get("quote")
            except Exception:
                _qmeta = None
            if isinstance(_qmeta, dict) and _qmeta.get("content"):
                _q_sender = _qmeta.get("sender")
                _q_label = user_name if _q_sender == "user" else char_name
                _q_text = str(_qmeta.get("content"))[:100]
                _q_line = f"（引用了{_q_label}的消息：{_q_text}）"
                content = f"{content} {_q_line}" if content else _q_line
            chat_history_lines.append(f"{_ts}{sender}: {content}")
        chat_history = "\n".join(chat_history_lines) or ""

        # 更早消息概要（G-P1-1，2026-08-18：补生成逻辑已抽至 _build_older_summaries，单次最多补 1 天）
        async with async_session_factory() as db:
            older_result = await db.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.session_id == state["session_id"],
                    ChatMessage.created_at < one_day_ago,
                )
                .order_by(ChatMessage.created_at.asc())
                .limit(5000)  # P1 性能（2026-08-16）：防极端历史全量加载
            )
            older_msgs = list(older_result.scalars().all()) + older_extra

        if older_msgs:
            older_summary = await _build_older_summaries(state, older_msgs, char_name, _trim)
            if older_summary:
                if chat_history:
                    chat_history = older_summary + "\n\n---\n\n" + chat_history
                else:
                    chat_history = older_summary

    # P4：世界状态（当前事实折叠，失败静默缺省"无"）
    # P3-1：注册表已执行 world_facts 时跳过内联（含 DB 查询），值由覆盖块用注册表值填充。
    world_facts_text = "无"
    if "world_facts" not in _registry_done:
        try:
            from app.events.facts import get_character_view
            _wv = await get_character_view(state.get("character_id"), state.get("user_id", 1))
            if _wv:
                world_facts_text = _wv
        except Exception as e:
            _logger.warning("World facts inject failed: %s", e)

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
            state.get("character_id"), state.get("user_id", 1), _trim,
        )
        memory_lines = _build_retrieved_memory_lines(state["character_id"], state.get("retrieved_memories", []))
        memories_text = "\n".join(memory_lines) if memory_lines else "\u6682\u65e0"

    # 朋友圈：角色自己最近 1 条 + 用户最近 3 条（近 7 天），让角色记得用户发过的内容（零额外 LLM）
    # P3-1：注册表已执行 moments 时跳过内联（含 DB 查询），值由覆盖块用注册表值填充。
    moments_text = "\u6682\u65e0"
    if "moments" not in _registry_done:
        try:
            moments_lines = []
            async with async_session_factory() as db:
                own_result = await db.execute(
                    select(AIMoment)
                    .where(AIMoment.character_id == state["character_id"], AIMoment.is_active == True)
                    .order_by(AIMoment.created_at.desc())
                    .limit(1)
                )
                own = own_result.scalars().all()
                user_result = await db.execute(
                    select(AIMoment)
                    .where(
                        AIMoment.sender_type == "user",
                        AIMoment.user_id == state.get("user_id", 1),
                        AIMoment.is_active == True,
                        AIMoment.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7),
                    )
                    .order_by(AIMoment.created_at.desc())
                    .limit(3)
                )
                user_moments = user_result.scalars().all()
            if own:
                moments_lines.append(f"[\u4f60\u53d1\u7684 {str(own[0].created_at)[:10]}] {own[0].content[:100]}")
            for m in user_moments:
                moments_lines.append(f"[\u7528\u6237\u53d1\u7684 {str(m.created_at)[:10]}] {m.content[:100]}")
            if moments_lines:
                moments_text = "\n".join(moments_lines)
        except Exception as e:
            _logger.warning("Failed to query moments: %s", e)

    # 宠物信息（只注入：用户养的宠物 + 当前角色自己养的 AI 宠物；
    # 其他角色养的 AI 宠物不注入，防止"别人的宠物被算作自己/用户养的"；只读注入不落库）
    # P3-1：注册表已执行 pets 时跳过内联（含 DB 查询 + 衰减），值由覆盖块用注册表值填充。
    pets_text = "无"
    if "pets" not in _registry_done:
        try:
            from app.models.pet import Pet as PetModel
            from app.application.pet_service import apply_decay as pet_apply_decay, species_label as pet_species_label, species_fact as pet_species_fact
            from sqlalchemy import or_ as _or_
            _uid = state.get("user_id", 1)
            _cid = state.get("character_id")
            async with async_session_factory() as db:
                pets_result = await db.execute(
                    select(PetModel).where(_or_(
                        (PetModel.user_id == _uid) & (PetModel.owner_type.is_(None)),   # 旧数据（无归属）视为用户宠物
                        (PetModel.user_id == _uid) & (PetModel.owner_type == "user"),   # 用户宠物
                        (PetModel.owner_type == "ai") & (PetModel.owner_id == _cid),    # 当前角色自己养的 AI 宠物
                    )).order_by(PetModel.created_at.asc())
                )
                user_pets = pets_result.scalars().all()
            if user_pets:
                pet_lines = []
                for p in user_pets:
                    pet_apply_decay(p)
                    owner_prefix = "你养的" if (p.owner_type == "ai" and p.owner_id == _cid) else "用户家的"
                    pet_lines.append(
                        f"- {owner_prefix}{p.name}（{pet_species_label(p.species)}）：{p.status_text}，"
                        f"饱食度 {p.hunger}%、心情 {p.mood}%、精力 {p.energy}%、清洁度 {p.cleanliness}%"
                        + (f"；习性：{pet_species_fact(p.species)}" if pet_species_fact(p.species) else "")
                    )
                pets_text = "\n".join(pet_lines)
        except Exception as e:
            _logger.warning("Failed to load pets: %s", e)

    storyline_recall = _persona["storyline_recall"]

    character_feelings = _persona["character_feelings"]

    storyline_status = _persona["storyline_status"]

    # 用户情绪感知（P2-1）：轻量规则器，零 token；认知循环开启时优先用感知层结果（等价回退）
    # P3-1：注册表已执行 user_emotion 时跳过内联（规则器检测），值由覆盖块用注册表值填充。
    user_emotion = "无"
    if "user_emotion" not in _registry_done:
        _perception = state.get("perception") or {}
        try:
            emo = _perception.get("emotion") or ""
            if not emo:
                from app.domain.emotion.model import detect_user_emotion
                emo = detect_user_emotion(state.get("user_message", ""))
            if emo:
                user_emotion = emo
        except Exception as e:
            _logger.warning("Failed to detect user emotion: %s", e)

    recent_emotion = _persona["recent_emotion"]

    # 用户八维可视化状态（用户手动设置）：全 50=未设置则跳过；有非默认值才注入（控 token）。
    # G-P2-4（2026-08-18）：独立分区（不再混入「用户情绪」区），与规则器情绪提示分离、各自独立配额
    # P3-1：注册表已执行 user_manual_state 时跳过内联（含 DB 查询），值由覆盖块用注册表值填充。
    user_manual_state = ""
    if "user_manual_state" not in _registry_done:
        try:
            from app.models.user import UserState
            async with async_session_factory() as db:
                _ur = await db.execute(select(UserState).where(UserState.user_id == state.get("user_id", 1)))
                _u = _ur.scalar_one_or_none()
            if _u is not None:
                _cn = {"mood": "心情", "body_temp": "体温", "desire": "性欲", "possessiveness": "占有欲",
                       "fatigue": "疲惫感", "sensitivity": "敏感度", "comfort": "舒适感", "anger": "怒气值"}
                _vals = {k: getattr(_u, k) for k in _cn}
                if any(v != 50 for v in _vals.values()):
                    _parts = [f"{_cn[k]}{v}" for k, v in _vals.items() if v != 50]
                    user_manual_state = _build_user_manual_state_text(_parts)
        except Exception as e:
            _logger.warning("Failed to load user states: %s", e)

    # 手机感知（用户授权采集的屏幕/剪贴板/相册快照，仅注入文本）
    # P3-1：注册表已执行 phone_perception 时跳过内联（含 DB 查询），值由覆盖块用注册表值填充。
    phone_perception = "无"
    if "phone_perception" not in _registry_done:
        try:
            from app.application.phone_service import get_recent_perception_text
            phone_text = await get_recent_perception_text(state.get("user_id", 1))
            if phone_text:
                phone_perception = phone_text
        except Exception as e:
            _logger.warning("Failed to load phone perception: %s", e)

    # 小手机（2026-08-11）：角色日历备注 + 浏览器搜索历史（仅文本注入）
    # P3-1：注册表已执行 phone_desktop 时跳过内联（含 DB 查询），值由覆盖块用注册表值填充。
    phone_desktop = "无"
    if "phone_desktop" not in _registry_done:
        try:
            from app.application.phone_desktop_service import get_phone_desktop_inject_text
            _cid = state.get("character_id")
            if _cid:
                _pdt = await get_phone_desktop_inject_text(int(_cid))
                if _pdt:
                    phone_desktop = _pdt
        except Exception as e:
            _logger.warning("Phone desktop inject failed: %s", e)

    # 进行中的时间承诺（防剧情穿帮：AI 承诺未到期时不得提前演"回来了"；2026-08-14 修复）
    # P3-1：注册表已执行 pending_timer 时跳过内联（含 DB 查询），值由覆盖块用注册表值填充。
    pending_timer_text = "无"
    if "pending_timer" not in _registry_done:
        try:
            from app.scheduling.promise_service import get_pending_timer_text
            _pt = await get_pending_timer_text(state.get("character_id"), state.get("user_id", 1))
            if _pt:
                pending_timer_text = _pt
        except Exception as e:
            _logger.warning("Pending timer inject failed: %s", e)

    # 时间感知（2026-08-08）：北京时间兜底 + 用户本地时区（若上报）+ 距上次互动时长
    # P3-1：注册表已执行 current_time 时跳过内联（含距上次互动 ChatSession 查询），值由覆盖块用注册表值填充。
    current_time_str = ""
    if "current_time" not in _registry_done:
        beijing_tz = timezone(timedelta(hours=8))
        now = datetime.now(beijing_tz)
        weekday_cn = ["\u661f\u671f\u4e00", "\u661f\u671f\u4e8c", "\u661f\u671f\u4e09", "\u661f\u671f\u56db", "\u661f\u671f\u4e94", "\u661f\u671f\u516d", "\u661f\u671f\u65e5"]
        wd = weekday_cn[now.weekday()]
        current_time_str = f"{now.year}\u5e74{now.month}\u6708{now.day}\u65e5 {wd} {now.hour}:{now.minute:02d}\uff08\u5317\u4eac\u65f6\u95f4\uff09"
        try:
            _tz_min = getattr(user, "timezone_offset_minutes", None)
            if _tz_min is not None:
                _local = now + timedelta(minutes=int(_tz_min) - 8 * 60)
                current_time_str += f"\uff5c\u4f60\u90a3\u8fb9 {_local.year}\u5e74{_local.month}\u6708{_local.day}\u65e5 {weekday_cn[_local.weekday()]} {_local.hour}:{_local.minute:02d}"
        except Exception as e:
            _logger.warning("Timezone inject failed: %s", e)
        # S-1 季节/节日注入（2026-08-16）：时间感知补季节与节日，角色言行随节气/节日变化（失败静默）
        try:
            from app.scheduling.holiday_calendar import get_holidays
            _hols = get_holidays(now.date())
            if _hols:
                _hnames = "、".join(h["name"] for h in _hols if h.get("lang") == "zh") or "、".join(h["name"] for h in _hols)
                current_time_str += f"｜今天节日：{_hnames}"
            _mon = now.month
            _season = ("春季" if _mon in (3, 4, 5) else "夏季" if _mon in (6, 7, 8)
                       else "秋季" if _mon in (9, 10, 11) else "冬季")
            current_time_str += f"｜{_season}"
        except Exception as e:
            _logger.warning("Season/holiday inject failed: %s", e)
        # 距上次互动（该用户+角色的最近会话更新时间，主动消息同样覆盖）
        try:
            async with async_session_factory() as db:
                _sr = await db.execute(
                    select(ChatSession)
                    .where(
                        ChatSession.user_id == state.get("user_id", 1),
                        ChatSession.character_id == state["character_id"],
                    )
                    .order_by(ChatSession.updated_at.desc())
                    .limit(1)
                )
                _last_session = _sr.scalar_one_or_none()
            if _last_session is not None and _last_session.updated_at is not None:
                _last_dt = _last_session.updated_at
                if _last_dt.tzinfo is None:
                    _last_dt = _last_dt.replace(tzinfo=timezone.utc)
                _delta = datetime.now(timezone.utc) - _last_dt
                _secs = max(0, int(_delta.total_seconds()))
                if _secs < 60:
                    _ago = "\u521a\u521a"
                elif _secs < 3600:
                    _ago = f"{_secs // 60} \u5206\u949f\u524d"
                elif _secs < 86400:
                    _h, _m = divmod(_secs // 60, 60)
                    _ago = f"{_h} \u5c0f\u65f6 {_m} \u5206\u949f\u524d"
                elif _secs < 172800:
                    _ago = "\u6628\u5929"
                elif _secs < 604800:
                    _ago = f"{_secs // 86400} \u5929\u524d"
                elif _secs < 2592000:
                    _ago = f"{_secs // 604800} \u5468\u524d"
                elif _secs < 31536000:
                    _ago = f"{_secs // 2592000} \u4e2a\u6708\u524d"
                else:
                    _ago = "\u5f88\u4e45"
                current_time_str += f"\uff5c\u8ddd\u4e0a\u6b21\u4e92\u52a8 {_ago}"
        except Exception as e:
            _logger.warning("Last interaction inject failed: %s", e)

    # 位置感知 + 天气（2026-08-08）：用户开启位置信息后注入城市（GPS 反查优先）+ 当前天气（Open-Meteo，30 分钟缓存，失败静默）
    # P3-1：注册表已执行 location（append 块）时跳过内联（含天气服务查询），追加块用 _sv["location"]；
    # 未执行（section 抛异常）时照常内联兜底。
    location_text = ""
    if "location" not in _registry_done:
        try:
            if getattr(user, "location_enabled", False):
                _uloc = getattr(user, "location_city", None) or getattr(user, "user_location", None)
                _aloc = getattr(user, "ai_location", None)
                if getattr(user, "location_follow", False):
                    _aloc = _uloc
                _parts = []
                if _uloc:
                    _parts.append(f"\u7528\u6237\u6240\u5728\u57ce\u5e02\uff1a{_uloc}")
                if _aloc:
                    _parts.append(f"\u4f60\u7684\u4f4d\u7f6e\uff1a{_aloc}")
                if _parts:
                    location_text = (
                        "\u300c\u4f4d\u7f6e\u611f\u77e5\u300d" + "\uff1b".join(_parts)
                        + "\u3002\u53ef\u5728\u804a\u5929\u4e2d\u81ea\u7136\u63d0\u53ca\uff0c\u4f46\u4e0d\u8981\u523b\u610f\u5ff5\u6570\u636e\u3002"
                    )
                # 天气注入：坐标优先，其次城市名；仅注入一句话天气（带缓存，失败静默）
                try:
                    from app.application.weather_service import get_weather_text
                    _wtext = await get_weather_text(
                        getattr(user, "location_lat", None),
                        getattr(user, "location_lng", None),
                        _uloc or "",
                    )
                    if _wtext:
                        location_text += f"\u300c\u5929\u6c14\u300d\u4f60\u90a3\u8fb9\u5f53\u524d\uff1a{_wtext}\u3002\u53ef\u5728\u804a\u5929\u4e2d\u81ea\u7136\u63d0\u53ca\u5929\u6c14\uff0c\u4f46\u4e0d\u8981\u523b\u610f\u5ff5\u6570\u636e\u3002"
                except Exception as _we:
                    _logger.warning("Weather inject failed: %s", _we)
        except Exception as e:
            _logger.warning("Location inject failed: %s", e)

    # 组装 context_messages（注入用户画像：性别/对象/关系，消除刻板印象）
    # P3-1：注册表已执行 user_info 时跳过内联（含用户画像/笔记 DB 查询），最终值用 _sv["user_info"]；
    # 未执行（section 抛异常）时照常内联兜底。
    user_profile_text = ""
    user_notes_text = ""
    if "user_info" not in _registry_done:
        try:
            from app.agent.user_profile import build_user_profile_text
            user_profile_text = await build_user_profile_text(state.get("user_id", 1))
        except Exception:
            user_profile_text = f"用户昵称: {user_name}"
        # 用户备忘录 + 最近日记（用户自己写、供角色阅读；注入失败静默降级）
        try:
            from app.agent.user_profile import build_user_notes_text
            user_notes_text = await build_user_notes_text(state.get("user_id", 1))
        except Exception as e:
            _logger.warning("Load user notes failed: %s", e)
            user_notes_text = ""
    relationship_state = _persona["relationship_state"]

    # 认知循环 v2.1：感知注入 + 规划指令（开关关闭时为空，走旧 prompt）
    # P3-1：注册表已执行 cognitive_plan 时跳过内联（感知/规划指令），值由覆盖块用注册表值填充。
    cognitive_plan = ""
    if "cognitive_plan" not in _registry_done and state.get("cognitive_loop_enabled") and state.get("perception"):
        try:
            from app.agent.perception import build_perception_section
            _sec = build_perception_section(state.get("perception"))
            _hint = (state.get("perception") or {}).get("length_hint") or "medium"
            _len_cn = {"long": "较长", "short": "简短", "medium": "适中"}.get(_hint, "适中")
            cognitive_plan = (
                (_sec + "\n" if _sec else "") +
                "- 开始回复前先在内心判断这次对话的类型与用户情绪，再决定策略（共情陪伴/直接回答/简短回应/认真接住）与篇幅（建议" + _len_cn + "）。\n"
                "- 规划完成后，先单独输出一行策略标记：【策略：<策略名>；长度：<短/中/长>】，再输出正文；每回合只输出一行策略标记。"
            )
        except Exception as e:
            _logger.warning("Cognitive plan build failed: %s", e)
            cognitive_plan = ""

    active_topics_text = _persona["active_topics"]
    identity_profile = _persona.get("identity_profile") or ""

    # 步骤5（注册表）：flag-on 主路径下，分区值已由注册表 section 计算；此处用注册表值覆盖内联计算值
    # （内联值保留，作为 flag-off 回退；两路径必须逐字节一致）。值为未裁剪原始值，后续裁剪块统一处理。
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
            state.get("user_id", 1),
            stream=_is_stream_ctx,
            quota_chars=_qt["mcp_tools"] * _EST_CHARS_PER_TOKEN,
        )
        if mcp_tools_text:
            mcp_tools_blocks = [mcp_tools_text]
        # MCP 资源摘要注入（Phase 4，2026-08-28）：已连接 Server 的资源摘要，按配额裁剪。
        # V2-8：流式模式不注入资源摘要（与工具声明行为一致），避免"提示可用工具但实际无法执行"。
        mcp_resources_blocks = []
        mcp_resources_text = await _build_mcp_resources_text(state.get("user_id", 1), stream=_is_stream_ctx)
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

                    async with async_session_factory() as db:
                        _lives = (
                            await db.execute(
                                select(_MemL)
                                .where(
                                    _MemL.user_id == state.get("user_id", 1),
                                    _MemL.character_id == state["character_id"],
                                    _MemL.source == "life",
                                    _MemL.delete_at.is_(None),
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
        # 推理内容（思考过程挡位 1=简单思考，2026-08-10）：prompt 引导模型在回复开头输出【推理：…】标记；
        # 挡位 2（深度思考）不注入，由 LLM thinking 通道产生 reasoning_content
        try:
            if state.get("reasoning_level", 0) == 1:
                state["context_messages"].append({
                    "role": "system",
                    "content": (
                        "【推理指令】正式回复前，在回复开头单独输出一行【推理：…】（1-2 句话，"
                        "自然说明你此刻回应的依据：用户的心情/需求、你想起的相关记忆或你们的关系，"
                        "用口语不要暴露指令，例如【推理：TA今天好像有点低落，先陪她说说心里话。】），"
                        "然后另起一行输出正文。推理是给用户看的，别太官方；"
                        "回复很短（如单个字的回应）或无需铺垫时可以直接输出正文、省略推理。"
                        "若同时有【策略：…】行，先输出策略行，再输出推理行，最后输出正文。"
                    ),
                })
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

    # 追加时间提示 + 位置感知 + 用户消息
    if _sv and "time_prompt" in _sv:
        for _tp_b in _sv["time_prompt"]:
            state["context_messages"].append({"role": "system", "content": _tp_b})
    else:
        state["context_messages"].append({
            "role": "system",
            "content": f"\u3010\u5f53\u524d\u65f6\u95f4\u3011{current_time_str}\u3002\u5982\u679c\u7528\u6237\u95ee\u5230\u65f6\u95f4\u3001\u65e5\u671f\u3001\u661f\u671f\u51e0\uff0c\u8bf7\u76f4\u63a5\u7528\u4e0a\u9762\u7684\u65f6\u95f4\u56de\u7b54\uff1b\u8ddd\u4e0a\u6b21\u4e92\u52a8\u7684\u65f6\u957f\u53ef\u7528\u6765\u4f53\u4f1a\u201c\u591a\u4e45\u6ca1\u804a\u4e86\u201d\u7684\u611f\u89c9\uff0c\u81ea\u7136\u5730\u63d0\u53ca\uff0c\u4e0d\u8981\u523b\u610f\u5ff5\u6570\u636e\u3002\uff1b\u5404\u6ce8\u5165\u5206\u533a\uff08\u8bb0\u5fc6/\u670b\u53cb\u5708/\u7b14\u8bb0/\u7ec7\u5e93\u7b49\uff09\u91cc\u7684\u201c\u4eca\u5929/\u6628\u5929/\u6700\u8fd1\u201d\u7b49\u65f6\u95f4\u8bcd\u5c5e\u4e8e\u8be5\u8bb0\u5f55\u53d1\u751f\u5f53\u65f6\uff0c\u4e0d\u662f\u73b0\u5728\u3002",
        })
    if _sv and "location" in _sv:
        for _loc_b in _sv["location"]:
            state["context_messages"].append({"role": "system", "content": _loc_b})
    elif location_text:
        state["context_messages"].append({"role": "system", "content": location_text})
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

    # 插件系统：context_inject（启用插件可向上下文追加内容；异常隔离）
    try:
        from app.plugins.registry import run_hook
        await run_hook("context_inject", {
            "user_id": state.get("user_id", 1),
            "character_id": state.get("character_id"),
            "session_id": state.get("session_id"),
            "user_message": state.get("user_message", ""),
            "context_messages": state["context_messages"],
        })
    except Exception:
        pass

    # 48c：配置驱动零代码技能注入（type=prompt 插件触发匹配后追加 system 消息；异常隔离不阻断主链路）
    try:
        from app.plugins.config_hooks import inject_prompt_skill
        await inject_prompt_skill(state)
    except Exception:
        pass

    # G-P1-2（2026-08-18）：system 整体 token 硬顶——所有分区 + 追加 system 块组装完成后，
    # 超限时从尾部裁剪各 system 块（追加块同样生效）；只截断文本、保留消息结构。
    _apply_system_total_quota(state["context_messages"], character_id=state.get("character_id"))
    return state
