"""受控 Agent Loop（Phase B，2026-08-16）

把 [SEARCH] 的「二次生成」泛化为受控 decide→execute→observe 循环：
- decide：LLM 输出正文 + 动作标记（现有 generate_response / agent.ainvoke）
- execute：解析动作并执行工具（搜索；失败自动重试 1 次，单工具超时 30s）
- observe：工具结果注入为带标注上下文（【搜索结果】），条件满足再决策（补查）

统一限制（方案 5.3）：最多 2 次搜索 / 3 次 LLM 调用；节流或搜索失败、
超限 → 静默降级（剥离标记、不编造成功）。
（agent_loop_search 已于 2026-09-17 固化为恒定受控多轮搜索，不再经 flag 控制。）
"""
import asyncio
from typing import Awaitable, Callable

from app.agent import actions as _actions
from app.utils.logger import get_logger

_logger = get_logger("agent.loop")

# 统一限制（方案 5.3：max_steps=3 含最终回复 → 最多 2 次真实搜索）
MAX_LLM_STEPS = 3  # LLM 调用轮数上限（首轮 + 2 次再决策）
MAX_SEARCH_ROUNDS = MAX_LLM_STEPS - 1
MAX_RECALL_ROUNDS = 1  # Ariadne 模块 B：记忆二跳最多 1 次（防无限检索/拖慢回复；与 SEARCH 二跳同上限）

# 记忆二跳结果注入模板（observe；与 _SEARCH_RESULT_TEMPLATE 同位）
_RECALL_RESULT_TEMPLATE = (
    "【补充记忆】（你主动调取了更早/更相关的记忆，现在直接结合它们与已有上下文回复；"
    "不要说'我查了一下记忆'。若与上方记忆冲突，以时间更晚、认知状态为 FACT 的为准）：\n{result}"
)
TOOL_TIMEOUT_SEC = 30.0  # 单工具执行超时
SEARCH_RETRY = 1  # 只读工具失败自动重试次数（方案 5.2）

# Feature Flag（2026-08-17 开源包基线：全部默认开启；各 Flag 作用/前值/回滚方法见 docs/feature-flags.md）：
# agent_loop_search：2026-09-17 固化——恒定走受控多轮搜索（曾为灰度开关；关=退回旧单次二次生成，现恒定开，不再经 flag 控制）
# agent_loop_scheduler 开=arbiter 主动任务写 AgentTask trace（含 10% 角色 route=scheduler_gray 对比标记 + 灰度角色真实任务记录）；
# agent_loop_chat 开=主链路日历/备忘等本地工具经统一执行入口 execute_tool；
# agent_tool_events（已固化常开，2026-09-17 用户拍板：功能常驻不下放）：工具执行联动织库增量（tool.executed 订阅）
# agent_trace_group 开=群聊角色回应写 AgentTask trace（只写不读可观测）；
# agent_daily_reflection（已固化常开，2026-09-17 用户拍板：功能常驻不下放）：周复盘（每 7 天 1 次）
# agent_reflection_inject（已固化常开，2026-09-17 用户拍板：功能常驻不下放）：主动消息注入最近复盘（反思驱动）
# agent_context_trim 开=认知注入按角色热度裁剪（低频角色缩小日摘要/织库）；
# agent_loop_group_chat 开=群聊回应走统一 Runtime（逐角色 build_context 注入世界认知，知识不串线）；关=旧单次 JSON 链路（Phase E，2026-08-18 全量开启）；
# agent_loop_social 开=渠道/插件主动候选走统一 Runtime（世界认知注入 + 防 hint 污染记忆）；关=旧裸生成链路（Phase E，2026-08-18 全量开启；X5 渠道化时按渠道语义改名，行为不变）；
# agent_social_light_context 开=群聊/渠道社交短回复走轻量上下文（跳过完整世界认知，单次 prompt ≈-64%；F1/F2，2026-08-18 全量开启）；关=全量 build_context（回退）
# AGENT_FLAGS 已下沉到中立模块 app/flags/agent_flags.py（断点 #8：斩断 memory→agent 循环依赖）。
# 这里 re-export 保持 `from app.agent.loop import AGENT_FLAGS` 旧写法可用。
import sys as _sys
from types import ModuleType as _ModuleType

from app.flags import agent_flags as _agent_flags
from app.flags.agent_flags import AGENT_FLAGS


class _AgentLoopModule(_ModuleType):
    """把对旧别名 AGENT_FLAGS 的**赋值**镜像回规范模块。

    只 re-export 不够：`loop.AGENT_FLAGS = {...}`（如既有测试的 monkeypatch.setattr）会改写旧
    别名的绑定，而 memory 侧已改读规范模块，两侧会分裂成两个 dict、开关拨了不生效。
    """

    def __setattr__(self, name, value):
        if name == "AGENT_FLAGS":
            _agent_flags.AGENT_FLAGS = value
        super().__setattr__(name, value)


_sys.modules[__name__].__class__ = _AgentLoopModule

# 搜索结果注入模板（与旧文案唯一差异：第 3 点允许结果不足时补查 1 次）
_SEARCH_RESULT_TEMPLATE = (
    "【搜索结果】（你已经搜索完成，现在直接基于这些真实信息回复；不要说自己去'搜索了'）。\n"
    "{result}\n\n"
    "注意：1. 如果结果有用，自然引用回答用户；2. 如果结果与问题无关或质量差，说明没查到靠谱的并给出你自己的看法（例如'网上说法不太靠谱，我估计…'）；"
    "3. 你已经搜索完成，绝不要说'我去搜一下/等着我去查'这类话；如果这次结果仍不够或与问题无关，可以再输出一次 [SEARCH] 补充查询（最多再查 1 次），否则不要再输出 [SEARCH] 标记。"
    "4. 网络信息属未证实来源（Observation: UNVERIFIED），涉及事实/数字/做法请谨慎转述，不确定就说明是'网上说法'。"
)

# 搜索结果注入模板（S1 发起方口径，2026-09-27；initiator="self"＝角色自主搜索专用）
# 与 user 版的口径差异只有一处：自主搜索没人等着回复，结果只是参考——
# 模型可以「什么都不说」，此时不得再输出任何正文或标记，由 run_search_loop 判定本轮不产出消息。
_SEARCH_RESULT_TEMPLATE_SELF = (
    "【搜索结果】（这是你自己起意去查的资料，只作参考；没有人在等你回复）。\n"
    "{result}\n\n"
    "注意：1. 与你本来想说的内容相关就自然引用，不必交代来源；2. 结果用不上时你可以什么都不说——"
    "决定不说就不要再输出任何正文或标记（留空即可，本轮不会发出消息）；"
    "3. 无论说不说，都不要说'我去搜一下/等着我去查'这类话；如果这次结果仍不够或与问题无关，可以再输出一次 [SEARCH] 补充查询（最多再查 1 次），否则不要再输出 [SEARCH] 标记。"
    "4. 网络信息属未证实来源（Observation: UNVERIFIED），涉及事实/数字/做法请谨慎转述，不确定就说明是'网上说法'。"
)

# 「本轮不产出消息」语义键（S1；self 分支专用）：调用方据此不追加消息（不替换已有消息、不报错）
SEARCH_NO_MESSAGE_KEY = "search_no_message"


async def _execute_search_tool(user_id: int, query: str, run_search: Callable[[str], Awaitable[str]]) -> dict:
    """经统一工具执行入口调用搜索（Phase E：权限三档 + 工具生命周期钩子 + 幂等重试 + 异常隔离）。

    - 单工具超时 30s 由本层 wait_for 保证（超时 → execute_tool 捕获为 error）；
    - forbid → blocked（搜索被权限拦截）；ask → search 为只读低风险自动放行（不挂起询问）；
    - run_search 返回空串不算异常，空结果重试由 run_search_loop 外层控制（SEARCH_RETRY）。
    """
    from app.agent import tools as _tools
    from app.agent.tool_runner import execute_tool
    _spec = _tools.get_tool("search")
    if _spec is None:
        return {"status": "error", "error": "search tool not registered"}
    _exec_spec = _tools.ToolSpec(
        name=_spec.name,
        description=_spec.description,
        action_type=_spec.action_type,
        risk_level=_spec.risk_level,
        rate_limit=_spec.rate_limit,
        idempotent=_spec.idempotent,
        scope=_spec.scope,
        ask_auto_allow=_spec.ask_auto_allow,
        epistemic_status=_spec.epistemic_status,
        provenance=_spec.provenance,
        execute=lambda payload: asyncio.wait_for(run_search(payload.get("query") or ""), timeout=TOOL_TIMEOUT_SEC),
    )
    return await execute_tool(_exec_spec, {"query": query}, user_id=user_id, character_id=None, session_id=None)


async def run_recall_loop(
    final_state: dict,
    *,
    user_id: int,
    character_id: int,
    gate: Callable[[], object] | None = None,
    tz_offset_min: int | None = None,
) -> tuple[dict, list[dict]]:
    """记忆二跳受控循环（Ariadne 模块 B，2026-09-04）：decide → [RECALL] → 本地记忆检索 → observe 注入 → 再决策 1 次。

    ``tz_offset_min``（F-3，2026-09-04）：用户本地时区分钟偏移，透传给
    ``parse_time_range``。「时间=YYYY-MM」走绝对自然月不受时区影响（二跳绝对月路径不改），
    透传仅与主检索相对时间口径保持一致；None 时回退 UTC（零行为变化）。

    - 镜像 run_search_loop（零新框架）；非流式专用——流式路径由调用方仅做标记剥离（与 SEARCH 同策略）；
    - flag ``memory_recall_second_hop`` 默认关：不检索、不注入、只剥离标记（零行为变化）；
    - 标记内轻量时间语法「时间=YYYY-MM；查询」→ parse_time_range 解析（失败回退纯语义）；
    - 查询失败/无命中/gate 不通过 → 剥离标记静默降级（不编造「想起来了」）；二跳触发/命中写 trace 步骤；
    - gate 支持 sync/async callable（角色 memory_v2_enabled 关闭时不开放二跳）。
    """
    import re as _re
    steps: list[dict] = []
    try:
        # 二跳最多 MAX_RECALL_ROUNDS(=1) 次：与 run_search_loop 的 rounds 语义一致（无 +1）；
        # 超限时残留的 [RECALL] 由循环后兜底剥离（幂等）
        for _ in range(MAX_RECALL_ROUNDS):
            clean, q = _actions.extract_recall(final_state.get("ai_response") or "")
            if not q:
                final_state["ai_response"] = clean
                break
            if not bool(AGENT_FLAGS.get("memory_recall_second_hop", False)):
                final_state["ai_response"] = clean
                break
            if gate is not None:
                _ok = gate()
                if asyncio.iscoroutine(_ok):
                    _ok = await _ok
                if not _ok:
                    final_state["ai_response"] = clean
                    break
            # 拆「时间=YYYY-MM；查询」轻量语法（解析失败回退纯语义，绝不猜）
            t_range = None
            qq = q
            tm = _re.match(r"\s*时间\s*[=:]\s*([0-9]{4}[-年/.][0-9]{1,2})[；;，,\s]+(.*)", q, _re.S)
            if tm:
                from app.memory.time_query import parse_time_range
                # F-3：透传用户时区偏移（「时间=YYYY-MM」绝对自然月不受影响，与主检索口径一致）
                t_range = parse_time_range(tm.group(1), tz_offset_min=tz_offset_min)
                qq = tm.group(2).strip() or q
            from app.memory import search_memories
            _hop_limit = 6  # 曾为 flag（memory_recall_hop_limit）；因热切通道只支持 bool，热切会静默把 6 变成 1，故固化为常量；要调参改这里
            hits = await search_memories(
                character_id=character_id,
                query=qq,
                limit=_hop_limit,
                time_range=t_range,
                user_id=user_id,  # A2 M0-4：透传调用者（memory_search hook ctx）
                trace_meta={"user_id": user_id, "trigger": "recall_second_hop"},
            )
            steps.append({"action": "RECALL", "query": qq[:80], "n": len(hits)})
            if not hits:
                # 没查到：不再二跳，用首轮正文（不编造「想起来了」）
                final_state["ai_response"] = clean
                break
            # 命中即复习（与第一跳一致，24h 防抖在 reinforce_memories 内）；失败不影响注入
            try:
                from app.memory.service import reinforce_memories
                from app.memory.constants import REINFORCE_FACTOR_RETRIEVE, REINFORCE_DEBOUNCE_HOURS
                await reinforce_memories(
                    [h["id"] for h in hits],
                    factor=REINFORCE_FACTOR_RETRIEVE,
                    debounce_hours=REINFORCE_DEBOUNCE_HOURS,
                )
            except Exception:
                pass
            from app.memory.format import format_memory_line
            block = "\n".join(format_memory_line(h, include_speaker=True) for h in hits)
            final_state["context_messages"] = final_state.get("context_messages") or []
            final_state["context_messages"] = final_state["context_messages"] + [{
                "role": "system",
                "content": _RECALL_RESULT_TEMPLATE.format(result=block),
            }]
            final_state["ai_response"] = ""
            from app.agent.nodes import generate_response as _regen
            final_state = await _regen(final_state)  # 主模型再生成 1 次（与 SEARCH 二跳同成本）
            # 下一轮循环开头 extract_recall 负责识别再次调取标记（超上限时兜底剥离，幂等）
        # 兜底：最后一次剥离（幂等）
        final_state["ai_response"] = _actions.extract_recall(final_state.get("ai_response") or "")[0]
    except Exception as e:
        _logger.warning("Agent recall loop failed char=%s: %s", character_id, e)
        try:
            final_state["ai_response"] = _actions.extract_recall(final_state.get("ai_response") or "")[0]
        except Exception:
            pass
    return final_state, steps


async def run_search_loop(
    final_state: dict,
    *,
    user_id: int,
    character_id: int,
    run_search: Callable[[str], Awaitable[str]],
    throttle: Callable[[int], bool],
    inject_enabled: Callable[[], bool],
    save_history: Callable[[int, str], Awaitable[None]],
    max_steps: int | None = None,
    initiator: str = "user",
) -> tuple[dict, list[dict]]:
    """受控搜索循环：decide → 执行 SEARCH → observe（注入结果）→ 条件再决策。

    - final_state 已含首轮 LLM 输出（agent.ainvoke 结果）；本函数处理其后所有 [SEARCH] 动作；
    - 返回 (final_state, steps)：steps 为每轮搜索执行摘要（供 Task Trace）；
    - 节流/开关不通过、搜索失败 → 剥离标记静默降级（不编造成功）；
    - 超过搜索轮数上限 LLM 仍输出 [SEARCH] → 剥离标记直接返回。

    发起方口径（S1，2026-09-27；``initiator``）：
    - ``"user"``（默认，用户请求）：结果必须落到回复里，**不许静默**——再生成为空时回落到
      上一轮正文（剥离标记后的模型自述文本）；模板用 _SEARCH_RESULT_TEMPLATE（文案逐字未变）。
      非 "self" 的取值一律按 user 处理（宁可不静默，也不误吞用户的回复）。
    - ``"self"``（角色自主）：结果只是参考，模板用 _SEARCH_RESULT_TEMPLATE_SELF；
      模型选择「什么都不说」（再生成结果为空/只剩标记）时，按用户拍板口径给出
      **本轮不产出消息** 的语义（final_state[SEARCH_NO_MESSAGE_KEY] = True），
      不回填上一轮正文、不报错、也不替换任何已有消息。
    """
    steps: list[dict] = []
    rounds = MAX_SEARCH_ROUNDS
    # agent_loop_search：2026-09-17 固化为恒定受控多轮搜索（曾为灰度开关；关=退回旧单次二次生成，现恒定开）
    if max_steps is not None:
        rounds = max(1, min(max_steps - 1, MAX_SEARCH_ROUNDS))
    self_initiated = initiator == "self"
    prev_body = ""  # 各轮「剥离标记后的模型正文」，仅 user 分支用于空生成回落

    def _no_silence(text: str) -> str:
        """user 分支不许静默：本轮生成为空则回落上一轮正文；self 分支原样返回（允许不说）"""
        if self_initiated or (text or "").strip():
            return text
        return prev_body

    try:
        round_no = 1
        while round_no <= rounds:
            clean, query = _actions.extract_search(final_state.get("ai_response") or "")
            if not query:
                final_state["ai_response"] = _no_silence(clean)
                break
            # 节流 / 搜索注入开关门禁（与旧行为一致）
            if not (throttle(user_id) and inject_enabled()):
                final_state["ai_response"] = _no_silence(clean)
                break
            _logger.info("AI web search char=%d round=%d query=%s initiator=%s", character_id, round_no, query[:60], initiator)
            # 执行搜索（Phase E：统一工具执行入口 execute_tool——权限三档 + 生命周期钩子 + 异常隔离；
            # 空结果重试 1 次由本层控制，单工具超时 30s）
            result = ""
            blocked = False
            for attempt in range(SEARCH_RETRY + 1):
                _res = await _execute_search_tool(user_id, query, run_search)
                if _res.get("status") == "blocked":
                    blocked = True
                    _logger.info("AI web search blocked round=%d query=%s: %s", round_no, query[:60], _res.get("error"))
                    break
                result = (_res.get("result") or "") if _res.get("status") == "ok" else ""
                if result:
                    break
            steps.append({"action": "SEARCH", "query": query[:80], "ok": bool(result), "round": round_no})
            if blocked or not result:
                _logger.warning("AI web search %s round=%d query=%s: 降级为剥离标记", "blocked" if blocked else "failed", round_no, query[:60])
                final_state["ai_response"] = _no_silence(clean)
                break
            # observe：落浏览记录 + 注入结果 → 再决策（允许补查）
            try:
                await save_history(character_id, query)
            except Exception as e:
                _logger.warning("AI search history save failed: %s", e)
            if clean.strip():
                prev_body = clean
            _template = _SEARCH_RESULT_TEMPLATE_SELF if self_initiated else _SEARCH_RESULT_TEMPLATE
            final_state["context_messages"] = final_state.get("context_messages") or []
            final_state["context_messages"] = final_state["context_messages"] + [{
                "role": "system",
                "content": _template.format(result=result),
            }]
            final_state["ai_response"] = ""
            from app.agent.nodes import generate_response as _regen
            final_state = await _regen(final_state)
            # 不再立即剥离：下一轮循环开头的 extract_search 负责识别补查标记；
            # 无补查时下一轮以 clean 退出；超限时由循环后的兜底剥离（幂等）。
            round_no += 1
        # 超限/退出兜底：最后一次剥离（幂等）
        final_state["ai_response"] = _actions.extract_search(final_state.get("ai_response") or "")[0]
        if self_initiated:
            # 「不说」＝本轮不产出消息（不回填 prev_body、不报错）；有正文则维持正常返回
            if not (final_state.get("ai_response") or "").strip():
                final_state[SEARCH_NO_MESSAGE_KEY] = True
        else:
            final_state["ai_response"] = _no_silence(final_state["ai_response"])
    except Exception as e:
        _logger.warning("Agent search loop failed: %s", e)
        try:
            final_state["ai_response"] = _actions.extract_search(final_state.get("ai_response") or "")[0]
        except Exception:
            pass
    return final_state, steps
