"""WebSocket 连接池统一管理

从 api/chat.py 抽出（2026-08-04 Phase 2.3）：连接池不再属于 api 层，
api/chat 与 scheduler 均引用本模块，消除 main.py 注入与 services 反向依赖 api 的延迟导入。
"""
from typing import Any

from fastapi import WebSocket

# session_id -> WebSocket
connected_clients: dict[int, WebSocket] = {}


async def push_to_session(session_id: int, payload: dict[str, Any]) -> bool:
    """向在线会话推送 JSON；离线返回 False（调用方自行处理落库即可）"""
    ws = connected_clients.get(session_id)
    if ws is None:
        return False
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False
