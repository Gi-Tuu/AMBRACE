"""主动消息上下文装配 — 时间/闲置描述、反思/场景/位置/身份注入、two-pass 现状 trace 构造与入口留痕。

本模块自 ``scheduling/message_generator.py`` 逐字节搬入（A22 ③b，2026-10-02）。边界＝**上下文装配
与状态轨迹**：读库/读 flag 的组装型助手，产出喂给 prompt 的文本块。tests/ 在 ``message_generator``
这个模块对象上对 ``_load_recent_reflection`` / ``_load_state_trace`` 打桩，且 ``_logger`` 留在
message_generator；故被搬走代码引用 ``_logger`` 时，一律在函数体内 ``from app.scheduling import
message_generator as _mg`` 现取 ``_mg._logger``（调用时刻解析，桩才打得上）。message_generator 侧靠
具名重导出保留原调用点，本模块**绝不**在顶层 import message_generator（顶层回指必成环：mg → ctx → mg）。
"""
from datetime import timedelta

from app.utils.timeutil import now_naive_utc
from app.scheduling.message_text import _segment_guard_on


def _describe_now() -> str:
    """生成当前时间的自然语言描述（北京时间，含日期）"""
    now = now_naive_utc()
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
    """反思驱动（Phase J/P1，2026-08-16）：最近一条每日复盘（ai_reflection）注入文本；曾为灰度开关 agent_reflection_inject，2026-09-17 固化常开（恒注入，无复盘则返回空串）。"""
    if not character_id:
        return ""
    # 曾为灰度开关，2026-09-17 固化（用户拍板：功能常驻不下放）：最近复盘恒注入
    try:
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


async def _load_scene_facts(user_id: int | None) -> str:
    """当前场景事实（批次四，2026-09-16）：user_facts.slot='location' + 用户作息活跃时段。

    仅 flag `proactive_segment_guard` 开时调用（关=空串、零 DB 查询/零行为变化）；
    location 槽沿用既有细粒度开关 `user_fact_slot_enabled` 门控（不绕过隐私开关）。
    失败静默返回已收集部分；无数据返回空串（调用方据此不注入）。
    """
    if not _segment_guard_on() or not user_id:
        return ""
    parts: list[str] = []
    try:
        from app.memory.user_facts import get_active_user_facts, user_fact_slot_enabled_for
        # A5（2026-09-19）：按 user_id 解析细槽开关（该账号覆盖优先），不用全局值冒充 per-user
        if await user_fact_slot_enabled_for("location", user_id):
            for _r in await get_active_user_facts(user_id, slots=["location"]):
                _v = (_r.value or "").strip()
                if _v:
                    parts.append(f"TA 现在的位置：{_v}")
    except Exception:
        pass
    try:
        from app.scheduling.user_rhythm import get_active_hours
        _hours = await get_active_hours(user_id)
        _rng = "、".join(
            f"{int(a)}点-{int(b)}点" for a, b in (_hours or []) if a is not None and b is not None)
        if _rng:
            parts.append(f"TA 的作息活跃时段：{_rng}")
    except Exception:
        pass
    return "\n".join(parts)


async def _load_authoritative_user_location(user_id: int | None) -> str:
    """用户权威位置（2026-09-17 批次二任务2.3）：user_facts 共享 location。

    主动消息是最易「用旧现状续写」的通道：生成器必须引用权威位置，而不是靠相似记忆检索出的
    旧碎片（生产实证：DeepSeek 8/18→9/13 数十条「轩在长沙」，用户 8 月底已回湛江）。
    位置槽走独立开关 user_current_location_share（默认开、不吃细槽总闸）。
    无权威值 → 空串（调用方据此不注入，零行为差异）。
    """
    if not user_id:
        return ""
    try:
        from app.memory.user_facts import get_authoritative_user_location
        loc = (await get_authoritative_user_location(user_id) or "").strip()
    except Exception:
        return ""
    if not loc:
        return ""
    return (
        f"用户当前权威位置：{loc}（以此为准；不得写用户在其他城市，"
        "旧记忆里的其他地点一律按过去处理）。"
    )


# ── two-pass POC（2026-09-23，方案书 AMBRACE_two-pass_POC方案 §2）：生成前置「现状 trace」──
# 灰度双条件（沿用 char13 先例）：AGENT_FLAGS["two_pass_trace"] 开 **且** 角色命中本白名单；
# 关/未命中 → 不构造、不注入、不多一次查询（逐字旧行为）。trace 只读、零 LLM、不落库、不上屏。
TWO_PASS_TRACE_GRAY_CHARS = frozenset({13})
TWO_PASS_TRACE_ALL_FLAG = "two_pass_trace_all_chars"   # 运行期总开关（默认关）：开 ⇒ 不再看白名单


def two_pass_trace_allowed(character_id, *, flags=None) -> bool:
    """主开关开 **且**（总开关开 或 角色在白名单里）才允许两遍重读。

    2026-09-26（C12b）：白名单原为硬编码常量 {13}；现补一个运行期总开关，默认关 ⇒
    行为与本批前逐字一致（只有 char13 会用）；放开全量＝由维护者把该 flag 打开。
    两个开关读同一份 flags（默认 AGENT_FLAGS），口径完全一致。
    """
    if flags is None:
        try:
            from app.agent import loop as _loop
            flags = _loop.AGENT_FLAGS
        except Exception:
            return False
    if not bool((flags or {}).get("two_pass_trace", False)):
        return False
    if character_id is None:
        return False
    try:
        char_id = int(character_id)
    except (TypeError, ValueError):
        return False
    if bool((flags or {}).get(TWO_PASS_TRACE_ALL_FLAG, False)):
        return True
    return char_id in TWO_PASS_TRACE_GRAY_CHARS


async def _load_state_trace(character_id, user_id) -> tuple[str, float, bool]:
    """确定性构造现状 trace，返回 (trace 文本, 构造耗时 ms, 是否因异常而空)。

    fail-open：任何异常（含查库失败）→ 空串 + WARNING，主链路照旧生成，绝不冒泡。
    第三位用于把「构造异常被吞掉」与「真·拼空」在数据上分开（低-5）：
    正常返回 ``False``（即便 text 为空也是「真拼空」），``except`` 分支返回 ``True``。
    """
    from app.scheduling import message_generator as _mg
    import time
    t0 = time.perf_counter()
    try:
        from app.db.database import async_session_factory
        from app.scheduling.state_trace import build_state_trace
        async with async_session_factory() as db:
            text = await build_state_trace(db, character_id=character_id, user_id=user_id)
    except Exception as e:
        _mg._logger.warning("Proactive state trace build failed char=%s: %s", character_id, e)
        return "", 0.0, True
    return text or "", (time.perf_counter() - t0) * 1000.0, False


def _prepend_state_trace(messages: list[dict], trace_text: str) -> list[dict]:
    """trace 前置到长历史/系统块之前（论文口径：前置重读才有效，插尾部会被长上下文淹没）。"""
    if not trace_text:
        return messages
    return [{"role": "system", "content": trace_text}, *messages]


def _note_state_trace_injected(character_id, trace_text: str, prompt_len: int, elapsed_ms: float) -> None:
    """影子对照留痕：字段只有 {enabled, trace_len, trace_sha8, prompt_len, elapsed_ms}。

    **绝不落 trace 全文**（POC 中间产物不进生产库正文；hash 够人工回查比对）。
    """
    import hashlib
    try:
        from app.memory.observability import obs_event
        obs_event(character_id, "two_pass_trace", {
            "enabled": True,
            "trace_len": len(trace_text or ""),
            "trace_sha8": hashlib.sha256((trace_text or "").encode("utf-8")).hexdigest()[:8],
            "prompt_len": int(prompt_len),
            "elapsed_ms": round(float(elapsed_ms), 1),
        })
    except Exception:
        pass


# two_pass 入口三态（2026-09-26 派单 Part B）：把「没跑到」与「跑到了但拼空」在数据上分开。
# 判定口径不在此复制——allowed 只由 two_pass_trace_allowed() 给，本模块只做记录。
GATE_ROUTE = "two_pass_gate"        # agent_task_logs.route（与 two_pass_trace 同表同通道）
GATE_NOT_ALLOWED = "not_allowed"    # 开关关 / 角色不在白名单 ⇒ 本趟压根没跑
GATE_EMPTY_TRACE = "empty_trace"    # 跑了但 trace 拼空（异常情形，需一眼看出）
GATE_TRACE_ERROR = "trace_error"    # 查库/构造异常被 fail-open 吞掉（与真·拼空区分）
GATE_INJECTED = "injected"          # 跑了且 trace 非空（详情见既有 two_pass_trace 事件）


def _note_state_trace_gate(character_id, state: str, *, trace_len: int = 0,
                           elapsed_ms: float = 0.0) -> None:
    """入口留痕 + 调用计数：每次 generate_proactive_event 恰好一条，与 trace 是否为空无关。

    与既有注入留痕走同一通道（obs_event → agent_task_logs / trigger=memory_obs），只换 route
    便于聚合：本 route 当**分母**（调用了几次），``two_pass_trace`` 当**分子**（真注入了几次）。
    """
    from app.scheduling import message_generator as _mg
    try:
        from app.memory.observability import obs_event
        obs_event(character_id, GATE_ROUTE, {
            "state": state,
            "allowed": state != GATE_NOT_ALLOWED,
            "trace_len": int(trace_len),
            "elapsed_ms": round(float(elapsed_ms), 1),
        })
    except Exception as e:      # fail-open：留痕坏了不影响生成（obs_event 内部亦已吞一层）
        _mg._logger.warning("Proactive two-pass gate trace failed char=%s: %s", character_id, e)


def _predict_notify_surface(session_id: int | None) -> tuple[bool, str]:
    """生成前判定本轮是否走**通知面**（只读探针，不改发送链、不落库）。

    返回 ``(notify_surface, source)``，``source`` ∈ ``ws_probe / no_session /
    probe_error / flag_off``：
    - **flag 关 ⇒ 不调探针**，返回 ``(False, 'flag_off')`` ⇒ 调用方保持逐字节旧 prompt；
    - ``session_id`` 为空（旧调用点没传） ⇒ **视为通知面 True**；
    - 探针抛错 ⇒ **视为通知面 True**。

    兜底一律取 True 的理由（设计 §1）：主动链的主要落点本就是「用户不在前台」，
    探针坏掉时「多给一句短句提示」的代价（消息变短）远小于「通知里被截断」。
    """
    from app.domain.message_shape import notify_shape_flag_on

    if not notify_shape_flag_on():
        return False, "flag_off"
    if session_id is None:
        return True, "no_session"
    try:
        from app.ws.connection_manager import is_session_online

        return (not is_session_online(session_id)), "ws_probe"
    except Exception:
        return True, "probe_error"


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
