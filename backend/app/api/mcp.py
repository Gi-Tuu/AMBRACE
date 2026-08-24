"""MCP 管理 API（Phase 1）：Server 增删改查 + connect/disconnect/test + 工具列表。

- 所有端点需登录态（get_current_user_id）。
- 添加/修改/删除/连接/断开/试连仅主账号（is_admin_user），与插件管理一致。
- 服务器配置按 user_id 隔离：非本用户的 server id 一律 404（不泄露存在性）。
- 错误统一 400 / 403 / 404 + tr_lang 双语。
"""
import json
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.i18n import tr_lang
from app.models.mcp_server import MCPServer
from app.utils.logger import get_logger

_logger = get_logger("api.mcp")

router = APIRouter(prefix="/api/v1/mcp/servers", tags=["MCP"])

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ------------------------------------------------------------------ 请求模型

class MCPServerCreate(BaseModel):
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    url: str = ""  # sse / streamable_http：端点
    headers: dict[str, str] = {}  # sse / streamable_http：自定义头
    enabled: bool = True
    auto_connect: bool = True


class MCPServerUpdate(BaseModel):
    name: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None
    auto_connect: bool | None = None


class MCPToolPermissionUpdate(BaseModel):
    """工具权限等级（Phase 3）：mode ∈ allow / ask / forbid，写入 ToolPermission(scope=mcp_{server})。"""

    mode: str


# ------------------------------------------------------------------ 序列化/校验

def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _serialize(row, *, status: str | None = None, last_error: str | None = None, tool_count: int | None = None) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "transport": row.transport,
        "command": row.command,
        "args": json.loads(row.args_json or "[]"),
        "env": json.loads(row.env_json or "{}"),
        "url": row.url,
        "headers": json.loads(row.headers_json or "{}"),
        "enabled": bool(row.enabled),
        "auto_connect": bool(row.auto_connect),
        "status": status if status is not None else row.status,
        "last_error": last_error if last_error is not None else row.last_error,
        "tools": tool_count if tool_count is not None else len(json.loads(row.tools_cache_json or "[]")),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


async def _load_owned_server(user_id: int, server_id: int, lang: str) -> MCPServer:
    """读取当前用户的 MCP Server；不存在/非本用户 → 404。"""
    async with async_session_factory() as db:
        row = (await db.execute(
            select(MCPServer).where(MCPServer.id == server_id, MCPServer.user_id == user_id)
        )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "mcp_not_found"))
    return row


async def _check_admin(user_id: int, lang: str) -> None:
    from app.services.permission_service import is_admin_user

    if not await is_admin_user(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_manage_only"))


def _validate_name(name: str, lang: str) -> None:
    if not NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "mcp_name_invalid"))


def _validate_transport(transport: str, lang: str) -> None:
    from app.mcp.manager import SUPPORTED_TRANSPORTS

    if transport not in SUPPORTED_TRANSPORTS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "mcp_transport_unsupported"))


def _validate_command(command: str, lang: str) -> None:
    if not (command or "").strip():
        raise HTTPException(status_code=400, detail=tr_lang(lang, "mcp_command_required"))


def _validate_http_config(transport: str, url: str, lang: str) -> None:
    """sse / streamable_http 传输：url 必填 + SSRF 校验（复用 manager.validate_mcp_url）。"""
    if transport not in ("sse", "streamable_http"):
        return
    url = (url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "mcp_url_required"))
    from app.mcp.manager import validate_mcp_url

    try:
        validate_mcp_url(url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "mcp_url_invalid", err=str(e)[:200]))


# ------------------------------------------------------------------ 只读端点

@router.get("")
async def list_servers(
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """列出当前用户的 MCP Server（含实时状态与工具数）。"""
    from app.mcp.manager import mcp_manager

    async with async_session_factory() as db:
        rows = (await db.execute(
            select(MCPServer).where(MCPServer.user_id == user_id).order_by(MCPServer.id)
        )).scalars().all()
    items = []
    for r in rows:
        conn = mcp_manager.get_connection(r.id)
        if conn is not None and conn.is_connected:
            items.append(_serialize(
                r, status=conn.status, last_error=conn.last_error, tool_count=len(conn.tools),
            ))
        else:
            items.append(_serialize(r))
    return {"items": items, "total": len(items)}


@router.get("/logs")
async def list_mcp_call_logs(
    limit: int = 20,
    server_id: int | None = None,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """最近 MCP 工具调用日志（Phase 4：扩展页「最近调用」列表）。

    限当前用户；默认取最近 20 条，可按 server_id 过滤。
    """
    from app.models.mcp_call_log import McpCallLog

    async with async_session_factory() as db:
        q = select(McpCallLog).where(McpCallLog.user_id == user_id).order_by(McpCallLog.id.desc())
        if server_id is not None:
            q = q.where(McpCallLog.server_id == server_id)
        q = q.limit(max(1, min(limit, 100)))
        rows = (await db.execute(q)).scalars().all()
    items = []
    for r in rows:
        items.append({
            "id": r.id,
            "server_id": r.server_id,
            "server_name": r.server_name,
            "tool": r.tool,
            "arguments_summary": r.arguments_summary,
            "ok": bool(r.ok),
            "status": r.status,
            "error": r.error,
            "latency_ms": r.latency_ms,
            "created_at": _iso(r.created_at),
        })
    return {"items": items, "total": len(items)}


@router.get("/{server_id}/tools")
async def list_server_tools(
    server_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """查看该 Server 发现的工具列表（已连接→live，未连接→DB 缓存）。

    Phase 3：每项补充风险等级（risk_level）与当前生效权限（mode，读 ToolPermission / 风险默认），
    便于前端回显权限三档切换。
    """
    row = await _load_owned_server(user_id, server_id, lang)
    from app.mcp.manager import mcp_manager

    tools = await mcp_manager.list_tools(server_id, refresh=False)
    items = []
    for t in tools:
        items.append(await _tool_with_permission(row, t, user_id))
    return {"items": items, "total": len(items)}


@router.get("/{server_id}/resources")
async def list_server_resources(
    server_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """查看该 Server 发现的资源列表（Phase 4：uri/name/description/mimeType）。

    已连接→live（refresh 由 query `refresh=true` 触发），未连接→空列表。
    """
    from fastapi import Query
    refresh: bool = Query(default=False)
    await _load_owned_server(user_id, server_id, lang)
    from app.mcp.manager import mcp_manager

    resources = await mcp_manager.list_resources(server_id, refresh=refresh)
    return {"items": resources, "total": len(resources)}


@router.get("/{server_id}/prompts")
async def list_server_prompts(
    server_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """查看该 Server 发现的提示词列表（Phase 4：只读展示，不强制接入对话）。

    已连接→live，未连接→空列表。
    """
    await _load_owned_server(user_id, server_id, lang)
    from app.mcp.manager import mcp_manager

    prompts = await mcp_manager.list_prompts(server_id, refresh=False)
    return {"items": prompts, "total": len(prompts)}


async def _tool_with_permission(row, tool: dict, user_id: int) -> dict:
    """给单个工具 dict 补充 risk_level + 当前生效 mode（读 ToolPermission / 风险默认）。"""
    from app.mcp.tool_adapter import infer_risk
    from app.services.permission_service import check_mcp_mode

    tool_name = str(tool.get("name") or "")
    risk = infer_risk(tool_name)
    scope = f"mcp_{row.name}"
    mode = await check_mcp_mode(user_id, scope, risk)
    return {
        **tool,
        "risk_level": risk,
        "mode": mode,
        "scope": scope,
    }


@router.put("/{server_id}/tools/{tool_name}")
async def set_tool_permission(
    server_id: int,
    tool_name: str,
    body: MCPToolPermissionUpdate,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """设置 MCP 工具权限等级（仅主账号）：写 ToolPermission(scope=mcp_{server}, level=mode)。

    - mode ∈ allow / ask / forbid；
    - 工具必须已存在（已连接→live，未连接→DB 缓存）；
    - 返回该工具当前生效配置（含 scope / mode），供前端回显。
    """
    from app.models.tool_permission import ToolPermission
    from app.services.permission_service import LEVELS

    await _check_admin(user_id, lang)
    row = await _load_owned_server(user_id, server_id, lang)

    # 参数校验：mode 白名单
    mode = (body.mode or "").strip().lower()
    if mode not in LEVELS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "mcp_mode_invalid"))

    # 工具存在性校验
    from app.mcp.manager import mcp_manager

    tools = await mcp_manager.list_tools(server_id, refresh=False)
    if not any(str(t.get("name") or "") == tool_name for t in tools):
        raise HTTPException(status_code=404, detail=tr_lang(lang, "mcp_tool_not_found"))

    scope = f"mcp_{row.name}"

    # upsert ToolPermission(user_id, scope, level)
    async with async_session_factory() as db:
        existing = (
            await db.execute(
                select(ToolPermission).where(
                    ToolPermission.user_id == user_id, ToolPermission.scope == scope
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(ToolPermission(user_id=user_id, scope=scope, level=mode))
        elif existing.level != mode:
            existing.level = mode
        await db.commit()

    _logger.info(
        "mcp tool permission set user=%d server=%d scope=%s mode=%s tool=%s",
        user_id, server_id, scope, mode, tool_name,
    )
    return {
        "server_id": server_id,
        "tool_name": tool_name,
        "scope": scope,
        "mode": mode,
    }


# ------------------------------------------------------------------ 管理端点

@router.post("")
async def create_server(
    body: MCPServerCreate,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """添加 MCP Server（仅主账号）。"""
    await _check_admin(user_id, lang)
    _validate_name(body.name, lang)
    _validate_transport(body.transport, lang)
    if body.transport == "stdio":
        _validate_command(body.command, lang)
    else:
        _validate_http_config(body.transport, body.url, lang)

    async with async_session_factory() as db:
        existing = (await db.execute(
            select(MCPServer).where(MCPServer.user_id == user_id, MCPServer.name == body.name)
        )).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "mcp_name_conflict"))
        row = MCPServer(
            user_id=user_id,
            name=body.name,
            transport=body.transport,
            command=body.command.strip() if body.transport == "stdio" else None,
            args_json=json.dumps(body.args, ensure_ascii=False),
            env_json=json.dumps(body.env, ensure_ascii=False),
            url=(body.url or "").strip() if body.transport != "stdio" else None,
            headers_json=json.dumps(body.headers, ensure_ascii=False),
            enabled=body.enabled,
            auto_connect=body.auto_connect,
            status="disconnected",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    _logger.info("mcp server created user=%d name=%s id=%d transport=%s", user_id, body.name, row.id, body.transport)
    return _serialize(row)


@router.put("/{server_id}")
async def update_server(
    server_id: int,
    body: MCPServerUpdate,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """修改 MCP Server 配置（仅主账号）；连接相关配置变更时断开连接并重新发现。"""
    await _check_admin(user_id, lang)
    row = await _load_owned_server(user_id, server_id, lang)
    data = body.model_dump(exclude_unset=True)

    # 校验
    if "name" in data and data["name"] != row.name:
        _validate_name(data["name"], lang)
    if "transport" in data:
        _validate_transport(data["transport"], lang)
    # 目标传输类型：以 body.transport（若提供）为准，否则沿用当前 row.transport
    _target_transport = data.get("transport", row.transport)
    if _target_transport == "stdio":
        _cmd = data.get("command")
        if "command" in data and _cmd is not None and not str(_cmd or "").strip():
            _validate_command("", lang)  # stdio 必须提供 command
    else:
        _url = data.get("url", row.url)
        _validate_http_config(_target_transport, _url, lang)

    # 连接配置变更 → 先断开（注销工具，状态置 disconnected，由 connect/reconnect 重新发现）
    conn_changed = (
        ("name" in data and data["name"] != row.name)
        or ("transport" in data and data["transport"] != row.transport)
        or ("command" in data and data["command"] != row.command)
        or ("args" in data)
        or ("env" in data)
        or ("url" in data and data["url"] != row.url)
        or ("headers" in data)
        or ("enabled" in data and data["enabled"] != row.enabled)
    )

    async with async_session_factory() as db:
        target = await db.get(MCPServer, server_id)
        if target is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "mcp_not_found"))
        if "name" in data:
            target.name = data["name"]
        if "transport" in data:
            target.transport = data["transport"]
        if "command" in data:
            target.command = data["command"].strip() if data["command"] else None
        if "args" in data:
            target.args_json = json.dumps(data["args"], ensure_ascii=False)
        if "env" in data:
            target.env_json = json.dumps(data["env"], ensure_ascii=False)
        if "url" in data:
            target.url = (data["url"] or "").strip() if data["url"] else None
        if "headers" in data:
            target.headers_json = json.dumps(data["headers"], ensure_ascii=False)
        if "enabled" in data:
            target.enabled = data["enabled"]
        if "auto_connect" in data:
            target.auto_connect = data["auto_connect"]
        if conn_changed:
            target.status = "disconnected"
            target.last_error = None
        await db.commit()
        await db.refresh(target)

    if conn_changed:
        from app.mcp.manager import mcp_manager, SUPPORTED_TRANSPORTS

        try:
            await mcp_manager.disconnect(server_id)
        except Exception as e:
            _logger.warning("mcp update disconnect failed id=%d: %s", server_id, e)
        # P4-D（2026-08-29）：配置变更且 auto_connect=True 时自动重连（用户无需手动点连接/重启）。
        # 仅当目标传输合法、enabled 时重连；失败静默（用户可手动连接，不影响配置保存）。
        if target.auto_connect and target.enabled and target.transport in SUPPORTED_TRANSPORTS:
            try:
                await mcp_manager.connect(server_id)
            except Exception as e:
                _logger.warning("mcp update reconnect failed id=%d: %s", server_id, e)
        # reconnect 会更新 DB 的 status/last_error；重新读取最新状态回显，避免返回过期的 disconnected
        async with async_session_factory() as db:
            fresh = await db.get(MCPServer, server_id)
        if fresh is not None:
            target = fresh
    _logger.info("mcp server updated id=%d", server_id)
    return _serialize(target)


@router.delete("/{server_id}")
async def delete_server(
    server_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除 MCP Server（仅主账号）：断开连接 + 删配置。"""
    await _check_admin(user_id, lang)
    await _load_owned_server(user_id, server_id, lang)

    from app.mcp.manager import mcp_manager

    try:
        await mcp_manager.disconnect(server_id)
    except Exception as e:
        _logger.warning("mcp delete disconnect failed id=%d: %s", server_id, e)
    async with async_session_factory() as db:
        target = await db.get(MCPServer, server_id)
        if target is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "mcp_not_found"))
        await db.delete(target)
        await db.commit()
    _logger.info("mcp server deleted id=%d", server_id)
    return {"deleted": True, "id": server_id}


@router.post("/{server_id}/connect")
async def connect_server(
    server_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """手动建立连接（仅主账号）：后台已 handle 重连退避。"""
    await _check_admin(user_id, lang)
    await _load_owned_server(user_id, server_id, lang)

    from app.mcp.manager import mcp_manager

    try:
        result = await mcp_manager.connect(server_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "mcp_connect_failed", err=str(e)[:200]))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "mcp_connect_failed", err=str(result.get("error", "unknown"))[:200]))
    return result


@router.post("/{server_id}/disconnect")
async def disconnect_server(
    server_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """手动断开连接（仅主账号）。"""
    await _check_admin(user_id, lang)
    await _load_owned_server(user_id, server_id, lang)

    from app.mcp.manager import mcp_manager

    result = await mcp_manager.disconnect(server_id)
    return result


@router.post("/{server_id}/test")
async def test_server(
    server_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """测试连接（仅主账号）：试连 + 发现工具，不保存配置/状态。"""
    await _check_admin(user_id, lang)
    await _load_owned_server(user_id, server_id, lang)

    from app.mcp.manager import mcp_manager

    result = await mcp_manager.test_connect(server_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "mcp_test_failed", err=str(result.get("error", "unknown"))[:200]))
    return result
