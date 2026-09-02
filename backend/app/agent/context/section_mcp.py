"""MCP section（试水，试点 A）：MCP 工具声明 / 资源摘要注入（append 块）。

从 ``context_builder`` 迁出，逻辑与旧版完全一致（零行为变化）：
- ``stream=True`` 不注入（P2-A：流式路径不执行 MCP 工具，避免「幻觉式工具调用」）→ 逻辑保留在 *_text 入口；
- 无工具/无资源返回空串 → 不追加块；
- ``owned_server_ids`` 多用户隔离（P1 归属过滤）+ 权限三档（非 forbid）。
"""
from __future__ import annotations

import logging

from app.agent.context.sections import ContextSection, register_section, TARGET_APPEND

_logger = logging.getLogger("agent.context.section_mcp")

# MCP 工具声明注入配额（Phase 2，2026-08-26）
_MCP_TOOLS_QUOTA_TOKENS = 800
# MCP 资源摘要注入配额（Phase 4，2026-08-28）
_MCP_RESOURCES_QUOTA_TOKENS = 400


# ------------------------------------------------------------------ MCP 工具声明注入（Phase 2，2026-08-26）
# 让 Agent 能「看见」并调用已启用且非 FORBID 的 mcp.* 工具：把 input_schema 透传为 JSON 工具声明
# （name/description/parameters），并给出调用标记格式。无 MCP 工具时返回空串（零行为变化）。

async def _build_mcp_tool_declarations(user_id: int, *, stream: bool = False) -> list[dict]:
    """收集当前用户可用的 MCP 工具声明（enabled、权限非 forbid、且属于本用户）。

    返回 [{name, description, parameters}]；parameters 透传 MCP input_schema。

    P1（多用户隔离，2026-08-29）：遍历全局 list_tools() 时按 server_id 归属过滤 —— 只注入
    当前用户拥有的 mcp server（owned_server_ids）。其他用户 server 的低风险工具不再注入，
    也无法被 AI 调用（tool_runner.check_tool_permission 另有归属校验兜底）。

    P2-A（流式不执行，2026-08-29）：`stream=True`（流式路径，见 chat_service._run_agent_core 仅在
    非流式触发 run_mcp_tool_stage）时不注入任何声明 —— 若注入，AI 会输出工具标记但实际不执行，
    产生「幻觉式工具调用」。短期方案：流式不注入；中期做流尾推送通道后再注入。
    """
    from app.mcp.ownership import owned_server_ids
    from app.agent.tools import list_tools

    # P2-A：流式模式不注入 MCP 工具声明（流式路径不执行 MCP 工具，见 chat_service L813）
    if stream:
        return []

    try:
        from app.application import permission_service
    except Exception:
        permission_service = None

    # P1：先查当前用户拥有的 mcp server id 集合（每次查库；查询失败返回空集 → fail-closed）
    owned_ids = await owned_server_ids(user_id)
    decls = []
    for spec in list_tools():
        if not spec.name.startswith("mcp."):
            continue
        if not spec.enabled:
            continue
        sid = getattr(spec, "server_id", None)
        # P1：非本用户 server 的工具（含无归属的兜底）一律不注入 —— 多用户隔离
        if sid not in owned_ids:
            continue
        mode = "allow"
        if permission_service is not None and spec.scope:
            try:
                mode = await permission_service.check_mcp_mode(
                    user_id, spec.scope, getattr(spec, "risk_level", "medium"),
                )
            except Exception:
                mode = "allow"
        if mode == "forbid":
            continue
        decls.append({
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_schema or {},
        })
    return decls


def _format_mcp_declarations(decls: list[dict]) -> str:
    """把 MCP 工具声明格式化为 prompt 注入文本（纯函数便于测试）。

    无声明 → 空串；含声明 → 说明 + JSON 数组 + 调用标记格式。
    """
    if not decls:
        return ""
    import json as _json

    payload = _json.dumps(decls, ensure_ascii=False)
    return (
        "以下是可调用的 MCP 工具（JSON 声明，含工具名/说明/参数）。"
        "需要调用时在回复中输出标记 [mcp.<server>.<tool>]{JSON 参数}[/mcp.<server>.<tool>]，"
        "系统会真实执行并把结果回填（否则不要假装调用过）。\n" + payload
    )


async def _build_mcp_tools_text(
    user_id: int,
    *,
    stream: bool = False,
    quota_chars: int | None = None,
) -> str:
    """MCP 工具声明注入文本（失败静默降级为空串，保证无 MCP 工具/异常时零行为变化）。

    - `stream=True`（P2-A）：流式模式不注入声明 → 返回空串；
    - `quota_chars`（P4-A）：按工具粒度裁剪声明（从尾部逐条丢弃完整声明直到落在配额内），
      避免 _clip_text_to_quota 硬截断切在 JSON 中间产生无效 JSON（只会少工具，不会切坏）。
    """
    try:
        decls = await _build_mcp_tool_declarations(user_id, stream=stream)
        if quota_chars is not None and quota_chars <= 0:
            return ""
        if quota_chars is not None:
            while decls:
                text = _format_mcp_declarations(decls)
                if len(text) <= quota_chars:
                    return text
                decls = decls[:-1]
            return ""
        return _format_mcp_declarations(decls)
    except Exception as e:
        _logger.warning("MCP tool declarations build failed: %s", e)
        return ""


# ------------------------------------------------------------------ MCP 资源摘要注入（Phase 4，2026-08-28）
# 已连接 MCP Server 的资源（uri/name/mimeType）以摘要形式注入上下文，让 Agent 知道有哪些
# 可读取数据源。默认打开；无资源时返回空串（零行为变化）。按 user_id 收集已连接且非空的 Server，
# 不做工具权限过滤（resources 仅注入描述性上下文，不触发读取/调用）。

def _format_mcp_resources(server_groups: list[dict]) -> str:
    """把资源摘要格式化为 prompt 注入文本（纯函数便于测试）。

    无资源 → 空串；有资源 → 说明 + 按 server 分组的 uri/name/mimeType 列表。
    """
    if not server_groups:
        return ""
    lines = []
    for g in server_groups:
        sname = str(g.get("server_name") or "")
        resources = g.get("resources") or []
        if not resources:
            continue
        lines.append(f"【{sname}】")
        for r in resources:
            uri = str(r.get("uri") or "")
            rname = str(r.get("name") or "") or uri
            mime = str(r.get("mime_type") or "")
            desc = str(r.get("description") or "")
            tail = f"（{mime}）" if mime else ""
            if desc:
                lines.append(f"- {rname}: {desc} {tail}".rstrip())
            else:
                lines.append(f"- {rname} {tail}".rstrip())
    if not lines:
        return ""
    return (
        "以下是可读取的 MCP 资源（需要时可用对应工具读取内容，不要凭空编造资源内容）：\n"
        + "\n".join(lines)
    )


async def _build_mcp_resources_text(user_id: int, *, stream: bool = False) -> str:
    """MCP 资源摘要注入文本（失败静默降级为空串，保证无资源/异常时零行为变化）。

    V2-8（2026-08-29）：`stream=True`（流式模式，与 P2-A 的工具声明一致）不注入资源摘要 ——
    流式路径不注入 MCP 工具声明，若仍注入资源摘要（含"可用对应工具读取内容"），AI 会看到资源
    提示但实际无法调用工具，可能产生不一致回复（如"我来读取一下"但实际无法执行）。
    """
    if stream:
        return ""
    try:
        from app.mcp.manager import mcp_manager
        groups = mcp_manager.resources_for_user(user_id)
        return _format_mcp_resources(groups)
    except Exception as e:
        _logger.warning("MCP resources build failed: %s", e)
        return ""


# ------------------------------------------------------------------ section builder（注册表接入）

async def mcp_tools_section(state: dict, ctx: dict) -> list[str]:
    """mcp_tools 分区：工具声明注入（append 块；stream 不注入、无工具空串）。

    返回 system 消息内容列表：有声明时 1 条，否则空列表（不追加块）。
    """
    text = await _build_mcp_tools_text(
        state.get("user_id", 1),
        stream=ctx.get("is_stream", False),
        quota_chars=_MCP_TOOLS_QUOTA_TOKENS * ctx.get("est_chars_per_token", 2),
    )
    return [text] if text else []


async def mcp_resources_section(state: dict, ctx: dict) -> list[str]:
    """mcp_resources 分区：资源摘要注入（append 块；stream 不注入、无资源空串）。

    返回 system 消息内容列表：有资源时 1 条，否则空列表（不追加块）。
    """
    text = await _build_mcp_resources_text(state.get("user_id", 1), stream=ctx.get("is_stream", False))
    return [text] if text else []


register_section(ContextSection(
    key="mcp_tools",
    builder=mcp_tools_section,
    target=TARGET_APPEND,
    quota_tokens=_MCP_TOOLS_QUOTA_TOKENS,
    order=30,
))
register_section(ContextSection(
    key="mcp_resources",
    builder=mcp_resources_section,
    target=TARGET_APPEND,
    quota_tokens=_MCP_RESOURCES_QUOTA_TOKENS,
    order=31,
))
