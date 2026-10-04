"""统一工具执行入口（Phase C，2026-08-16）

- 工具生命周期钩子：requested → permission_checked → started → result/error → finished
  （复用插件 run_hook 分发：异常隔离 + 超时门禁；插件可 @sdk.hook("tool_call_requested") 等挂载）
- 权限三档裁决（Operit 式，与现有 permission_service 统一）：allow=执行 / forbid=拒绝 / ask=挂起待确认
- 插件 action 工具：按 plugin+plugin_action 调 registry.run_plugin_action（与现有调用行为一致）
- 幂等/频率门禁：idempotent 工具失败自动重试 1 次；rate_limit 登记（执行由调用方节流，如搜索 60s）

安全向改动（U1，2026-09-29）：权限校验异常兜底由「一律放行」改为**分级 fail-closed**——
高风险工具（risk_level=high / MCP scope / 插件-设备行动类 scope）在 permission_service 抛异常时
返回 forbid，低风险仍返回 allow（避免权限表抖动掐断本地能力）。基线两档（无 user_id、无 scope）
语义不变，只是抽成显式函数（理由见 _baseline_decision docstring）。
"""
import inspect
import time

from app.agent.tools import RISK_HIGH, ToolSpec
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
    """生成 Observation（Phase G）：epistemic_status/provenance/summary（对齐世界认知标注）。

    A28-S1（2026-10-05）：语义承载搬到 `app.agent.observation`，本函数**只做一件事**＝拿统一对象的
    `to_core_dict()`，产物与历史实现**同形同值**（三键、同一取法、同一截断上限）。
    完整记录（source/tool_name/归属 ids）留给 Workspace，**不塞进本函数返回值**——多一个键就是行为变化。
    """
    from app.agent.observation import from_spec

    return from_spec(spec, result, status).to_core_dict()


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


def _baseline_decision(spec: ToolSpec, user_id: int | None, scope: str | None) -> str:
    """权限分级第 0 档（基线放行档）：以下两种场景直接 allow，不进权限系统。

    这两条**不是漏洞**，理由与「异常兜底放行」有本质区别：
    - ``user_id is None``＝后台/系统场景（定时任务、主动消息、系统链路内部调用）。此时按
      user_id 在 ToolPermission 表里根本查不到行，走 permission_service 只会落到
      DEFAULT_GLOBAL_LEVEL（allow）——多一次无谓查询、结果相同；且系统链路不该被某个用户的
      个人档位掐断。真正对外的副作用（发消息/落库）在下游仍按 user_id 复核。
    - ``scope is None``＝本地能力（日历/备忘/timer/记忆提炼等），不在 permission 的能力清单
      （permission_service.SCOPES）里，用户既看不到也无法为它配置档位。对它执行「全局默认档」
      等于「拿不到配置就当 allow」，那是假门禁；显式放行才是诚实的设计声明。
    也就是说：这两档是**语义上必须 allow**（可审计、有判据），而异常兜底是**未知状态**——
    未知状态才需要 fail-closed 分级（见 check_tool_permission 的 except 分支）。
    """
    return "allow"


def _plugin_self_declares_high_risk(plugin: str | None) -> bool:
    """fail-open 分级判据④的实现：插件已加载 manifest 的 capability_notes 里是否有任一 risk=high。

    取「插件名 → manifest」走 registry 既有内存视图（``get_plugin`` 给出的 info["path"]）
    ＋ manifest 既有读取函数 ``load_manifest``，不新造缓存。

    一律**向下**（返回 False＝改动前行为）：flag 关 / 插件未加载 / 目录或 manifest 读不到 /
    字段缺失 / 读取过程中任何异常 ⇒ False。本函数只可能把「未知」留在原样，绝不因拿不到
    自述信息而新增拒绝。
    """
    if not plugin:
        return False
    try:
        from app.plugins.manifest import capability_notes_enabled, load_manifest
        if not capability_notes_enabled():
            return False
        from app.plugins import registry
        path = str((registry.get_plugin(plugin) or {}).get("path") or "")
        if not path:
            return False
        manifest = load_manifest(f"{path}/manifest.json") or {}
        notes = manifest.get("capability_notes") or {}
        if not isinstance(notes, dict):
            return False
        return any(isinstance(n, dict) and n.get("risk") == "high" for n in notes.values())
    except Exception as e:
        _logger.warning("capability_notes 读取失败 plugin=%s（按旧行为）: %s", plugin, e)
        return False


def _is_high_risk_for_failopen(spec: ToolSpec, scope: str | None) -> bool:
    """异常兜底用的高风险判据（满足其一即高风险 → 异常时拒绝，不返回放行）：

    ① ``spec.risk_level == "high"``；
    ② scope 以 ``mcp_`` 开头（跨进程的外部 MCP server 能力，行为不可控）；
    ③ scope 属插件/设备行动类（复用 permission_service 现成常量 SCOPE_EXTENSION / SCOPE_BROWSER，
       不新造清单）；
    ④ 插件自述高风险（批 8 块 B M1，**只收紧**）：``spec.plugin`` 非空，且该插件已加载的
       manifest 里 ``capability_notes`` 存在任一 ``risk == "high"``。

    ④ 的边界：
    - **插件粒度**判定，不建「动作 → 权限名」对照表（插件 action 目前没有统一映射，只有设备
      能力有 ``device:<cap>:read``）——只要该插件任一条自述为 high，其全部工具按高风险处理；
      逐动作精确映射留 M2。
    - 只作用于「权限系统抛异常」这一条兜底路径，正常裁决（allow/ask/forbid）不受影响。
    - flag 关 / 读不到 manifest / 字段缺失 ⇒ 一律不生效（等价改动前）。
    - 实现上排在 ③ 之前短路：③ 的 try/except（读 permission_service 常量失败即按低风险）
      一字不动，避免新判据改变既有兜底方向。
    """
    if getattr(spec, "risk_level", "") == RISK_HIGH:
        return True
    if scope and scope.startswith("mcp_"):
        return True
    if _plugin_self_declares_high_risk(getattr(spec, "plugin", None)):
        return True
    try:
        from app.application import permission_service
        return scope in {permission_service.SCOPE_EXTENSION, permission_service.SCOPE_BROWSER}
    except Exception:
        return False


async def check_tool_permission(spec: ToolSpec, user_id: int | None) -> str:
    """工具权限三档裁决：allow / forbid / ask（与现有 permission_service 统一）。

    分级顺序：
    - 第 0 档 基线放行：user_id 为空（后台/系统场景）或无 scope 的本地能力（日历/备忘/timer 等）→ allow；
    - 第 1 档 权限系统：MCP 工具先做归属校验（非本人 → forbid），再按能力例外/全局默认裁决；
    - 第 2 档 异常兜底：权限系统异常时**分级 fail-closed**——高风险工具 → forbid，
      其余（低风险本地/媒体类）→ allow（保持现有体验，与 run_plugin_action 的 except 一致）。
    """
    if user_id is None:
        return _baseline_decision(spec, user_id, None)
    scope = _resolve_scope(spec)
    if scope is None:
        return _baseline_decision(spec, user_id, scope)
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
        fail_closed = _is_high_risk_for_failopen(spec, scope)
        _logger.warning(
            "tool permission check failed name=%s user=%s scope=%s high_risk=%s decision=%s err=%r",
            spec.name, user_id, scope, fail_closed, "forbid" if fail_closed else "allow", e,
        )
        # 安全向（U1）：高风险工具异常时拒绝（fail-closed），低风险仍放行
        return "forbid" if fail_closed else "allow"


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
