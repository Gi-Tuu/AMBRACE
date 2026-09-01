"""MCP 部署级预置（F1 拆分，2026-08-31）：backend/data/mcp_servers.json → DB 幂等播种。

自 manager.py 原样搬移；app.mcp.manager 保留同名重导出（兼容面不变）。
"""
from pathlib import Path

from app.config import settings
from app.mcp.transport import SUPPORTED_TRANSPORTS
from app.utils.logger import get_logger

_logger = get_logger("mcp.preset")


async def preset_defaults() -> int:
    """启动时读取 backend/data/mcp_servers.json（部署级预置，可选）：按用户 id=1 预置 Server 配置。

    - 文件不存在 / 非 JSON 数组 / 空 → 跳过（返回 0）；
    - 目标用户：id=1；若 1 非主账号则回退 settings.admin_user_ids 首位（主账号优先）；
    - DB 已有同名 (user_id, name) → 跳过（幂等）；
    - 名称/传输/必填字段不合法 → 跳过该项，不抛错。
    返回预置条数。
    """
    import json as _json
    import re as _re

    path = Path(settings.PROJECT_ROOT) / "data" / "mcp_servers.json"
    if not path.exists():
        return 0
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        _logger.warning("mcp preset file parse failed: %s", e)
        return 0
    if not isinstance(data, list) or not data:
        return 0

    user_id = 1
    try:
        from app.services.permission_service import is_admin_user_sync
        if not is_admin_user_sync(1):
            user_id = int((settings.admin_user_ids or [1])[0])
    except Exception:
        pass

    from sqlalchemy import select

    from app.db.database import async_session_factory
    from app.models.mcp import MCPServer

    _name_re = _re.compile(r"^[A-Za-z0-9_-]{1,64}$")
    count = 0
    async with async_session_factory() as db:
        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            transport = str(item.get("transport") or "stdio").strip()
            if not _name_re.match(name) or transport not in SUPPORTED_TRANSPORTS:
                continue
            command = item.get("command") or ""
            url = str(item.get("url") or "").strip() if transport in ("sse", "streamable_http") else None
            if transport == "stdio" and not str(command).strip():
                continue
            if transport in ("sse", "streamable_http") and not url:
                continue
            existing = (await db.execute(
                select(MCPServer).where(MCPServer.user_id == user_id, MCPServer.name == name)
            )).scalar_one_or_none()
            if existing is not None:
                continue
            db.add(MCPServer(
                user_id=user_id,
                name=name,
                transport=transport,
                command=str(command) if transport == "stdio" else None,
                args_json=_json.dumps(item.get("args") or [], ensure_ascii=False),
                env_json=_json.dumps(item.get("env") or {}, ensure_ascii=False),
                url=url,
                headers_json=_json.dumps(item.get("headers") or {}, ensure_ascii=False),
                enabled=bool(item.get("enabled", True)),
                auto_connect=bool(item.get("auto_connect", True)),
                status="disconnected",
            ))
            count += 1
        await db.commit()
    if count:
        _logger.info("mcp preset seeded: %d servers for user=%d", count, user_id)
    return count
