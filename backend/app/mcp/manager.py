"""MCP Client Manager：管理多个 MCP Server 连接，发现工具，代理调用。

Phase 1-2（stdio / sse / streamable_http）。
- connect / disconnect / list_tools / call_tool / reconnect_all / shutdown
- stdio 传输用 mcp.client.stdio.StdioServerParameters + stdio_client（Pydantic 入参，非旧 dict）
- sse / streamable_http 用 mcp.client.sse / mcp.client.streamable_http，url+headers_json 驱动
- 连接时 initialize + list_tools，工具缓存到 conn.tools 与 DB tools_cache_json
- 子进程/远程异常隔离：单个 Server 崩溃不影响其他；连接失败指数退避重连最多 3 次（1s/2s/4s）
- SSRF 防护：sse / streamable_http 复用 plugin_bridge 的 IP 判定（默认禁内网，可配置放行）
- 连接状态变化广播（Event Bus：connected/disconnected/error）
- 工具发现后注册进 ToolRegistry（mcp_tool_to_spec）；断开/删除时注销

实现要点：每个 Server 一个独立的连接 worker 任务（`_worker_main`）。mcp SDK 的 transport
（stdio_client/sse_client/streamable_http_client）用 anyio TaskGroup 持有子进程/HTTP 会话，
其取消作用域是任务绑定的——enter/exit 必须在同一个任务内完成，因此连接基元全部收在 worker
任务里；connect/call_tool/disconnect 通过 conn._queue/asyncio.Future 与该任务交互。这样跨请求
（HTTP / Agent 任务）也能正确调用、不会跨任务 exit。

说明：mcp SDK 仅在连接方法内部延迟 import，保证 app.mcp 模块本身在 mcp 未安装时仍可 import。

F1（2026-08-31）：传输/安全 → transport.py，连接状态 → connection.py，预置 → preset.py，
退出清理 → lifecycle.py；本文件保留 MCPClientManager 与单例，并对旧名做门面重导出（兼容面不变）。
"""
import asyncio
import json
import time
from pathlib import Path

from app.config import settings
from app.utils.logger import get_logger

# F1 拆分：兼容重导出（外部 `from app.mcp.manager import X` 全部保持可用）
from app.mcp.connection import _Connection  # noqa: F401
from app.mcp.transport import (  # noqa: F401
    MCP_CALL_TIMEOUT,
    MCP_CONNECT_TIMEOUT,
    MCP_RECONNECT_MAX,
    STATUS_CONNECTED,
    STATUS_CONNECTING,
    STATUS_DISCONNECTED,
    STATUS_ERROR,
    SUPPORTED_TRANSPORTS,
    _ManagedHttpTransport,
    _TransportConfig,
    _build_pinned_http_client,
    _call_timeout,
    _connect_timeout,
    _reconnect_max,
    _resolve_mcp_ip,
    _to_httpx_timeout,
    validate_mcp_url,
)

_logger = get_logger("mcp.manager")


async def _get_server_row(server_id: int):
    """按 id 读取 MCPServer 行（独立会话，避免与调用方会话耦合）。"""
    from sqlalchemy import select

    from app.db.database import async_session_factory
    from app.models.mcp import MCPServer

    async with async_session_factory() as db:
        return (await db.execute(select(MCPServer).where(MCPServer.id == server_id))).scalar_one_or_none()


class MCPClientManager:
    """管理多个 MCP Server 连接（phase 1 stdio）。"""

    def __init__(self) -> None:
        self._conns: dict[int, _Connection] = {}

    # ------------------------------------------------------------------ 状态

    def get_connection(self, server_id: int) -> _Connection | None:
        return self._conns.get(server_id)

    def is_connected(self, server_id: int) -> bool:
        conn = self._conns.get(server_id)
        return bool(conn and conn.is_connected)

    # ------------------------------------------------------------- 连接控制

    async def connect(self, server_id: int) -> dict:
        """建立连接：stdio 启子进程 → initialize → list_tools 缓存。

        失败指数退避重连最多 settings.mcp_reconnect_max 次；返回 {"ok", "status", "tools" | "error"}。

        P3-A（2026-08-29）：同一 server_id 的并发 connect 用 per-server asyncio.Lock 串行化 +
        双检（已连接直接返回；正在连接则等待 _ready），避免并发创建两个 worker 并把第一个孤儿化
        （孤儿 worker 持有一个 stdio 子进程，资源泄漏；两个 worker 共享队列还会乱序处理命令）。
        """
        row = await _get_server_row(server_id)
        if row is None:
            raise ValueError("mcp server not found")
        if row.transport not in SUPPORTED_TRANSPORTS:
            raise ValueError(f"unsupported transport: {row.transport}")

        conn = self._conns.get(server_id)
        if conn is None:
            conn = _Connection(server_id=server_id, server_name=row.name, enabled=bool(row.enabled), user_id=row.user_id)
            self._conns[server_id] = conn
        else:
            conn.server_name = row.name
            conn.enabled = bool(row.enabled)
            conn.user_id = row.user_id

        # P3-A：per-server 连接锁 + 双检（并发 connect 只产生一个 worker）
        if conn.connect_lock is None:
            conn.connect_lock = asyncio.Lock()
        async with conn.connect_lock:
            if conn.is_connected:
                return {"ok": True, "status": STATUS_CONNECTED, "tools": list(conn.tools)}
            if conn.status == STATUS_CONNECTING:
                # 并发连接已在进行中：等待其 _ready（worker 置位），避免再建 worker
                if conn._ready is None:
                    return {"ok": False, "status": conn.status, "error": "connect in progress"}
                try:
                    await asyncio.wait_for(asyncio.shield(conn._ready), timeout=_connect_timeout())
                except asyncio.TimeoutError:
                    pass
                if conn.is_connected:
                    return {"ok": True, "status": STATUS_CONNECTED, "tools": list(conn.tools)}
                return {"ok": False, "status": conn.status, "error": conn.last_error or "connect in progress"}

            conn.status = STATUS_CONNECTING
            await self._update_db_status(server_id, STATUS_CONNECTING, None)

            try:
                cfg = self._build_config(row)
            except Exception as e:
                conn.status = STATUS_ERROR
                conn.last_error = str(e)
                await self._update_db_status(server_id, STATUS_ERROR, str(e))
                return {"ok": False, "status": STATUS_ERROR, "error": str(e)}

            delay = 1.0
            last_exc: Exception | None = None
            for attempt in range(_reconnect_max()):
                conn._queue = asyncio.Queue()
                conn._ready = asyncio.get_running_loop().create_future()
                conn._worker = asyncio.ensure_future(self._worker_main(conn, cfg))
                try:
                    await asyncio.wait_for(asyncio.shield(conn._ready), timeout=_connect_timeout())
                except asyncio.TimeoutError:
                    last_exc = TimeoutError("connect timeout")
                    await self._settle_worker(conn)
                    conn.status = STATUS_ERROR
                if conn.status == STATUS_CONNECTED:
                    break
                last_exc = last_exc or Exception(conn.last_error or "connect failed")
                await self._settle_worker(conn)
                if attempt < _reconnect_max() - 1:
                    await asyncio.sleep(delay)
                    delay *= 2

            if conn.status == STATUS_CONNECTED:
                conn.last_error = None
                await self._update_db_status(server_id, STATUS_CONNECTED, None)
                await self._register_conn_tools(conn)
                await self._cache_tools_db(server_id, conn.tools)
                _logger.info("mcp connected server=%s tools=%d", conn.server_name, len(conn.tools))
                return {"ok": True, "status": STATUS_CONNECTED, "tools": list(conn.tools)}

            conn.status = STATUS_ERROR
            conn.last_error = str(last_exc or "connect failed")
            await self._update_db_status(server_id, STATUS_ERROR, conn.last_error)
            return {"ok": False, "status": STATUS_ERROR, "error": conn.last_error}

    async def disconnect(self, server_id: int) -> dict:
        """断开连接：注销工具 + 结束 worker 任务（终止 stdio 子进程）；状态置 disconnected。

        V2-6（2026-08-29）：获取 per-server connect_lock（懒创建）与 connect() 互斥，避免并发
        connect/disconnect 竞态（协程 A 连接初始化中，协程 B 直接注销工具/关停 → A 连接建立完成但
        工具已被注销、状态不一致）。disconnect 与 connect 串行化；connect 内部双检逻辑不受影响
        （disconnect 结束后 status=DISCONNECTED，connect 的双检会走正常连接分支）。
        """
        conn = self._conns.get(server_id)
        if conn is not None:
            if conn.connect_lock is None:
                conn.connect_lock = asyncio.Lock()
            async with conn.connect_lock:
                await self._unregister_conn_tools(conn)
                await self._request_shutdown(conn)
                conn.status = STATUS_DISCONNECTED
                conn.last_error = None
                # 清空资源/提示词缓存（API 用 is_connected 守卫，此处避免陈旧内存）
                conn.resources = []
                conn.prompts = []
        await self._update_db_status(server_id, STATUS_DISCONNECTED, None)
        return {"ok": True}

    async def reconnect_all(self, auto_connect: bool | None = None) -> None:
        """启动时重连所有 auto_connect=True 且 enabled 的 Server。

        - 单个 Server 失败不抛错（不影响其他 / 不阻塞启动）。
        - auto_connect 参数当前为兼容占位；行为只看 DB 的 auto_connect 列。
        """
        from sqlalchemy import select

        from app.db.database import async_session_factory
        from app.models.mcp import MCPServer

        try:
            async with async_session_factory() as db:
                rows = (await db.execute(
                    select(MCPServer).where(MCPServer.auto_connect.is_(True))
                )).scalars().all()
        except Exception as e:
            _logger.warning("mcp reconnect_all load failed: %s", e)
            return
        for row in rows:
            if not row.enabled:
                continue
            conn = self._conns.get(row.id)
            if conn is not None and conn.is_connected:
                continue
            try:
                await self.connect(row.id)
            except Exception as e:
                _logger.warning("mcp reconnect_all server=%d failed: %s", row.id, e)

    async def shutdown(self) -> None:
        """应用关闭时断开所有连接并清理。"""
        count = len(self._conns)
        _logger.info("mcp manager shutdown: %d connections", count)
        for server_id in list(self._conns.keys()):
            try:
                await self.disconnect(server_id)
            except Exception as e:
                _logger.warning("mcp shutdown disconnect server=%d failed: %s", server_id, e)
        self._conns.clear()

    # ------------------------------------------------------------- 工具发现/调用

    async def list_tools(self, server_id: int, refresh: bool = False) -> list[dict]:
        """返回工具列表：已连接时用 live（refresh=True 强制重新发现）；未连接时回退 DB 缓存。"""
        conn = self._conns.get(server_id)
        if conn is not None and conn.is_connected:
            if refresh:
                try:
                    await self._submit_list_tools(conn)
                except Exception as e:
                    _logger.warning("mcp refresh tools failed server=%s: %s", server_id, e)
            return list(conn.tools)

        row = await _get_server_row(server_id)
        if row is None:
            return []
        return json.loads(row.tools_cache_json or "[]")

    async def list_resources(self, server_id: int, refresh: bool = False) -> list[dict]:
        """返回资源列表：已连接时用 live（refresh=True 强制重新发现）；未连接时返回空列表（无 DB 缓存）。

        每个资源 dict：{"uri", "name", "description", "mime_type"}。
        """
        conn = self._conns.get(server_id)
        if conn is not None and conn.is_connected:
            if refresh:
                try:
                    await self._submit_list_resources(conn)
                except Exception as e:
                    _logger.warning("mcp refresh resources failed server=%s: %s", server_id, e)
            return list(conn.resources)
        return []

    async def get_resource(self, server_id: int, uri: str) -> dict:
        """读取单个资源内容：返回 {"ok", "uri", "contents": [...] | "error"}。"""
        conn = self._conns.get(server_id)
        if conn is None or not conn.is_connected:
            return {"ok": False, "error": "server not connected"}
        fut = asyncio.get_running_loop().create_future()
        try:
            await conn._queue.put({"kind": "get_resource", "uri": uri, "future": fut})
            res = await asyncio.wait_for(fut, timeout=_call_timeout() + 2.0)
            res.setdefault("uri", uri)
            return res
        except asyncio.TimeoutError:
            _logger.warning("mcp get_resource timeout server=%s uri=%s", server_id, uri)
            return {"ok": False, "uri": uri, "error": "read resource timeout"}
        except Exception as e:
            _logger.warning("mcp get_resource failed server=%s uri=%s: %s", server_id, uri, e)
            return {"ok": False, "uri": uri, "error": str(e)}

    async def list_prompts(self, server_id: int, refresh: bool = False) -> list[dict]:
        """返回提示词列表：已连接时用 live（refresh=True 强制重新发现）；未连接时返回空列表。

        每个 prompt dict：{"name", "description", "arguments":[...]}。
        """
        conn = self._conns.get(server_id)
        if conn is not None and conn.is_connected:
            if refresh:
                try:
                    await self._submit_list_prompts(conn)
                except Exception as e:
                    _logger.warning("mcp refresh prompts failed server=%s: %s", server_id, e)
            return list(conn.prompts)
        return []

    async def get_prompt(self, server_id: int, name: str, arguments: dict | None = None) -> dict:
        """按名获取提示词：返回 {"ok", "name", "description", "messages": [...] | "error"}。"""
        conn = self._conns.get(server_id)
        if conn is None or not conn.is_connected:
            return {"ok": False, "error": "server not connected"}
        fut = asyncio.get_running_loop().create_future()
        try:
            await conn._queue.put({
                "kind": "get_prompt", "name": name, "arguments": arguments or {}, "future": fut,
            })
            res = await asyncio.wait_for(fut, timeout=_call_timeout() + 2.0)
            res.setdefault("name", name)
            return res
        except asyncio.TimeoutError:
            _logger.warning("mcp get_prompt timeout server=%s name=%s", server_id, name)
            return {"ok": False, "name": name, "error": "get prompt timeout"}
        except Exception as e:
            _logger.warning("mcp get_prompt failed server=%s name=%s: %s", server_id, name, e)
            return {"ok": False, "name": name, "error": str(e)}

    def resources_for_user(self, user_id: int) -> list[dict]:
        """收集某用户全部已连接 Server 的资源摘要，供 context_builder 注入。

        返回 [{"server_id","server_name","resources":[{"uri","name","mime_type","description"}]}]，
        仅包含已连接且 resources 非空的 Server。
        """
        out = []
        for conn in self._conns.values():
            if conn.user_id != user_id:
                continue
            if not conn.is_connected or not conn.resources:
                continue
            out.append({
                "server_id": conn.server_id,
                "server_name": conn.server_name,
                "resources": conn.resources,
            })
        return out

    async def call_tool(self, server_id: int, tool_name: str, arguments: dict) -> dict:
        """代理调用 MCP 工具：返回 {"content": [...], "isError": bool}。

        未连接时尝试自动重连一次；调用异常隔离，不抛出（返回 isError=True + error）。
        每次调用落一条 mcp_call_logs（Phase 4：server/tool/参数摘要/ok/耗时）。
        """
        t0 = time.monotonic()
        result = await self._do_call_tool(server_id, tool_name, arguments)
        await self._log_mcp_call(server_id, tool_name, arguments, result, t0)
        return result

    async def _do_call_tool(self, server_id: int, tool_name: str, arguments: dict) -> dict:
        """实际代理调用（不含日志），供 call_tool 包装。"""
        conn = self._conns.get(server_id)
        if conn is None or not conn.is_connected:
            try:
                await self.connect(server_id)
            except Exception as e:
                _logger.warning("mcp auto-reconnect failed server=%s: %s", server_id, e)
            conn = self._conns.get(server_id)
        if conn is None or not conn.is_connected:
            return {"content": [], "isError": True, "error": "server not connected"}

        fut = asyncio.get_running_loop().create_future()
        try:
            await conn._queue.put({
                "kind": "call_tool", "tool": tool_name, "args": arguments or {}, "future": fut,
            })
            return await asyncio.wait_for(fut, timeout=_call_timeout() + 2.0)
        except asyncio.TimeoutError:
            _logger.warning("mcp call_tool timeout server=%s tool=%s", server_id, tool_name)
            return {"content": [], "isError": True, "error": "call timeout"}
        except Exception as e:
            _logger.warning("mcp call_tool failed server=%s tool=%s: %s", server_id, tool_name, e)
            return {"content": [], "isError": True, "error": str(e)}

    async def _log_mcp_call(
        self, server_id: int, tool_name: str, arguments: dict, result: dict, t0: float,
    ) -> None:
        """落一条 MCP 调用日志（mcp_call_logs；写失败静默，不影响调用主链路）。"""
        try:
            from app.db.database import async_session_factory
            from app.models.mcp import McpCallLog

            conn = self._conns.get(server_id)
            server_name = conn.server_name if conn is not None else ""
            user_id = conn.user_id if conn is not None else None
            is_error = bool(result.get("isError", False))
            error = str(result.get("error") or "")[:500] or None
            status = "error" if is_error else "ok"
            if (result.get("error") or "") == "call timeout":
                status = "timeout"
            # 参数摘要：截断避免存大 payload
            try:
                args_summary = json.dumps(arguments or {}, ensure_ascii=False)[:500]
            except Exception:
                args_summary = str(arguments)[:500]

            async with async_session_factory() as db:
                db.add(McpCallLog(
                    user_id=user_id,
                    server_id=server_id,
                    server_name=(server_name or "")[:64],
                    tool=f"mcp.{server_name}.{tool_name}" if server_name else tool_name,
                    arguments_summary=args_summary or None,
                    ok=not is_error,
                    status=status,
                    error=error,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                ))
                await db.commit()
        except Exception as e:
            _logger.warning("mcp call log write failed server=%s tool=%s: %s", server_id, tool_name, e)

    async def test_connect(self, server_id: int) -> dict:
        """试连不保存：一次性连接 + 发现工具后关闭；不写状态、不注册工具。"""
        row = await _get_server_row(server_id)
        if row is None:
            return {"ok": False, "error": "server not found"}
        try:
            cfg = self._build_config(row)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        try:
            tools = await self._probe_connect(cfg)
            return {"ok": True, "tools": tools}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------- worker 任务

    async def _worker_main(self, conn: _Connection, cfg: _TransportConfig) -> None:
        """单个 Server 的连接 worker：持有 transport + ClientSession 上下文。

        在同一任务内完成 __aenter__/initialize/list_tools，并循环处理命令（call/list/shutdown）。
        通过 conn._ready（连接结果）与命令 future（调用结果）与外部交互。
        """
        transport = None
        sess = None
        try:
            from mcp import ClientSession

            transport = self._open_transport(cfg)
            read, write = await transport.__aenter__()
            sess = ClientSession(read, write)
            session = await sess.__aenter__()
            await asyncio.wait_for(session.initialize(), timeout=_connect_timeout())
            result = await asyncio.wait_for(session.list_tools(), timeout=_connect_timeout())
            conn.tools = [self._tool_to_dict(t) for t in result.tools]
            conn.status = STATUS_CONNECTED
            # 先置 ready：connect() 不必等待资源/提示词发现（避免慢/不支持该接口的 server 连接假超时）
            self._set_ready(conn, ok=True)
            # Phase 4：资源/提示词发现（旧 server 可能不支持，失败静默为空；不阻塞连接返回）
            conn.resources = await self._try_list_resources(session)
            conn.prompts = await self._try_list_prompts(session)

            while True:
                cmd = await conn._queue.get()
                kind = cmd["kind"]
                if kind == "call_tool":
                    await self._handle_call(session, cmd)
                elif kind == "list_tools":
                    await self._handle_list(session, cmd)
                elif kind == "list_resources":
                    await self._handle_list_resources(session, cmd)
                elif kind == "get_resource":
                    await self._handle_get_resource(session, cmd)
                elif kind == "list_prompts":
                    await self._handle_list_prompts(session, cmd)
                elif kind == "get_prompt":
                    await self._handle_get_prompt(session, cmd)
                elif kind == "shutdown":
                    cmd["future"].set_result(True)
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            conn.status = STATUS_ERROR
            conn.last_error = str(e)
            self._set_ready(conn, ok=False, error=str(e))
            _logger.warning("mcp worker failed server=%s: %s", conn.server_id, e)
        finally:
            conn.status = STATUS_DISCONNECTED
            # V2-5（2026-08-29）：worker 因未捕获异常退出（非 disconnect 主动断开）时也应注销工具，
            # 避免 ToolRegistry 残留已死连接的工具声明（幽灵工具）。disconnect 已在 _request_shutdown
            # 前调 _unregister_conn_tools，正常退出时此处重复调用是幂等的（内部按名删除）。
            try:
                await self._unregister_conn_tools(conn)
            except Exception as e:
                _logger.warning("mcp worker unregister tools failed: %s", e)
            for cm in (sess, transport):
                if cm is not None:
                    try:
                        await cm.__aexit__(None, None, None)
                    except Exception as e:
                        _logger.warning("mcp worker close error: %s", e)

    async def _handle_call(self, session, cmd: dict) -> None:
        try:
            resp = await asyncio.wait_for(
                session.call_tool(cmd["tool"], cmd["args"]), timeout=_call_timeout(),
            )
            cmd["future"].set_result(self._call_result_to_dict(resp))
        except asyncio.TimeoutError:
            cmd["future"].set_result({"content": [], "isError": True, "error": "call timeout"})
        except Exception as e:
            _logger.warning("mcp worker call failed tool=%s: %s", cmd["tool"], e)
            cmd["future"].set_result({"content": [], "isError": True, "error": str(e)})

    async def _handle_list(self, session, cmd: dict) -> None:
        try:
            result = await asyncio.wait_for(session.list_tools(), timeout=_connect_timeout())
            conn = self._conns.get(cmd["server_id"])
            if conn is not None:
                conn.tools = [self._tool_to_dict(t) for t in result.tools]
            cmd["future"].set_result([self._tool_to_dict(t) for t in result.tools])
        except Exception as e:
            _logger.warning("mcp worker list failed: %s", e)
            cmd["future"].set_result([])

    async def _handle_list_resources(self, session, cmd: dict) -> None:
        try:
            result = await asyncio.wait_for(session.list_resources(), timeout=_connect_timeout())
            conn = self._conns.get(cmd["server_id"])
            if conn is not None:
                conn.resources = [self._resource_to_dict(r) for r in result.resources]
            cmd["future"].set_result([self._resource_to_dict(r) for r in result.resources])
        except Exception as e:
            _logger.warning("mcp worker list resources failed: %s", e)
            cmd["future"].set_result([])

    async def _handle_get_resource(self, session, cmd: dict) -> None:
        try:
            result = await asyncio.wait_for(
                session.read_resource(cmd["uri"]), timeout=_call_timeout(),
            )
            cmd["future"].set_result(self._resource_content_to_dict(result))
        except asyncio.TimeoutError:
            cmd["future"].set_result({"ok": False, "error": "read resource timeout"})
        except Exception as e:
            _logger.warning("mcp worker get resource failed uri=%s: %s", cmd["uri"], e)
            cmd["future"].set_result({"ok": False, "error": str(e)})

    async def _handle_list_prompts(self, session, cmd: dict) -> None:
        try:
            result = await asyncio.wait_for(session.list_prompts(), timeout=_connect_timeout())
            conn = self._conns.get(cmd["server_id"])
            if conn is not None:
                conn.prompts = [self._prompt_to_dict(p) for p in result.prompts]
            cmd["future"].set_result([self._prompt_to_dict(p) for p in result.prompts])
        except Exception as e:
            _logger.warning("mcp worker list prompts failed: %s", e)
            cmd["future"].set_result([])

    async def _handle_get_prompt(self, session, cmd: dict) -> None:
        try:
            result = await asyncio.wait_for(
                session.get_prompt(cmd["name"], arguments=cmd.get("arguments")),
                timeout=_call_timeout(),
            )
            cmd["future"].set_result(self._prompt_content_to_dict(result))
        except asyncio.TimeoutError:
            cmd["future"].set_result({"ok": False, "error": "get prompt timeout"})
        except Exception as e:
            _logger.warning("mcp worker get prompt failed name=%s: %s", cmd["name"], e)
            cmd["future"].set_result({"ok": False, "error": str(e)})

    async def _try_list_resources(self, session) -> list[dict]:
        """连接时发现资源（旧 server 不支持则空列表；异常隔离）。"""
        try:
            result = await asyncio.wait_for(session.list_resources(), timeout=_connect_timeout())
            return [self._resource_to_dict(r) for r in result.resources]
        except Exception as e:
            _logger.debug("mcp list resources skipped (unsupported?): %s", e)
            return []

    async def _try_list_prompts(self, session) -> list[dict]:
        """连接时发现提示词（旧 server 不支持则空列表；异常隔离）。"""
        try:
            result = await asyncio.wait_for(session.list_prompts(), timeout=_connect_timeout())
            return [self._prompt_to_dict(p) for p in result.prompts]
        except Exception as e:
            _logger.debug("mcp list prompts skipped (unsupported?): %s", e)
            return []

    async def _submit_list_tools(self, conn: _Connection) -> list[dict]:
        fut = asyncio.get_running_loop().create_future()
        await conn._queue.put({"kind": "list_tools", "server_id": conn.server_id, "future": fut})
        return await asyncio.wait_for(fut, timeout=_connect_timeout() + 2.0)

    async def _submit_list_resources(self, conn: _Connection) -> list[dict]:
        fut = asyncio.get_running_loop().create_future()
        await conn._queue.put({"kind": "list_resources", "server_id": conn.server_id, "future": fut})
        return await asyncio.wait_for(fut, timeout=_connect_timeout() + 2.0)

    async def _submit_list_prompts(self, conn: _Connection) -> list[dict]:
        fut = asyncio.get_running_loop().create_future()
        await conn._queue.put({"kind": "list_prompts", "server_id": conn.server_id, "future": fut})
        return await asyncio.wait_for(fut, timeout=_connect_timeout() + 2.0)

    async def _request_shutdown(self, conn: _Connection) -> None:
        """请求 worker 结束（shutdown 命令）并等待其退出；兜底取消并收割。"""
        if conn._queue is not None and conn._worker is not None and not conn._worker.done():
            if conn.status == STATUS_CONNECTED:
                try:
                    fut = asyncio.get_running_loop().create_future()
                    await conn._queue.put({"kind": "shutdown", "future": fut})
                    await asyncio.wait_for(fut, timeout=5.0)
                except Exception as e:
                    _logger.warning("mcp shutdown request failed server=%s: %s", conn.server_id, e)
        await self._settle_worker(conn)

    async def _settle_worker(self, conn: _Connection) -> None:
        """等待 worker 自然收尾（如 __aexit__ 杀子进程，约 2-4s）；超时才取消并收割。"""
        if conn._worker is None:
            return
        worker = conn._worker
        try:
            # shield 避免 wait_for 超时把 worker 连带取消：只在它真的卡住时再 cancel
            await asyncio.wait_for(asyncio.shield(worker), timeout=8.0)
        except asyncio.TimeoutError:
            _logger.warning("mcp worker settle wait timeout server=%s; cancelling", conn.server_id)
            worker.cancel()
            try:
                await worker
            except (asyncio.CancelledError, Exception):
                pass
        except (asyncio.CancelledError, Exception):
            pass
        conn._worker = None
        conn._queue = None
        conn._ready = None

    def _set_ready(self, conn: _Connection, ok: bool, error: str | None = None) -> None:
        if conn._ready is not None and not conn._ready.done():
            conn._ready.set_result(True if ok else False)
        if error is not None:
            conn.last_error = error

    # ------------------------------------------------------------- 试连（一次性）

    async def _probe_connect(self, cfg: _TransportConfig) -> list[dict]:
        """一次性试连：进入并立即退出 transport（同一任务内），返回发现的工具。"""
        from mcp import ClientSession

        transport = self._open_transport(cfg)
        async with transport as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=_connect_timeout())
                result = await asyncio.wait_for(session.list_tools(), timeout=_connect_timeout())
                return [self._tool_to_dict(t) for t in result.tools]

    # ------------------------------------------------------------- 内部实现

    def _build_config(self, row) -> _TransportConfig:
        """把 DB 行解析为传输配置（stdio→command/args/env/cwd；sse/http→url/headers；含 SSRF 校验）。"""
        transport = row.transport
        if transport == "stdio":
            args = json.loads(row.args_json or "[]")
            env = json.loads(row.env_json or "{}")
            return _TransportConfig(
                transport="stdio",
                name=row.name,
                command=str(row.command),
                args=args,
                env=(env or None),
                cwd=self._workdir(row),
            )
        if transport in ("sse", "streamable_http"):
            url = (row.url or "").strip()
            validate_mcp_url(url)
            return _TransportConfig(
                transport=transport,
                name=row.name,
                url=url,
                headers=self._headers(row),
            )
        raise ValueError(f"unsupported transport: {transport}")

    def _headers(self, row) -> dict:
        """解析 headers_json（防御脏数据）。"""
        try:
            h = json.loads(row.headers_json or "{}")
            return h if isinstance(h, dict) else {}
        except Exception:
            return {}

    def _open_transport(self, cfg: _TransportConfig):
        """按传输类型返回异步上下文管理器（__aenter__ 返回 (read, write) 流）。

        必须与 worker 在同一任务内 enter/exit（mcp SDK transport 上下文绑定创建它的 anyio 任务）。
        """
        if cfg.transport == "stdio":
            from mcp.client.stdio import StdioServerParameters, stdio_client

            params = StdioServerParameters(
                command=cfg.command,
                args=cfg.args or [],
                env=(cfg.env or None),
                cwd=cfg.cwd,
            )
            return stdio_client(params)
        if cfg.transport == "sse":
            from mcp.client.sse import sse_client

            url = cfg.url
            headers = cfg.headers or None

            def _sse_client_factory(headers=None, timeout=None, auth=None):
                # P3-8：连接期把 SSE 的 httpx client 绑定到已校验 IP（防 DNS rebinding）；
                # 仍保留 mcp 传入的 headers/timeout/auth。绑定/解析失败会自动回退普通 client。
                return _build_pinned_http_client(url, headers, timeout=timeout, auth=auth)

            return sse_client(url, headers=headers, httpx_client_factory=_sse_client_factory)
        if cfg.transport == "streamable_http":
            from mcp.client.streamable_http import streamable_http_client

            http_client = _build_pinned_http_client(cfg.url, cfg.headers or None)
            return _ManagedHttpTransport(streamable_http_client(cfg.url, http_client=http_client), http_client)
        raise ValueError(f"unsupported transport: {cfg.transport}")

    def _workdir(self, row) -> str | None:
        """stdio 工作目录：backend/data/mcp/user_{user_id}/{name}/（隔离文件访问；失败回退 None）。

        P3-D（2026-08-29）：按 user_id 隔离 —— 多用户部署里两个用户创建同名 MCP Server 不再共享
        同一工作目录（避免文件互相覆盖/信息泄露）。
        """
        try:
            base = Path(settings.PROJECT_ROOT) / "data" / "mcp" / f"user_{row.user_id}" / row.name
            base.mkdir(parents=True, exist_ok=True)
            return str(base)
        except Exception:
            return None

    async def _register_conn_tools(self, conn: _Connection) -> int:
        """把 conn.tools 注册进 ToolRegistry（enabled 跟随 server.enabled；重复注册覆盖）。"""
        from app.agent.tools import register_tool
        from app.mcp.tool_adapter import mcp_tool_to_spec

        count = 0
        for tool in conn.tools:
            spec = mcp_tool_to_spec(conn.server_name, tool, conn.server_id)
            spec.enabled = conn.enabled
            register_tool(spec)
            count += 1
        _logger.info("mcp registered tools server=%s count=%d", conn.server_name, count)
        return count

    async def _unregister_conn_tools(self, conn: _Connection) -> None:
        """从 ToolRegistry 注销该 Server 的全部工具。"""
        from app.agent.tools import unregister_tool

        for tool in conn.tools:
            unregister_tool(f"mcp.{conn.server_name}.{tool.get('name')}")
        conn.tools = []

    def _tool_to_dict(self, tool) -> dict:
        return {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", "") or "",
            "input_schema": getattr(tool, "input_schema", None) or {},
        }

    def _resource_to_dict(self, resource) -> dict:
        """Resource → dict：uri/name/description/mime_type（name 由 uri 末段兜底）。"""
        uri = getattr(resource, "uri", "")
        rname = getattr(resource, "name", "") or ""
        if not rname:
            rname = uri.rsplit("/", 1)[-1].rsplit(":", 1)[-1] or uri
        return {
            "uri": uri,
            "name": rname,
            "description": getattr(resource, "description", "") or "",
            "mime_type": getattr(resource, "mime_type", "") or "",
        }

    def _resource_content_to_dict(self, result) -> dict:
        """ReadResourceResult → dict（contents 只保留文本/二进制摘要，便于 API 回显）。"""
        contents = []
        for c in getattr(result, "contents", []) or []:
            ctype = getattr(c, "type", "")
            entry = {"type": ctype, "uri": getattr(c, "uri", "")}
            if ctype == "text":
                entry["text"] = (getattr(c, "text", "") or "")[:2000]
                entry["mime_type"] = getattr(c, "mime_type", "") or ""
            else:
                # 二进制/图片等：只回显 mimeType 与字节数，不回传 base64 正文
                entry["mime_type"] = getattr(c, "mime_type", "") or ""
            contents.append(entry)
        return {"ok": True, "contents": contents}

    def _prompt_to_dict(self, prompt) -> dict:
        """Prompt → dict：name/description/arguments。"""
        args = []
        for a in getattr(prompt, "arguments", None) or []:
            args.append({
                "name": getattr(a, "name", "") or "",
                "description": getattr(a, "description", "") or "",
                "required": bool(getattr(a, "required", False)),
            })
        return {
            "name": getattr(prompt, "name", "") or "",
            "description": getattr(prompt, "description", "") or "",
            "arguments": args,
        }

    def _prompt_content_to_dict(self, result) -> dict:
        """GetPromptResult → dict（messages 摘要，只读展示）。"""
        messages = []
        for m in getattr(result, "messages", []) or []:
            role = getattr(m, "role", "")
            content = getattr(m, "content", None)
            text = ""
            if hasattr(content, "text"):
                text = (content.text or "")[:2000]
            elif isinstance(content, str):
                text = content[:2000]
            messages.append({"role": role, "text": text})
        return {
            "ok": True,
            "description": getattr(result, "description", "") or "",
            "messages": messages,
        }

    def _call_result_to_dict(self, result) -> dict:
        content = []
        for c in getattr(result, "content", []) or []:
            content.append({
                "type": getattr(c, "type", "text"),
                "text": getattr(c, "text", "") or "",
            })
        return {"content": content, "isError": bool(getattr(result, "isError", False))}

    async def _update_db_status(self, server_id: int, status: str, last_error: str | None) -> None:
        """回写 mcp_servers.status / last_error（失败静默）并广播状态事件。"""
        from app.db.database import async_session_factory
        from app.models.mcp import MCPServer

        try:
            async with async_session_factory() as db:
                row = await db.get(MCPServer, server_id)
                if row is None:
                    return
                row.status = status
                if last_error is not None:
                    row.last_error = (last_error or "")[:1000] or None
                await db.commit()
                server_name = row.name
        except Exception as e:
            _logger.warning("mcp update db status failed server=%d: %s", server_id, e)
            server_name = ""
        await self._publish_status(server_id, server_name, status, last_error)

    async def _publish_status(self, server_id: int, server_name: str, status: str, last_error: str | None) -> None:
        """广播 MCP Server 连接状态变化（Event Bus，供 Phase 3 前端状态灯）。"""
        try:
            from app.events.bus import publish
            from app.events.types import EventType
            publish(EventType.MCP_SERVER_STATUS.value, {
                "server_id": server_id,
                "server_name": server_name,
                "status": status,
                "last_error": (last_error or "")[:300] or None,
                "ok": status == STATUS_CONNECTED,
            })
        except Exception as e:
            _logger.warning("mcp status event publish failed server=%d: %s", server_id, e)

    async def _cache_tools_db(self, server_id: int, tools: list[dict]) -> None:
        """把工具列表缓存写回 tools_cache_json（失败静默）。"""
        from app.db.database import async_session_factory
        from app.models.mcp import MCPServer

        try:
            async with async_session_factory() as db:
                row = await db.get(MCPServer, server_id)
                if row is None:
                    return
                row.tools_cache_json = json.dumps(tools, ensure_ascii=False)
                await db.commit()
        except Exception as e:
            _logger.warning("mcp cache tools db failed server=%d: %s", server_id, e)


# 模块级单例（manager.py 全局复用；测试可直接操作该实例）
mcp_manager = MCPClientManager()


# F1 拆分：异常退出清理移至 lifecycle.py（atexit 注册随迁）
from app.mcp.lifecycle import _emergency_cleanup  # noqa: E402,F401
from app.mcp.preset import preset_defaults  # noqa: E402,F401
