"""受控 Agent Loop（Phase B，2026-08-16）

把 [SEARCH] 的「二次生成」泛化为受控 decide→execute→observe 循环：
- decide：LLM 输出正文 + 动作标记（现有 generate_response / agent.ainvoke）
- execute：解析动作并执行工具（搜索；失败自动重试 1 次，单工具超时 30s）
- observe：工具结果注入为带标注上下文（【搜索结果】），条件满足再决策（补查）

统一限制（方案 5.3）：最多 2 次搜索 / 3 次 LLM 调用；节流或开关不通过、搜索失败、
超限 → 静默降级（剥离标记、不编造成功）。Feature Flag agent_loop_search=False 时
退回旧单次「搜索→二次生成」行为（search_rounds=1）。
"""
import asyncio
from typing import Awaitable, Callable

from app.agent import actions as _actions
from app.utils.logger import get_logger

_logger = get_logger("agent.loop")

# 统一限制（方案 5.3：max_steps=3 含最终回复 → 最多 2 次真实搜索）
MAX_LLM_STEPS = 3  # LLM 调用轮数上限（首轮 + 2 次再决策）
MAX_SEARCH_ROUNDS = MAX_LLM_STEPS - 1
TOOL_TIMEOUT_SEC = 30.0  # 单工具执行超时
SEARCH_RETRY = 1  # 只读工具失败自动重试次数（方案 5.2）

# Feature Flag（2026-08-17 开源包基线：全部默认开启；各 Flag 作用/前值/回滚方法见 docs/feature-flags.md）：
# agent_loop_search 开=受控搜索循环（最多补查 1 次、失败静默降级）；关=退回旧单次二次生成；
# agent_loop_scheduler 开=arbiter 主动任务写 AgentTask trace（含 10% 角色 route=scheduler_gray 对比标记 + 灰度角色真实任务记录）；
# agent_loop_chat 开=主链路日历/备忘等本地工具经统一执行入口 execute_tool；
# agent_tool_events 开=工具执行联动织库增量（tool.executed 订阅）；
# agent_trace_group 开=群聊角色回应写 AgentTask trace（只写不读可观测）；
# agent_daily_reflection 开=周复盘（每 7 天 1 次，2026-08-17 转全量）；
# agent_reflection_inject 开=主动消息注入最近复盘（反思驱动）；
# agent_context_trim 开=认知注入按角色热度裁剪（低频角色缩小日摘要/织库）
AGENT_FLAGS = {
    "agent_loop_search": True,
    "agent_loop_scheduler": True,  # 2026-08-17 全量基线（开源包）：主动任务 trace + 10% 灰度 route 对比
    "agent_loop_chat": True,
    "agent_tool_events": True,  # 2026-08-17 全量基线（开源包）：工具执行联动织库增量
    "agent_trace_group": True,
    "agent_daily_reflection": True,  # 2026-08-17 全量基线（开源包）：周复盘
    "agent_reflection_inject": True,
    "agent_context_trim": True,  # 认知注入按角色热度裁剪（2026-08-16）
    "agent_daily_memory_maintenance": True,  # 日终记忆维护（P0-5，2026-08-16：日摘要补生成+去重+置顶摘要补生成，默认开）
}

# 搜索结果注入模板（与旧文案唯一差异：第 3 点允许结果不足时补查 1 次）
_SEARCH_RESULT_TEMPLATE = (
    "【搜索结果】（你已经搜索完成，现在直接基于这些真实信息回复；不要说自己去'搜索了'）。\n"
    "{result}\n\n"
    "注意：1. 如果结果有用，自然引用回答用户；2. 如果结果与问题无关或质量差，说明没查到靠谱的并给出你自己的看法（例如'网上说法不太靠谱，我估计…'）；"
    "3. 你已经搜索完成，绝不要说'我去搜一下/等着我去查'这类话；如果这次结果仍不够或与问题无关，可以再输出一次 [SEARCH] 补充查询（最多再查 1 次），否则不要再输出 [SEARCH] 标记。"
    "4. 网络信息属未证实来源（Observation: UNVERIFIED），涉及事实/数字/做法请谨慎转述，不确定就说明是'网上说法'。"
)


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
) -> tuple[dict, list[dict]]:
    """受控搜索循环：decide → 执行 SEARCH → observe（注入结果）→ 条件再决策。

    - final_state 已含首轮 LLM 输出（agent.ainvoke 结果）；本函数处理其后所有 [SEARCH] 动作；
    - 返回 (final_state, steps)：steps 为每轮搜索执行摘要（供 Task Trace）；
    - 节流/开关不通过、搜索失败 → 剥离标记静默降级（不编造成功）；
    - 超过搜索轮数上限 LLM 仍输出 [SEARCH] → 剥离标记直接返回。
    """
    steps: list[dict] = []
    rounds = MAX_SEARCH_ROUNDS
    if not AGENT_FLAGS.get("agent_loop_search", True):
        rounds = 1  # Feature Flag 关：退回旧单次二次生成
    if max_steps is not None:
        rounds = max(1, min(max_steps - 1, MAX_SEARCH_ROUNDS))
    try:
        round_no = 1
        while round_no <= rounds:
            clean, query = _actions.extract_search(final_state.get("ai_response") or "")
            if not query:
                final_state["ai_response"] = clean
                break
            # 节流 / 搜索注入开关门禁（与旧行为一致）
            if not (throttle(user_id) and inject_enabled()):
                final_state["ai_response"] = clean
                break
            _logger.info("AI web search char=%d round=%d query=%s", character_id, round_no, query[:60])
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
                final_state["ai_response"] = clean
                break
            # observe：落浏览记录 + 注入结果 → 再决策（允许补查）
            try:
                await save_history(character_id, query)
            except Exception as e:
                _logger.warning("AI search history save failed: %s", e)
            final_state["context_messages"] = final_state.get("context_messages") or []
            final_state["context_messages"] = final_state["context_messages"] + [{
                "role": "system",
                "content": _SEARCH_RESULT_TEMPLATE.format(result=result),
            }]
            final_state["ai_response"] = ""
            from app.agent.nodes import generate_response as _regen
            final_state = await _regen(final_state)
            # 不再立即剥离：下一轮循环开头的 extract_search 负责识别补查标记；
            # 无补查时下一轮以 clean 退出；超限时由循环后的兜底剥离（幂等）。
            round_no += 1
        # 超限/退出兜底：最后一次剥离（幂等）
        final_state["ai_response"] = _actions.extract_search(final_state.get("ai_response") or "")[0]
    except Exception as e:
        _logger.warning("Agent search loop failed: %s", e)
        try:
            final_state["ai_response"] = _actions.extract_search(final_state.get("ai_response") or "")[0]
        except Exception:
            pass
    return final_state, steps
