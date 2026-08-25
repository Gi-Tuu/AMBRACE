"""用户级通知 WebSocket 连接池（#55 后台保活实时推送，2026-08-23）。

与 `app.ws.connection_manager`（会话级 session_id -> WebSocket）不同，本模块以
`user_id` 为粒度：一个用户可有多个连接（App 前台 isolate + 后台 isolate），
服务端把「新 AI 消息 / 主动消息」事件实时推给该用户的所有连接，
供 App 退到后台时用 flutter_local_notifications 弹系统通知。
"""
from typing import Any

from fastapi import WebSocket

# user_id -> set[WebSocket]
_connections: dict[int, set[WebSocket]] = {}


async def register(user_id: int, ws: WebSocket) -> None:
    """注册一个用户通知连接。"""
    _connections.setdefault(user_id, set()).add(ws)


async def unregister(user_id: int, ws: WebSocket) -> None:
    """注销一个用户通知连接（连接断开时调用；清空后删除该用户的集合）。"""
    conns = _connections.get(user_id)
    if not conns:
        return
    conns.discard(ws)
    if not conns:
        _connections.pop(user_id, None)


async def push_to_user(user_id: int, payload: dict[str, Any]) -> bool:
    """向该用户的所有通知连接广播；全部离线返回 False（调用方自行落库即可）。"""
    conns = list(_connections.get(user_id, ()))
    if not conns:
        return False
    ok = False
    for ws in conns:
        try:
            await ws.send_json(payload)
            ok = True
        except Exception:
            # 连接已异常：就地移除，避免脏连接累积导致一直尝试发送
            try:
                _connections.get(user_id, set()).discard(ws)
            except Exception:
                pass
    if not _connections.get(user_id):
        _connections.pop(user_id, None)
    return ok


def connection_count(user_id: int = 0) -> int:
    """统计在线通知连接数（user_id=0 表示全部；用于观测/测试）。"""
    if user_id:
        return len(_connections.get(user_id, ()))
    return sum(len(v) for v in _connections.values())
