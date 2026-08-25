"""MCP 工具标记执行（Phase 2，2026-08-26）。

LLM 经 context_builder 注入声明后，可输出 [mcp.<server>.<tool>]{JSON 参数}[/mcp.<server>.<tool>]
标记。本模块把这些标记解析并按完整工具名路由到 ToolRunner.execute_tool（复用权限三档 /
生命周期钩子 / 异常隔离 / Observation 标注），与既有工具调用路径一致，不改主链路行为。
"""
import asyncio

from app.utils.logger import get_logger

_logger = get_logger("agent.mcp_tools")

TOOL_TIMEOUT_SEC = 30.0  # 单工具执行超时（与 loop / runtime 对齐）


async def run_mcp_tool_stage(
    state: dict,
    steps: list[dict],
    *,
    user_id: int | None,
    character_id: int | None,
    session_id: int | None,
    timeout: float = TOOL_TIMEOUT_SEC,
) -> bool:
    """执行 LLM 输出的 mcp.* 工具标记（经 ToolRunner.execute_tool）。

    - 无 mcp.* 标记 → 返回 False（零行为变化）；
    - 每个标记按完整工具名查 ToolRegistry；未注册/禁用跳过（不编造成功）；
    - 执行成功后注入【工具结果】observation；返回是否至少执行完一个（调用方可据此再决策一次）。
    """
    from app.agent import actions as _actions
    from app.agent.tools import get_tool
    from app.agent.tool_runner import execute_tool

    text = state.get("ai_response") or ""
    mcp_actions = [a for a in _actions.parse_actions(text) if a.action_type.startswith("mcp.")]
    if not mcp_actions:
        return False
    executed_any = False
    for act in mcp_actions:
        spec = get_tool(act.action_type)
        if spec is None or not spec.enabled:
            continue
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
            continue
        ok = bool(res.get("status") == "ok")
        steps.append({"action": act.action_type, "ok": ok})
        if ok:
            executed_any = True
            obs = (res.get("observation") or {}).get("summary") or ""
            state["context_messages"] = state.get("context_messages") or []
            state["context_messages"] = state["context_messages"] + [{
                "role": "system",
                "content": f"【工具结果】MCP 工具 {act.action_type} 已执行完成：{obs}"
                           "（基于真实结果继续回复，不要说'我去执行了'）。",
            }]
    return executed_any
