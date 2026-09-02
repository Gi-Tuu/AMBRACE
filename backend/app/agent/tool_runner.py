"""统一工具执行入口（Phase C，2026-08-16）

- 工具生命周期钩子：requested → permission_checked → started → result/error → finished
  （复用插件 run_hook 分发：异常隔离 + 超时门禁；插件可 @sdk.hook("tool_call_requested") 等挂载）
- 权限三档裁决（Operit 式，与现有 permission_service 统一）：allow=执行 / forbid=拒绝 / ask=挂起待确认
- 插件 action 工具：按 plugin+plugin_action 调 registry.run_plugin_action（与现有调用行为一致）
- 幂等/频率门禁：idempotent 工具失败自动重试 1 次；rate_limit 登记（执行由调用方节流，如搜索 60s）
"""
import inspect
import time

from app.agent.tools import ToolSpec
from app.utils.logger import get_logger

_logger = get_logger("agent.tool_runner")

# 工具生命周期阶段（方案 5.3 ToolPkg 式；插件可在此挂载，如渠道敏感词检查/生图额度校验/失败日志）
TOOL_HOOK_STAGES = (
    "tool_call_requested",
    "tool_permission_checked",
    "tool_execution_started",
    "tool_result",
    "tool_error",
    "tool_finished",
)


async def _run_hook(stage: str, ctx: dict) -> None:
    """分发工具生命周期 hook（异常隔离，不阻断工具执行）"""
    try:
        from app.plugins.registry import run_hook
        await run_hook(stage, ctx)
    except Exception as e:
        _logger.warning("tool hook %s 异常: %s", stage, e)


def _make_observation(spec: ToolSpec, result, status: str) -> dict:
    """生成 Observation（Phase G）：epistemic_status/provenance/summary（对齐世界认知标注）"""
    summary = ""
    if isinstance(result, dict):
        summary = str(result.get("summary") or result.get("message") or result.get("result") or result.get("text") or "")  # text 兜底：MCP 工具返回 {ok,text,raw}
    elif isinstance(result, str):
        summary = result
    # P2-B（2026-08-29）：截断上限按工具配置（ToolSpec.max_observation_chars），默认 120；
    # MCP 工具（mcp_tool_to_spec 设为 4000）返回文本不可控，120 字符会严重砍掉内容。
    max_chars = int(getattr(spec, "max_observation_chars", 120) or 120)
    return {
        "epistemic_status": getattr(spec, "epistemic_status", "FACT"),
        "provenance": getattr(spec, "provenance", "tool"),
        "summary": str(summary)[:max_chars],
    }


def _publish_tool_event(spec: ToolSpec, status: str, observation: dict, *, user_id, character_id, session_id, latency_ms: int, error=None) -> None:
    """发布 tool.executed 事件（Phase G：无订阅者时近零开销；失败静默）"""
    try:
        from app.events.bus import publish
        from app.events.types import EventType
        publish(EventType.TOOL_EXECUTED.value, {
            "tool": spec.name,
            "action_type": spec.action_type,
            "status": status,
            "epistemic_status": observation.get("epistemic_status"),
            "provenance": observation.get("provenance"),
            "summary": observation.get("summary", ""),
            "user_id": user_id,
            "character_id": character_id,
            "session_id": session_id,
            "latency_ms": latency_ms,
            "error": error,
        })
    except Exception as e:
        _logger.warning("tool event publish failed: %s", e)


def _resolve_scope(spec: ToolSpec) -> str | None:
    """工具权限 scope：显式指定优先；插件工具按插件名映射（browser/渠道注册/extension）"""
    if spec.scope:
        return spec.scope
    if spec.plugin:
        try:
            from app.application import permission_service
            return permission_service._plugin_scope(spec.plugin)
        except Exception:
            return "extension"
    return None


async def check_tool_permission(spec: ToolSpec, user_id: int | None) -> str:
    """工具权限三档裁决：allow / forbid / ask（与现有 permission_service 统一）。

    - user_id 为空 → allow（后台/系统场景）；
    - 无 scope 的本地能力（日历/备忘/timer 等）→ allow；
    - 其余 → 能力例外优先、无例外跟随全局默认（permission_service 现有语义）。
    """
    if user_id is None:
        return "allow"
    scope = _resolve_scope(spec)
    if scope is None:
        return "allow"
    try:
        from app.application import permission_service
        # MCP 工具（scope=mcp_{server}）：显式配置优先，否则高风险默认 ask、低风险默认 allow。
        if scope.startswith("mcp_"):
            # P1（防御纵深）：归属校验 —— 该 server 必须属于当前用户，否则一律 forbid。
            # （主隔离在 context_builder 声明注入的实时查询；这里兜底防止直接 API 调用绕过）
            from app.mcp.ownership import user_owns_server

            owned = await user_owns_server(user_id, getattr(spec, "server_id", None))
            if not owned:
                _logger.info("mcp tool ownership denied name=%s user=%s", spec.name, user_id)
                return "forbid"
            return await permission_service.check_mcp_mode(
                user_id, scope, getattr(spec, "risk_level", "medium"),
            )
        return await permission_service.check_mode(user_id, scope)
    except Exception as e:
        _logger.warning("tool permission check failed name=%s: %s", spec.name, e)
        return "allow"  # 权限系统异常时放行（与现有 run_plugin_action 的 except 放行一致）


async def execute_tool(
    spec: ToolSpec,
    payload: dict,
    *,
    user_id: int | None = None,
    character_id: int | None = None,
    session_id: int | None = None,
) -> dict:
    """统一工具执行（生命周期钩子 + 权限三档 + 异常隔离）。

    返回 {status, tool, ...}：
    - ok: 执行成功（result 为返回值，latency_ms 耗时）
    - blocked: forbid 或 ask 缺会话上下文
    - pending: ask 已挂起待确认（action_id 指向 PendingPermissionAction）
    - error: 执行异常（已隔离）
    """
    if not spec.enabled:
        _logger.info("tool disabled name=%s", spec.name)
        return {"status": "blocked", "tool": spec.name, "error": "tool disabled"}
    t0 = time.monotonic()
    hook_ctx = {
        "tool": spec.name,
        "action_type": spec.action_type,
        "risk_level": spec.risk_level,
        "user_id": user_id,
        "character_id": character_id,
        "session_id": session_id,
        "payload": payload,
        "started_at": time.time(),
    }
    await _run_hook("tool_call_requested", dict(hook_ctx))

    mode = await check_tool_permission(spec, user_id)
    hook_ctx["permission_mode"] = mode
    await _run_hook("tool_permission_checked", dict(hook_ctx))

    if mode == "forbid":
        _logger.info("tool blocked name=%s user=%s mode=forbid", spec.name, user_id)
        hook_ctx["status"] = "blocked"
        await _run_hook("tool_finished", dict(hook_ctx))
        _obs = _make_observation(spec, None, "blocked")
        _publish_tool_event(spec, "blocked", _obs, user_id=user_id, character_id=character_id, session_id=session_id, latency_ms=int((time.monotonic() - t0) * 1000), error="forbid")
        return {"status": "blocked", "tool": spec.name, "error": "forbid", "observation": _obs}
    if mode == "ask" and getattr(spec, "ask_auto_allow", False):
        # 只读低风险工具（如搜索）：ask 不打扰用户，直接放行（forbid 仍拦截）
        _logger.info("tool ask auto-allow name=%s user=%s", spec.name, user_id)
        mode = "allow"
    if mode == "ask":
        if session_id is None or character_id is None:
            hook_ctx["status"] = "blocked"
            await _run_hook("tool_finished", dict(hook_ctx))
            return {"status": "blocked", "tool": spec.name, "error": "ask without session context"}
        try:
            from app.application import permission_service
            scope = _resolve_scope(spec) or "extension"
            row = await permission_service.create_pending_action(
                user_id, session_id, character_id, scope,
                {"tool": spec.name, "payload": payload},
            )
            hook_ctx["status"] = "pending"
            hook_ctx["action_id"] = row.id
            await _run_hook("tool_finished", dict(hook_ctx))
            return {"status": "pending", "tool": spec.name, "action_id": row.id}
        except Exception as e:
            _logger.warning("tool ask pending failed name=%s: %s", spec.name, e)
            hook_ctx["status"] = "error"
            hook_ctx["error"] = str(e)
            await _run_hook("tool_finished", dict(hook_ctx))
            return {"status": "error", "tool": spec.name, "error": str(e)}

    # allow → 执行（幂等工具失败自动重试 1 次）
    await _run_hook("tool_execution_started", dict(hook_ctx))
    attempts = 2 if spec.idempotent else 1
    last_error = None
    for attempt in range(attempts):
        try:
            if spec.plugin and spec.plugin_action:
                from app.plugins.registry import run_plugin_action
                ok = await run_plugin_action(spec.plugin, spec.plugin_action, payload, user_id=user_id)
                result = {"ok": bool(ok)}
            elif spec.execute is not None:
                res = spec.execute(payload)
                if inspect.isawaitable(res):
                    res = await res
                result = res
            else:
                result = {"ok": False, "message": f"工具 {spec.name} 未接执行入口（占位登记）"}
            hook_ctx["status"] = "ok"
            hook_ctx["result"] = result
            await _run_hook("tool_result", dict(hook_ctx))
            await _run_hook("tool_finished", dict(hook_ctx))
            latency_ms = int((time.monotonic() - t0) * 1000)
            _obs = _make_observation(spec, result, "ok")
            _publish_tool_event(spec, "ok", _obs, user_id=user_id, character_id=character_id, session_id=session_id, latency_ms=latency_ms)
            return {"status": "ok", "tool": spec.name, "result": result, "latency_ms": latency_ms, "observation": _obs}
        except Exception as e:
            last_error = e
            _logger.warning("tool execute failed name=%s attempt=%d: %s", spec.name, attempt + 1, e)
    hook_ctx["status"] = "error"
    hook_ctx["error"] = str(last_error or "")
    await _run_hook("tool_error", dict(hook_ctx))
    await _run_hook("tool_finished", dict(hook_ctx))
    _obs = _make_observation(spec, None, "error")
    _publish_tool_event(spec, "error", _obs, user_id=user_id, character_id=character_id, session_id=session_id, latency_ms=int((time.monotonic() - t0) * 1000), error=str(last_error or ""))
    return {"status": "error", "tool": spec.name, "error": str(last_error or ""), "observation": _obs}
