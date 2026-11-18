# -*- coding: utf-8 -*-
"""YAI Core 接入 · M1 设备工具契约（`/api/agent/tools/*`）。

与 YAI Core v1.0 冻结版一致，且满足其 M1 review 的三条修正：
1. **公开 schema 不接受设备树**：`tree/current_tree` 只存在于内部通道
   `POST /api/agent/tools/internal/observe`（AMBRACE 设备执行器使用，不进注册 spec），
   公开的 `device_observe/device_act` 不得由模型伪造界面树；
2. **最小 OpenAPI**：`/api/agent/tools/openapi.json` 由本模块手工构建，只含
   `device_observe` / `device_act` 两个操作与必要 schema（不含后台 components、不含 manifest）；
3. **typed response**：统一响应模型（ObserveResponse / ActResponse / ErrorBody），
   `device_act` 未真实下发时返回 202 + `status=pending_delivery / executed=false`，不伪装成功。
"""
from __future__ import annotations

import asyncio
import json
import hashlib
import os
import secrets
import time
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth.deps import get_current_user_id, require_server_admin, security
from app.device import snapshot as snap
from app.device import runs as run_registry
from app.device import adapter as device_adapter
from app.device.actions import configured_policies
from app.memory.retrieve import search_memories
from app.memory.write import save_memory

router = APIRouter(prefix="/api/agent/tools", tags=["agent-tools"])

MAX_RESULT_CHARS = 16_000
_SNAPSHOT_TARGETS: dict[str, str] = {}
_AMBRACE_SERVICE_TOKEN = os.environ.get("AMBRACE_SERVICE_TOKEN", "")
_AMBRACE_SERVICE_USER_ID = int(os.environ.get("AMBRACE_SERVICE_USER_ID", "3") or 3)


async def get_tool_user_id(request: Request, credentials=Depends(security)) -> int:
    """工具端点鉴权：长期 AMBRACE service token（Core sidecar）或用户 JWT。"""
    auth = request.headers.get("authorization") or ""
    token = request.headers.get("x-agent-service-key") or ""
    if not token and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1]
    if _AMBRACE_SERVICE_TOKEN and token and secrets.compare_digest(token, _AMBRACE_SERVICE_TOKEN):
        return _AMBRACE_SERVICE_USER_ID
    return await get_current_user_id(request, credentials)


_SSE_TICKETS: dict[str, dict] = {}
CORE_BASE_URL = os.environ.get("CORE_BASE_URL", "").rstrip("/")
CORE_SERVICE_TOKEN = os.environ.get("CORE_SERVICE_TOKEN", "")
_M7_FINALIZE_ENABLED = os.environ.get("M7_FINALIZE_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
_CORE_CALLS: dict[tuple[str, str], dict] = {}
_DONE_FIELDS = ("status", "final_text", "error", "verification", "usage")
_HOST_RUN_ENABLED = __import__("os").environ.get(
    "M6_HOST_RUN_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
_INTERNAL_APPEND_ENABLED = __import__("os").environ.get(
    "M6_INTERNAL_APPEND_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
SSE_TICKET_TTL = 900.0
SSE_PING_INTERVAL = 15.0
_SNAPSHOT_PATHS: dict[str, dict[str, str]] = {}
MAX_LIMIT = 200
TTL_SECONDS = 120.0


class ErrorBody(BaseModel):
    code: str
    message: str


class RefItem(BaseModel):
    ref: str
    role: str = ""
    name: str = ""
    native_id: str = ""
    path: str = ""
    actions: list[str] = Field(default_factory=list)


class ObserveRequest(BaseModel):
    """公开入参：不接受树（树只能来自 AMBRACE 内部通道）。"""

    model_config = ConfigDict(extra="forbid")

    target_app: str = ""
    mode: Literal["skeleton", "full"] = "skeleton"
    max_depth: int = 2
    window_title: str = ""


class ObserveResponse(BaseModel):
    snapshot_id: str
    items: list[RefItem]
    has_more: bool
    offset: int
    redacted_count: int
    expires_in_s: float


class ActRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    ref: str
    verb: Literal["click", "set_text", "long_click", "scroll"] = "click"
    value: str | None = None


class ActResponse(BaseModel):
    status: Literal["pending_delivery", "executed"]
    executed: bool
    delivery: Literal["not_delivered", "delivered"]
    action: str
    path: str
    expected_identity: dict


class RecallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_id: int
    query: str = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=20)
    kind: str | None = None


class RecallItem(BaseModel):
    id: int
    kind: str
    text: str
    score: float = 0.0


class RecallResponse(BaseModel):
    items: list[RecallItem]


class RememberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_id: int
    kind: str = "daily"  # AMBRACE 记忆类型映射，不要求 Core 理解
    text: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)


class RememberResponse(BaseModel):
    id: int | None
    applied: bool


class InternalObserveRequest(BaseModel):
    """内部通道入参（AMBRACE 设备执行器上传树；不出现在注册 spec）。"""

    model_config = ConfigDict(extra="forbid")

    tree: dict
    target_app: str = ""
    window_title: str = ""
    ttl: float = TTL_SECONDS


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execute: bool = False

    run_id: str | None = None  # 宿主预生成并透传；缺省由 registry 生成
    session_id: str = ""
    task: str = ""
    tools: list[str] = Field(default_factory=list)
    approval_mode: Literal["ask", "auto"] = "ask"
    deadline_s: float = 30.0


class HostRunStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    target_app: str = ""
    by: str | None = None
    path: str | None = None
    query: str | None = None
    text: str | None = None
    delay_ms: int = 800
    pre_delay_ms: int = 0


class HostRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[HostRunStep]


class OpenAppRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: str


def _error(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _clip(payload: dict) -> dict:
    items = payload.get("items") or []
    while items and len(json.dumps(payload, ensure_ascii=False)) > MAX_RESULT_CHARS:
        items = items[:-1]
        payload["has_more"] = True
    payload["items"] = items
    return payload


def _observe_payload(tree: dict, window_title: str, offset: int, limit: int) -> dict:
    limit = max(1, min(int(limit), MAX_LIMIT))
    try:
        s = snap.create_snapshot(tree, window_title=window_title, ttl=TTL_SECONDS)
    except snap.SnapshotError as e:
        raise _error(e.code, str(e), 400)
    refs = []
    for ref, rec in list(s.refs.items())[offset: offset + limit]:
        refs.append({
            "ref": ref,
            "role": rec.role,
            "name": rec.name,
            "native_id": rec.native_id or "",
            "path": rec.path,
            "actions": sorted(rec.actions),
        })
    return _clip({
        "snapshot_id": s.snapshot_id,
        "items": refs,
        "has_more": offset + limit < len(s.refs),
        "offset": offset,
        "redacted_count": s.redacted,
        "expires_in_s": TTL_SECONDS,
    })


@router.post("/device_observe", operation_id="agentToolsDeviceObserve",
             response_model=ObserveResponse,
             responses={400: {"model": ErrorBody}, 503: {"model": ErrorBody}})
async def device_observe(
    body: ObserveRequest,
    request: Request,
    offset: int = 0,
    limit: int = 50,
    user_id: int = Depends(get_tool_user_id),
) -> dict:
    """读取目标应用无障碍树并返回脱敏 refs（树由服务端设备执行器采集）。"""
    attach = _core_attach(request, user_id, "device_observe", body.model_dump())
    _audit_run = attach[0] if isinstance(attach, tuple) and attach and attach[0] != "DUP" else ""
    if isinstance(attach, tuple) and attach and attach[0] == "DUP":
        cached = _CORE_CALLS.get(attach[1]) or {}
        if cached.get("payload"):
            return cached["payload"]
        return {"snapshot_id": "duplicate", "window_title": body.window_title or "",
                "items": [], "has_more": False, "redacted_count": 0}
    try:
        tree = await device_adapter.observe_tree(user_id=user_id, target_app=body.target_app, timeout=15.0)
    except Exception as e:
        await _audit_write(user_id=user_id, character_id=None, session_id="", run_id=_audit_run,
                           tool="device_observe", ok=False, detail=str(e))
        _core_result(attach, False, str(e))
        raise _error("DEVICE_CAPTURE_UNAVAILABLE", str(e), 503)
    try:
        payload = _observe_payload(tree, body.window_title, offset, limit)
    except Exception as e:
        await _audit_write(user_id=user_id, character_id=None, session_id="", run_id=_audit_run,
                           tool="device_observe", ok=False, detail=str(e))
        _core_result(attach, False, str(e))
        raise _error("DEVICE_CAPTURE_UNAVAILABLE", str(e), 503)
    _SNAPSHOT_TARGETS[payload["snapshot_id"]] = body.target_app
    # 记录 ref → 客户端原始 path（服务端归一化 path 是点号格式，App 只认 0/0/1）
    by_name: dict[str, list[str]] = {}
    def _walk(nodes):
        for n in nodes or []:
            if isinstance(n, dict):
                name = str(n.get("name") or n.get("text") or n.get("desc") or "")
                cp = str(n.get("path") or "")
                if name and cp:
                    by_name.setdefault(name, []).append(cp)
                _walk(n.get("children") or [])
    _walk(tree.get("children") or [tree])
    mapping = {}
    for item in payload["items"]:
        cands = by_name.get(item["name"]) or []
        if cands:
            mapping[item["ref"]] = cands.pop(0)
    _SNAPSHOT_PATHS[payload["snapshot_id"]] = mapping
    await _audit_write(user_id=user_id, character_id=None, session_id="", run_id=_audit_run,
                       tool="device_observe", ok=True,
                       detail=f"target={body.target_app} refs={len(payload['items'])}")
    _core_result(attach, True, f"refs={len(payload['items'])}")
    if isinstance(attach, tuple) and attach[0] != "DUP":
        _CORE_CALLS.setdefault(attach, {})["payload"] = payload
    return payload


@router.post("/internal/observe", tags=["agent-tools-internal"], operation_id="agentToolsInternalObserve",
             response_model=ObserveResponse)
async def internal_observe(
    body: InternalObserveRequest,
    offset: int = 0,
    limit: int = 50,
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """内部通道：AMBRACE 设备执行器上传树 → 快照。不进入注册用 OpenAPI。"""
    payload = _observe_payload(body.tree, body.window_title, offset, limit)
    if body.target_app:
        _SNAPSHOT_TARGETS[payload["snapshot_id"]] = body.target_app
    return payload


@router.post("/device_act", operation_id="agentToolsDeviceAct",
             response_model=ActResponse,
             responses={403: {"model": ErrorBody}, 409: {"model": ErrorBody}})
async def device_act(body: ActRequest, request: Request, user_id: int = Depends(get_tool_user_id)):
    """复核 ref 并真实下发点按（path 来自服务端快照记录，不接受请求传入）；未授权 403，执行失败 502。"""
    attach = _core_attach(request, user_id, "device_act", body.model_dump())
    _audit_run = attach[0] if isinstance(attach, tuple) and attach and attach[0] != "DUP" else ""
    if isinstance(attach, tuple) and attach and attach[0] == "DUP":
        cached = _CORE_CALLS.get(attach[1]) or {}
        return cached.get("payload") or {"status": "duplicate", "executed": False,
                                          "delivery": "duplicate"}
    try:
        plan = snap.plan_action(body.snapshot_id, body.ref, body.verb, value=body.value)
    except snap.SnapshotError as e:
        code = "STALE_REF" if e.code == "SNAPSHOT_NOT_FOUND" else e.code
        status = 409 if code in ("STALE_REF", "AMBIGUOUS_TARGET") else 400
        await _audit_write(user_id=user_id, character_id=None, session_id="", run_id=_audit_run,
                           tool="device_act", ok=False, detail=f"{code}: {e}")
        _core_result(attach, False, f"{code}: {e}")
        raise _error(code, str(e), status)
    # 所有 verb 统一走产品审批门（scroll 也会改变界面，不豁免）
    cap = "action_set_text" if body.verb == "set_text" else "action_tap"
    policies = await configured_policies(user_id)
    if policies.get(cap) != "once_ever":
        await _audit_write(user_id=user_id, character_id=None, session_id="", run_id=_audit_run,
                           tool="device_act", ok=False, detail=f"APPROVAL_REQUIRED: {cap}")
        _core_result(attach, False, f"APPROVAL_REQUIRED: {cap}")
        raise _error("APPROVAL_REQUIRED",
                     f"{cap} 需在 AMBRACE App 内确认（once_ever 后自动放行）", 403)
    if body.verb != "click":
        return JSONResponse(status_code=202, content={
            "status": "pending_delivery", "executed": False, "delivery": "not_delivered",
            "action": plan["action"], "path": plan["path"],
            "expected_identity": plan["expected_identity"],
        })
    target = _SNAPSHOT_TARGETS.get(body.snapshot_id, "")
    if not target:
        raise _error("STALE_REF", "快照未关联目标应用；请重新 observe", 409)
    # 执行用 path 只取服务端 observe 时记录的快照映射（_SNAPSHOT_PATHS），绝不读请求/模型传入的 path
    exec_path = _SNAPSHOT_PATHS.get(body.snapshot_id, {}).get(body.ref) or plan["path"]
    plan = dict(plan, path=exec_path)
    try:
        res = await device_adapter.execute_action(user_id=user_id, target_app=target, plan=plan, timeout=15.0)
    except Exception as e:
        await _audit_write(user_id=user_id, character_id=None, session_id="", run_id=_audit_run,
                           tool="device_act", ok=False, detail=str(e))
        _core_result(attach, False, str(e))
        raise _error("ACTION_FAILED", str(e), 502)
    if not res.get("ok"):
        await _audit_write(user_id=user_id, character_id=None, session_id="", run_id=_audit_run,
                           tool="device_act", ok=False,
                           detail=str(res.get("detail") or "device_failed"))
        _core_result(attach, False, str(res.get("detail") or "device_failed"))
        raise _error("ACTION_FAILED", str(res.get("detail") or "device_failed"), 502)
    await _audit_write(user_id=user_id, character_id=None, session_id="", run_id=_audit_run,
                       tool="device_act", ok=True,
                       detail=f"verb={body.verb} ref={body.ref} path={plan['path']}")
    _core_result(attach, True, f"verb={body.verb} path={plan['path']}")
    _result = {"status": "executed", "executed": True, "delivery": "delivered",
            "action": plan["action"], "path": plan["path"],
            "expected_identity": plan["expected_identity"]}
    if isinstance(attach, tuple) and attach[0] != "DUP":
        _CORE_CALLS.setdefault(attach, {})["payload"] = _result
    return _result


@router.post("/device_open_app", tags=["agent-tools-internal"],
             operation_id="agentToolsDeviceOpenApp")
async def device_open_app(body: OpenAppRequest, user_id: int = Depends(get_tool_user_id)) -> dict:
    """启动目标应用（AmBraceClick 通道）。"""
    res = await device_adapter.open_app(user_id=user_id, package=body.package)
    if not res.get("ok"):
        raise _error("ACTION_FAILED", str(res.get("detail") or "device_failed"), 502)
    return {"ok": True, "package": body.package, "delivery": "delivered"}


@router.post("/device_snapshot", tags=["agent-tools-internal"],
             operation_id="agentToolsDeviceSnapshot")
async def device_snapshot(user_id: int = Depends(get_current_user_id)) -> dict:
    raise _error("DEVICE_CAPTURE_UNAVAILABLE",
                 "snapshot 需经 AMBRACE 设备执行器回传（M1 适配器阶段）", 503)


@router.post("/runs", tags=["agent-tools-admin"], operation_id="agentToolsRunCreate")
async def create_run(body: RunCreateRequest, user_id: int = Depends(get_current_user_id)):
    """宿主预生成 run_id；execute=true 时异步调 Core 并由后台原子收口。"""
    if body.execute:
        if not _M7_FINALIZE_ENABLED or not CORE_BASE_URL or not CORE_SERVICE_TOKEN:
            raise _error("M7_DISABLED", "M7 执行未启用（需 M7_FINALIZE_ENABLED + CORE_BASE_URL/TOKEN）", 409)
    st = run_registry.create_run(user_id=user_id, session_id=body.session_id, task=body.task,
                                 tools=body.tools, approval_mode=body.approval_mode,
                                 deadline_s=body.deadline_s, run_id=body.run_id)
    st.source = "core" if body.execute else "host"
    if body.execute:
        st.status = "starting"
        asyncio.create_task(_core_execute(st.run_id, body.task, body.deadline_s))
        return JSONResponse(status_code=202, content={
            "run_id": st.run_id, "status": "starting", "source": "core"})
    return run_registry.get_run(st.run_id, user_id=user_id)


@router.post("/runs/{run_id}/cancel", tags=["agent-tools-admin"],
             operation_id="agentToolsRunCancel")
async def cancel_run(run_id: str, user_id: int = Depends(get_current_user_id)) -> dict:
    """取消 run：registry 记 cancelled，不伪造 DONE。"""
    st = run_registry.get_run(run_id, user_id=user_id)
    if st is None:
        raise _error("RUN_NOT_FOUND", "run 不存在或不属于当前账号", 404)
    if st.get("source") == "core" and CORE_BASE_URL:
        try:
            await asyncio.to_thread(_post_core, f"/v1/agent/runs/{run_id}/cancel", {}, 5.0)
        except Exception:
            pass
    ok = run_registry.cancel_run(run_id, user_id=user_id)
    if not ok:
        cur = run_registry.get_run(run_id, user_id=user_id) or {}
        if cur.get("status") == "cancelled":
            return cur
        raise _error("ALREADY_TERMINAL", "run 已终态，不可取消", 409)
    return run_registry.get_run(run_id, user_id=user_id)


@router.post("/internal/runs/{run_id}/finalize", tags=["agent-tools-internal"],
             operation_id="agentToolsRunFinalize")
async def run_finalize(run_id: str, body: dict,
                       user_id: int = Depends(require_server_admin)) -> dict:
    """Core 终态收口（幂等）；done 必须带真实 Core payload（白名单透传）。"""
    if not _M7_FINALIZE_ENABLED:
        raise _error("FINALIZE_DISABLED", "M7 finalize 未启用", 403)
    st = run_registry.get_run(run_id, user_id=user_id)
    if st is None:
        raise _error("RUN_NOT_FOUND", "run 不存在或不属于当前账号", 404)
    status = str(body.get("status") or "")
    if status == "done":
        done = body.get("done") or {}
        payload = {k: done[k] for k in _DONE_FIELDS if k in done}
        ok = run_registry.close_run(run_id, "done", payload, source="core_adapter")
    elif status == "error":
        ok = run_registry.close_run(run_id, "error",
                                    {"message": str(body.get("error") or "")[:300]},
                                    source="registry")
    else:
        raise _error("INVALID_STATUS", "status 只能是 done/error", 400)
    if not ok:
        raise _error("ALREADY_FINALIZED", "run 已终态", 409)
    return run_registry.get_run(run_id, user_id=user_id)


@router.get("/runs/{run_id}", tags=["agent-tools-admin"],
            operation_id="agentToolsRunGet")
async def get_run(run_id: str, user_id: int = Depends(get_current_user_id)) -> dict:
    st = run_registry.get_run(run_id, user_id=user_id)
    if st is None:
        raise _error("RUN_NOT_FOUND", "run 不存在或不属于当前账号", 404)
    return st


def _RUNS_STATUS(run_id: str) -> str:
    st = run_registry._RUNS.get(run_id)
    return st.status if st is not None else ""


def _SET_RUNNING(run_id: str) -> None:
    st = run_registry._RUNS.get(run_id)
    if st is not None and st.status == "starting":
        st.status = "running"


def _core_done_payload(data: dict) -> dict:
    out = {k: data[k] for k in _DONE_FIELDS if k in data}
    out.setdefault("status", "ok")
    if len(json.dumps(out, ensure_ascii=False)) > 32768:
        out = {"status": out["status"], "final_text": str(out.get("final_text", ""))[:8000]}
    return out


def _post_core(path: str, payload: dict, timeout: float) -> tuple[int, dict]:
    import urllib.request
    req = urllib.request.Request(CORE_BASE_URL + path, method="POST",
                                 data=json.dumps(payload).encode())
    req.add_header("Content-Type", "application/json")
    if CORE_SERVICE_TOKEN:
        req.add_header("X-Service-Key", CORE_SERVICE_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:200]}
    except Exception as e:  # 网络/超时
        return 0, {"error": str(e)[:200]}


async def _core_execute(run_id: str, task: str, deadline_s: float) -> None:
    try:
        cur = _RUNS_STATUS(run_id)
        if cur == "starting":
            _SET_RUNNING(run_id)
        status, data = await asyncio.to_thread(
            _post_core, "/v1/agent/run",
            {"task": task, "session_id": f"ambrace-{run_id}",
             "run_id": run_id, "deadline_s": deadline_s},
            max(5.0, deadline_s + 5.0))
        if status == 499 or str(data.get("status")) == "cancelled":
            run_registry.close_run(run_id, "cancelled")
        elif 200 <= status < 300:
            run_registry.close_run(run_id, "done", _core_done_payload(data), source="core_adapter")
        else:
            run_registry.close_run(run_id, "error",
                                   {"message": str(data.get("error") or f"core_http_{status}")},
                                   source="registry")
    except Exception as e:
        run_registry.close_run(run_id, "error", {"message": str(e)[:200]}, source="registry")


def _core_attach(request: Request, user_id: int, tool: str, args: dict) -> tuple[str, str] | None:
    """Core 工具调用挂载：仅 source=core、同 user、未终态；call_id 幂等。"""
    rid = request.headers.get("x-agent-run-id") or ""
    if not rid:
        return None
    st = run_registry.get_run(rid, user_id=user_id)
    if st is None:
        raise _error("RUN_NOT_FOUND", "X-Agent-Run-Id 对应 run 不存在或不属于当前账号", 404)
    if st.get("source") != "core":
        raise _error("RUN_SOURCE_MISMATCH", "该 run 不是 core 来源，拒绝 header 挂载", 409)
    if st.get("closed"):
        raise _error("RUN_TERMINAL", "run 已终态，拒绝执行工具", 409)
    cid = request.headers.get("x-agent-call-id") or f"auto-{time.time_ns()}"
    h = hashlib.sha256(json.dumps({"tool": tool, "args": args}, sort_keys=True,
                                  ensure_ascii=False).encode()).hexdigest()[:16]
    key = (rid, cid)
    prev = _CORE_CALLS.get(key)
    if prev is not None:
        if prev["hash"] != h:
            raise _error("CALL_ID_CONFLICT", "同 call_id 的参数不一致", 409)
        return ("DUP", key)  # 幂等：调用方回放缓存结果
    _CORE_CALLS[key] = {"hash": h}
    run_registry.append_event(rid, "tool_call", {"tool": tool, "call_id": cid, "args_hash": h})
    return rid, cid


def _core_result(rid_cid: tuple[str, str] | None, ok: bool, detail: str = "") -> None:
    if not rid_cid:
        return
    rid, cid = rid_cid
    _CORE_CALLS.setdefault((rid, cid), {})["result"] = {"ok": ok, "detail": detail[:300]}
    run_registry.append_event(rid, "tool_result",
                              {"call_id": cid, "ok": ok, "detail": detail[:300]})


async def _audit_write(*, user_id: int, character_id: int | None, session_id: str,
                        run_id: str, tool: str, ok: bool, detail: str = "") -> None:
    """工具审计落库（agent_task_logs 一行；失败重试并记录，不阻断主流程）。"""
    from sqlalchemy import text
    from app.db import database
    from datetime import datetime, timezone
    last = None
    for attempt in range(3):
        try:
            async with database.async_session_factory() as db:
                await db.execute(text(
                    "INSERT INTO agent_task_logs (task_id, character_id, user_id, session_id,"
                    " trigger, route, steps_json, llm_calls, tool_calls, latency_ms, cost_estimate,"
                    " status, error, created_at) VALUES (:task_id, :cid, :uid, :sid, :trigger, :route,"
                    " :steps, 0, 1, 0, 0, :status, :error, :ts)"
                ), {
                    "task_id": run_id or tool, "cid": character_id, "uid": user_id,
                    "sid": session_id, "trigger": "agent_tools", "route": tool,
                    "steps": json.dumps([{"tool": tool, "ok": ok, "detail": detail[:200]}],
                                        ensure_ascii=False),
                    "status": "ok" if ok else "error",
                    "error": "" if ok else detail[:200],
                    "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" "),
                })
                await db.commit()
            return
        except Exception as e:
            last = e
            await __import__('asyncio').sleep(0.05 * (attempt + 1))
    import logging
    logging.getLogger(__name__).warning('agent_tools audit write failed: %s', last)


@router.post("/memory_recall", operation_id="agentToolsMemoryRecall",
             response_model=RecallResponse)
async def memory_recall(body: RecallRequest, user_id: int = Depends(get_tool_user_id)) -> dict:
    """长期记忆召回（scope=角色/用户由服务端解析；只回 id/kind/text/score）。"""
    rows = await search_memories(body.character_id, body.query, limit=max(1, min(body.k, 20)),
                                 user_id=user_id)
    items = []
    for r in rows:
        kind = str(r.get("type") or r.get("memory_type") or r.get("kind") or "")
        if body.kind and kind != body.kind:
            continue
        raw_score = float(r.get("importance") or r.get("score") or 0.0)
        score = round(raw_score / 100.0, 3) if raw_score > 1 else raw_score
        items.append({"id": int(r.get("id") or 0), "kind": kind,
                      "text": str(r.get("content") or r.get("text") or "")[:500],
                      "score": score})
    await _audit_write(user_id=user_id, character_id=body.character_id, session_id="",
                       run_id="", tool="memory_recall", ok=True,
                       detail=f"query={body.query[:60]} hits={len(items)}")
    return {"items": items}


@router.post("/memory_remember", operation_id="agentToolsMemoryRemember",
             response_model=RememberResponse)
async def memory_remember(body: RememberRequest,
                          user_id: int = Depends(get_tool_user_id)) -> dict:
    """长期记忆写入（source 只留在适配层；tags 存 sub_type，不新增 Core 一等字段）。"""
    sub_type = (("tags:" + ",".join(body.tags))[:64]) if body.tags else None
    obj = await save_memory(user_id=user_id, character_id=body.character_id,
                            memory_type=body.kind or "daily", content=body.text,
                            title=body.text[:40], sub_type=sub_type,
                            source="agent_tools")  # 固定来源，不接受模型自报
    mid = getattr(obj, "id", obj) if obj is not None else None
    await _audit_write(user_id=user_id, character_id=body.character_id, session_id="",
                       run_id="", tool="memory_remember", ok=mid is not None,
                       detail=f"kind={body.kind} id={mid}")
    return {"id": mid, "applied": mid is not None}


@router.get("/manifest", tags=["agent-tools-admin"],
            operation_id="agentToolsManifest")
async def manifest() -> dict:
    """启动期白名单/健康检查；不进入注册用 OpenAPI。"""
    return {
        "status": "ok",
        "protocol": "openapi-3.1",
        "registered_tools": ["device_observe", "device_act", "memory_recall", "memory_remember"],
        "deferred_tools": ["device_open_app", "device_snapshot"],
        "limits": {"max_result_chars": MAX_RESULT_CHARS, "snapshot_ttl_s": TTL_SECONDS},
    }


_SPEC_TOOLS = ("device_observe", "device_act")
MEMORY_SPEC_TOOLS = ("memory_recall", "memory_remember")


def _resolve_refs(node, defs: dict):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return _resolve_refs(defs.get(ref.split("/")[-1], {}), defs)
        return {k: _resolve_refs(v, defs) for k, v in node.items() if k not in ("$defs",)}
    if isinstance(node, list):
        return [_resolve_refs(v, defs) for v in node]
    return node


def _schema(model: type[BaseModel]) -> dict:
    # 内联 $defs：YAI Core 的 OpenAPI 解析器不支持 $ref/$defs
    schema = model.model_json_schema()
    defs = schema.get("$defs") or {}
    return _resolve_refs({k: v for k, v in schema.items() if k != "$defs"}, defs)


def _minimal_spec() -> dict:
    """手工构建最小 OpenAPI 3.1：只有两个设备工具操作与必要 schema。"""
    observe = _schema(ObserveRequest)
    act = _schema(ActRequest)
    return {
        "openapi": "3.1.0",
        "info": {"title": "AMBRACE agent tools", "version": "1.0.0"},
        "tags": [{"name": "agent-tools"}],
        "paths": {
            "/api/agent/tools/device_observe": {
                "post": {
                    "operationId": "agentToolsDeviceObserve",
                    "summary": "读取目标应用无障碍树并返回脱敏 refs",
                    "tags": ["agent-tools"],
                    "security": [{"BearerAuth": []}],
                    "parameters": [
                        {"name": "offset", "in": "query",
                         "schema": {"type": "integer", "minimum": 0, "default": 0}},
                        {"name": "limit", "in": "query",
                         "schema": {"type": "integer", "minimum": 1,
                                    "maximum": MAX_LIMIT, "default": 50}},
                    ],
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": observe}}},
                    "responses": {
                        "200": {"description": "快照", "content": {
                            "application/json": {"schema": _schema(ObserveResponse)}}},
                        "400": {"description": "参数错误", "content": {
                            "application/json": {"schema": _schema(ErrorBody)}}},
                        "503": {"description": "设备采集不可用", "content": {
                            "application/json": {"schema": _schema(ErrorBody)}}},
                    },
                }
            },
            "/api/agent/tools/device_act": {
                "post": {
                    "operationId": "agentToolsDeviceAct",
                    "summary": "复核 ref 并真实下发点按（未授权 403；执行失败 502）",
                    "tags": ["agent-tools"],
                    "security": [{"BearerAuth": []}],
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": act}}},
                    "responses": {
                        "202": {"description": "计划已生成，待设备执行器下发", "content": {
                            "application/json": {"schema": _schema(ActResponse)}}},
                        "403": {"description": "需要产品侧授权", "content": {
                            "application/json": {"schema": _schema(ErrorBody)}}},
                        "409": {"description": "ref 失效/歧义", "content": {
                            "application/json": {"schema": _schema(ErrorBody)}}},
                    },
                }
            },
            "/api/agent/tools/memory_recall": {
                "post": {
                    "operationId": "agentToolsMemoryRecall",
                    "summary": "长期记忆召回（脱敏摘要）",
                    "tags": ["agent-tools"],
                    "security": [{"BearerAuth": []}],
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": _schema(RecallRequest)}}},
                    "responses": {
                        "200": {"description": "召回结果", "content": {
                            "application/json": {"schema": _schema(RecallResponse)}}},
                        "400": {"description": "参数错误", "content": {
                            "application/json": {"schema": _schema(ErrorBody)}}},
                    },
                }
            },
            "/api/agent/tools/memory_remember": {
                "post": {
                    "operationId": "agentToolsMemoryRemember",
                    "summary": "长期记忆写入（产品侧审核/删除）",
                    "tags": ["agent-tools"],
                    "security": [{"BearerAuth": []}],
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": _schema(RememberRequest)}}},
                    "responses": {
                        "200": {"description": "写入结果", "content": {
                            "application/json": {"schema": _schema(RememberResponse)}}},
                        "400": {"description": "参数错误", "content": {
                            "application/json": {"schema": _schema(ErrorBody)}}},
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}},
            "schemas": {
            "ObserveRequest": observe,
            "ObserveResponse": _schema(ObserveResponse),
            "ActRequest": act,
            "ActResponse": _schema(ActResponse),
            "ErrorBody": _schema(ErrorBody),
            "RecallRequest": _schema(RecallRequest),
            "RecallItem": _schema(RecallItem),
            "RecallResponse": _schema(RecallResponse),
            "RememberRequest": _schema(RememberRequest),
            "RememberResponse": _schema(RememberResponse),
        }},
    }


def _verify_sse(request: Request, run_id: str) -> int:
    """SSE 鉴权：Bearer 优先；无 Bearer 时校验 run 专属 cookie ticket。"""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        from jose import JWTError, jwt
        from app.auth.config import auth_settings
        try:
            payload = jwt.decode(auth.split(" ", 1)[1], auth_settings.secret_key,
                                 algorithms=[auth_settings.algorithm])
            return int(payload["user_id"])
        except (JWTError, KeyError, ValueError):
            raise _error("UNAUTHORIZED", "token_invalid", 401)
    token = request.cookies.get(f"ambrace_stream_{run_id}") or ""
    ent = _SSE_TICKETS.get(token)
    if not ent or ent["exp"] < time.monotonic() or ent["run"] != run_id:
        raise _error("UNAUTHORIZED", "stream_ticket_invalid", 401)
    return int(ent["user"])


@router.post("/runs/{run_id}/stream-ticket", tags=["agent-tools-admin"],
             operation_id="agentToolsStreamTicket")
async def stream_ticket(run_id: str, request: Request, response: Response,
                        user_id: int = Depends(get_current_user_id)) -> dict:
    """签发 run 专属 SSE ticket（HttpOnly cookie，15min，可复用）。"""
    if run_registry.get_run(run_id, user_id=user_id) is None:
        raise _error("RUN_NOT_FOUND", "run 不存在或不属于当前账号", 404)
    token = secrets.token_hex(16)
    _SSE_TICKETS[token] = {"user": user_id, "run": run_id,
                           "exp": time.monotonic() + SSE_TICKET_TTL}
    response.set_cookie(
        key=f"ambrace_stream_{run_id}", value=token, max_age=int(SSE_TICKET_TTL),
        httponly=True, samesite="lax", secure=(request.url.scheme == "https"),
        path=f"/api/agent/tools/runs/{run_id}/")
    return {"run_id": run_id, "expires_in": int(SSE_TICKET_TTL)}


@router.get("/runs/{run_id}/events", tags=["agent-tools-admin"],
            operation_id="agentToolsRunEvents")
async def run_events(run_id: str, request: Request,
                     last_event_id: str | None = Header(default=None),
                     offset: str | None = None):
    """SSE 事件流：Last-Event-ID/offset 续传；STREAM_GAP/INVALID_OFFSET；15s ping。"""
    user_id = _verify_sse(request, run_id)
    st = run_registry.get_run(run_id, user_id=user_id)
    if st is None:
        raise _error("RUN_NOT_FOUND", "run 不存在或不属于当前账号", 404)
    raw_off = last_event_id if last_event_id is not None else offset
    try:
        start = int(raw_off or 0)
    except (TypeError, ValueError):
        raise _error("INVALID_OFFSET", "offset 必须是整数", 400)
    if start < 0:
        raise _error("INVALID_OFFSET", "offset 不能为负", 400)
    if start > st["last_offset"]:
        raise _error("INVALID_OFFSET", f"offset {start} > last_offset {st['last_offset']}", 400)
    oldest = st["oldest_available"]
    if start >= 0 and st["last_offset"] > 0 and start < oldest - 1:
        return JSONResponse(status_code=409, content={"detail": {
            "code": "STREAM_GAP", "run_id": run_id,
            "last_event_id": start, "oldest_available": oldest}})

    async def gen():
        sent = start
        last_ping = time.monotonic()
        terminal_sent = False
        while True:
            cur = run_registry.get_run(run_id, user_id=user_id) or {}
            oldest_cur = cur.get("oldest_available", 1)
            if sent > 0 and oldest_cur > sent + 1:
                # 慢客户端：已读游标早于缓冲最早可回放 offset → stream_lagged（控制帧，不续传）
                yield (f"event: stream_lagged\ndata: {json.dumps({'dropped': oldest_cur - sent - 1, 'last_event_id': sent})}\n\n")
                return
            for ev in cur.get("events", []):
                if ev["offset"] <= sent:
                    continue
                yield (f"id: {ev['offset']}\nevent: {ev['type']}\n"
                       f"data: {json.dumps(ev['data'], ensure_ascii=False)}\n\n")
                sent = ev["offset"]
                if ev["type"] in ("done", "run_terminal"):
                    terminal_sent = True
                    break
            if terminal_sent or cur.get("status") in ("done", "cancelled", "expired"):
                yield f"event: eof\ndata: {json.dumps({'run_id': run_id, 'last_event_id': sent})}\n\n"
                return
            if time.monotonic() - last_ping > SSE_PING_INTERVAL:
                yield ": ping\n\n"
                last_ping = time.monotonic()
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


async def _execute_host_run(run_id: str, steps: list[HostRunStep], user_id: int) -> None:
    for step in steps:
        st = run_registry.get_run(run_id, user_id=user_id)
        if st is None or st["status"] != "running":
            return
        if step.pre_delay_ms > 0:
            await asyncio.sleep(step.pre_delay_ms / 1000.0)
        cap = step.capability
        run_registry.append_event(run_id, "tool_call",
                                  {"tool": cap, "target_app": step.target_app})
        res = await device_adapter.enqueue_and_wait(
            user_id=user_id, capability=cap, target_app=step.target_app,
            by=step.by, path=step.path, query=step.query, text=step.text)
        ok = bool(res.get("ok"))
        run_registry.append_event(run_id, "tool_result",
                                  {"tool": cap, "ok": ok,
                                   "detail": str(res.get("detail"))[:300]})
        await _audit_write(user_id=user_id, character_id=None, session_id="",
                           run_id=run_id, tool=cap, ok=ok,
                           detail=f"host_run step={cap} ok={ok}")
        if not ok:
            run_registry.append_event(run_id, "done", {"status": "error"})
            return
        await asyncio.sleep(max(0.0, step.delay_ms / 1000.0))
    if (run_registry.get_run(run_id, user_id=user_id) or {}).get("status") == "running":
        run_registry.append_event(run_id, "done", {"status": "ok"})


@router.post("/internal/runs/{run_id}/execute", tags=["agent-tools-internal"],
             operation_id="agentToolsHostRunExecute")
async def host_run_execute(run_id: str, body: HostRunRequest,
                           user_id: int = Depends(require_server_admin)) -> dict:
    """受信宿主执行：在真实 run 内执行设备步骤并写事件/审计（默认关闭，仅 staging）。"""
    if not _HOST_RUN_ENABLED:
        raise _error("HOST_RUN_DISABLED", "宿主 run 执行未启用", 403)
    if run_registry.get_run(run_id, user_id=user_id) is None:
        raise _error("RUN_NOT_FOUND", "run 不存在或不属于当前账号", 404)
    asyncio.create_task(_execute_host_run(run_id, body.steps, user_id))
    return {"status": "accepted", "run_id": run_id, "steps": len(body.steps)}


@router.post("/internal/runs/{run_id}/events", tags=["agent-tools-internal"],
             operation_id="agentToolsInternalAppend")
async def internal_append_event(run_id: str, payload: dict,
                                user_id: int = Depends(require_server_admin)) -> dict:
    """受信内部通道：仅 server_admin + `M6_INTERNAL_APPEND_ENABLED=true`；禁终端类型。"""
    if not _INTERNAL_APPEND_ENABLED:
        raise _error("INTERNAL_APPEND_DISABLED", "内部事件注入未启用", 403)
    if str(payload.get("type") or "") in ("done", "run_terminal"):
        raise _error("EVENT_TYPE_FORBIDDEN", "内部注入不得伪造终态事件", 403)
    if run_registry.get_run(run_id, user_id=user_id) is None:
        raise _error("RUN_NOT_FOUND", "run 不存在或不属于当前账号", 404)
    etype = str(payload.get("type") or "")
    ok = run_registry.append_event(run_id, etype, payload.get("data") or {})
    if not ok:
        raise _error("EVENT_REJECTED", f"事件被终态屏障拒绝：{etype}", 409)
    return {"ok": True, "run_id": run_id}


def _get_core(path: str, timeout: float = 3.0) -> tuple[int, dict]:
    import urllib.request
    req = urllib.request.Request(CORE_BASE_URL + path, method="GET")
    if CORE_SERVICE_TOKEN:
        req.add_header("X-Service-Key", CORE_SERVICE_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)[:120]}


@router.get("/core-status", tags=["agent-tools-admin"], operation_id="agentToolsCoreStatus")
async def core_status(user_id: int = Depends(get_current_user_id)) -> dict:
    """UI 用：Core 工具服务在线状态（不暴露 run 细节）。"""
    if not CORE_BASE_URL:
        return {"configured": False, "online": False, "tools": 0, "expected": 0}
    expected = int(os.environ.get("CORE_EXPECTED_TOOLS", "0") or 0)
    hst, health = await asyncio.to_thread(_get_core, "/health")
    tst, tools = await asyncio.to_thread(_get_core, "/v1/tools")
    items = tools.get("tools", []) if isinstance(tools, dict) else tools
    count = len(items or [])
    return {"configured": True, "online": hst == 200, "health": health,
            "tools": count, "expected": expected,
            "ok": hst == 200 and count == expected}


@router.get("/audit", tags=["agent-tools-admin"], operation_id="agentToolsAudit")
async def audit(user_id: int = Depends(get_current_user_id),
                run_id: str = "", tool: str = "", limit: int = 50) -> dict:
    """按 run_id/tool 查询工具审计（agent_task_logs，只回本账号）。"""
    from sqlalchemy import text
    from app.db import database
    limit = max(1, min(int(limit), 200))
    conds = ["user_id = :uid", "trigger = 'agent_tools'"]
    params: dict = {"uid": user_id, "lim": limit}
    if run_id:
        conds.append("task_id = :rid"); params["rid"] = run_id
    if tool:
        conds.append("route = :tool"); params["tool"] = tool
    sql = ("SELECT id, task_id, route, status, steps_json, created_at FROM agent_task_logs "
           "WHERE " + " AND ".join(conds) + " ORDER BY id DESC LIMIT :lim")
    async with database.async_session_factory() as db:
        rows = (await db.execute(text(sql), params)).all()
    return {"status": "ok", "items": [
        {"id": r[0], "run_id": r[1], "tool": r[2], "status": r[3],
         "steps": json.loads(r[4] or "[]"), "created_at": str(r[5])} for r in rows]}


@router.get("/runs", tags=["agent-tools-admin"], operation_id="agentToolsRunList")
async def list_runs(user_id: int = Depends(get_current_user_id)) -> dict:
    """运维：当前 run registry 统计（活跃/上限）。"""
    import app.device.runs as rr
    active = [r for r in rr._RUNS.values() if r.user_id == user_id and r.status == "running"]
    return {"status": "ok", "active": len(active), "limit": rr.MAX_RUNS,
            "runs": [{"run_id": r.run_id, "status": r.status,
                      "events": len(r.events)} for r in active]}


@router.get("/openapi.json", tags=["agent-tools-admin"],
            operation_id="agentToolsOpenApi")
async def filtered_openapi() -> dict:
    """注册用 OpenAPI 3.1：只含 device_observe/device_act 与必要 schema。"""
    return _minimal_spec()
