"""MCP 工具标记执行（Phase 2，2026-08-26 / A1 流式扩展，2026-08-27）。

LLM 经 context_builder 注入声明后，可输出 [mcp.<server>.<tool>]{JSON 参数}[/mcp.<server>.<tool>]
标记。本模块把这些标记解析并按完整工具名路由到 ToolRunner.execute_tool（复用权限三档 /
生命周期钩子 / 异常隔离 / Observation 标注），与既有工具调用路径一致，不改主链路行为。

A1（#59 流式路径 MCP 工具循环）：新增 ``run_stream_mcp_tool_stage``，从流式 ``raw_response``
（而非已剥离的 ``ai_response``）解析 mcp.* 标记并执行，返回逐工具的详细结果（tool/ok/summary/error），
供聊天服务层经独立流尾事件（``tool_result``）推给前端。``run_mcp_tool_stage`` 保持原签名不变。
"""
import asyncio

from app.utils.logger import get_logger

_logger = get_logger("agent.mcp_tools")

TOOL_TIMEOUT_SEC = 30.0  # 单工具执行超时（与 loop / runtime 对齐）


async def _execute_mcp_actions(
    text: str,
    state: dict,
    steps: list[dict],
    *,
    user_id: int | None,
    character_id: int | None,
    session_id: int | None,
    timeout: float = TOOL_TIMEOUT_SEC,
    collect_results: bool = False,
) -> tuple[bool, list[dict]]:
    """解析 ``text`` 中的 mcp.* 标记并逐一执行（ToolRunner.execute_tool）。

    返回 ``(executed_any, results)``：
    - ``executed_any``：是否至少执行成功一个（调用方可据此再决策一次）；
    - ``results``：``collect_results=True`` 时逐工具返回 ``{tool, ok, summary, error}`` 详情，
      （非流式/历史调用路径传 False，保留旧布尔语义，零行为变化）。
    无 mcp.* 标记 → ``(False, [])``。
    """
    from app.agent import actions as _actions
    from app.agent.tools import get_tool
    from app.agent.tool_runner import execute_tool

    mcp_actions = [a for a in _actions.parse_actions(text) if a.action_type.startswith("mcp.")]
    if not mcp_actions:
        return False, []
    executed_any = False
    results: list[dict] = []
    for act in mcp_actions:
        spec = get_tool(act.action_type)
        if spec is None or not spec.enabled:
            continue
        _promise = {
            "tool": act.action_type, "ok": False,
            "summary": "", "error": None,
        }
        try:
            res = await asyncio.wait_for(
                execute_tool(
                    spec, dict(act.payload or {}),
                    user_id=user_id, character_id=character_id, session_id=session_id,
                ),
                timeout=timeout,
            )
        except Exception as e:
            _logger.warning("mcp tool execute failed %s: %s", act.action_type, e)
            steps.append({"action": act.action_type, "ok": False})
            _promise.update({"ok": False, "summary": "执行失败", "error": str(e)})
            results.append(_promise)
            continue
        ok = bool(res.get("status") == "ok")
        steps.append({"action": act.action_type, "ok": ok})
        obs = (res.get("observation") or {}).get("summary") or ""
        _promise.update({
            "ok": ok,
            "summary": (obs or ("执行成功" if ok else "执行失败")),
            "error": None,
        })
        results.append(_promise)
        state["context_messages"] = state.get("context_messages") or []
        state["context_messages"] = state["context_messages"] + [{
            "role": "system",
            "content": f"【工具结果】MCP 工具 {act.action_type} 已执行完成：{obs}"
                       "（基于真实结果继续回复，不要说'我去执行了'）。",
        }]
        if ok:
            executed_any = True
    return executed_any, (results if collect_results else [])


async def run_mcp_tool_stage(
    state: dict,
    steps: list[dict],
    *,
    user_id: int | None,
    character_id: int | None,
    session_id: int | None,
    timeout: float = TOOL_TIMEOUT_SEC,
) -> bool:
    """执行 LLM 输出的 mcp.* 工具标记（经 ToolRunner.execute_tool）。非流式主链路入口。

    - 无 mcp.* 标记 → 返回 False（零行为变化）；
    - 每个标记按完整工具名查 ToolRegistry；未注册/禁用跳过（不编造成功）；
    - 执行成功后注入【工具结果】observation；返回是否至少执行完一个（调用方可据此再决策一次）。

    与 A1 新增的 ``run_stream_mcp_tool_stage`` 共用 ``_execute_mcp_actions``（本函数不收集结果详情）。
    """
    executed_any, _ = await _execute_mcp_actions(
        state.get("ai_response") or "", state, steps,
        user_id=user_id, character_id=character_id, session_id=session_id,
        timeout=timeout, collect_results=False,
    )
    return executed_any


async def run_stream_mcp_tool_stage(
    state: dict,
    steps: list[dict],
    *,
    user_id: int | None,
    character_id: int | None,
    session_id: int | None,
    timeout: float = TOOL_TIMEOUT_SEC,
) -> tuple[bool, list[dict]]:
    """流式路径 MCP 工具执行（A1，#59）：从 ``raw_response``（未剥离标记）解析并执行。

    流式路径 ``_run_agent_core`` 的 ``ai_response`` 已被 ``strip_stream_display`` 剥离全部标记，
    无法用于解析 mcp.* 标记；改用 ``state["raw_response"]``（真流式生成节点写入，含全部原始标记）
    作为标记来源，避免把工具调用当普通正文处理。

    返回 ``(executed_any, results)``：``results`` 为逐工具结果详情（tool/ok/summary/error），
    聊天服务层据此经独立流尾事件 ``tool_result`` 推给前端（前端观察区可折叠展示）。
    """
    text = state.get("raw_response") or state.get("ai_response") or ""
    return await _execute_mcp_actions(
        text, state, steps,
        user_id=user_id, character_id=character_id, session_id=session_id,
        timeout=timeout, collect_results=True,
    )
