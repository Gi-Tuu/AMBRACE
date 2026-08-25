"""MCP tool → AMBRACE ToolSpec 适配（Phase 1）。

- 工具名命名空间：f"mcp.{server_name}.{tool_name}"，避免与内置工具（search/image_gen）和插件 action 冲突。
- scope：f"mcp_{server_name}"（权限粒度到 Server 级；Phase 2 走 ALLOW/ASK/FORBID）。
- 风险等级按工具名关键词推断：write/create/update/delete/remove/execute/send/post → high；
  read/search/list/get/find → low；其余 → medium。
- execute 闭包：调 MCPClientManager.call_tool，把 content 文本提取为 ok/text/raw。
"""
from typing import Any

from app.agent.tools import RISK_HIGH, RISK_LOW, RISK_MEDIUM, ToolSpec

_HIGH_KEYWORDS = ("write", "create", "update", "delete", "remove", "execute", "send", "post")
_LOW_KEYWORDS = ("read", "search", "list", "get", "find")


def infer_risk(tool_name: str) -> str:
    """按工具名关键词推断风险档（read→low，write/delete/execute→high，其余→medium）。"""
    name = (tool_name or "").lower()
    if any(k in name for k in _HIGH_KEYWORDS):
        return RISK_HIGH
    if any(k in name for k in _LOW_KEYWORDS):
        return RISK_LOW
    return RISK_MEDIUM


def _make_mcp_execute(server_id: int, tool_name: str) -> Any:
    """构造 MCP 工具 execute 闭包：调 manager.call_tool（延迟 import 避免循环依赖）。"""

    async def _execute(payload: dict) -> dict:
        from app.mcp.manager import mcp_manager

        result = await mcp_manager.call_tool(server_id, tool_name, payload)
        text_parts = [
            c.get("text", "")
            for c in result.get("content", [])
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        return {
            "ok": not result.get("isError", False),
            "text": "\n".join(text_parts),
            "raw": result,
        }

    return _execute


def mcp_tool_to_spec(server_name: str, mcp_tool: dict, server_id: int) -> ToolSpec:
    """把 MCP tool 描述转换成 AMBRACE ToolSpec。

    MCP tool 格式（mcp 2.0.0：list_tools() 返回的 Tool.input_schema，snake_case）：
    {"name": "read_file", "description": "...", "input_schema": {...}}
    """
    tool_name = str(mcp_tool.get("name") or "")
    full_name = f"mcp.{server_name}.{tool_name}"
    description = mcp_tool.get("description") or f"MCP 工具 {tool_name}（{server_name}）"
    input_schema = mcp_tool.get("input_schema") or mcp_tool.get("inputSchema") or {}
    risk = infer_risk(tool_name)
    return ToolSpec(
        name=full_name,
        description=description,
        risk_level=risk,
        idempotent=(risk == RISK_LOW),
        scope=f"mcp_{server_name}",
        execute=_make_mcp_execute(server_id, tool_name),
        epistemic_status="UNVERIFIED",  # 外部 MCP 工具结果默认未证实
        provenance=f"mcp:{server_name}",
        input_schema=input_schema,
        server_id=server_id,
        max_observation_chars=4000,  # P2-B（2026-08-29）：MCP 返回文本不可控，放宽截断上限
    )
