"""WebSocket 连接池统一管理

从 api/chat.py 抽出（2026-08-04 Phase 2.3）：连接池不再属于 api 层，
api/chat 与 scheduler 均引用本模块，消除 main.py 注入与 services 反向依赖 api 的延迟导入。
"""
from typing import Any

from fastapi import WebSocket

# session_id -> WebSocket
connected_clients: dict[int, WebSocket] = {}


def is_session_online(session_id: int | None) -> bool:
    """**只读**探针：该会话当前是否在线（WebSocket 在连接池里）。

    块 C M1（2026-10-01）：生成前用它判定本轮会不会走通知面。纪律：
    - **不发送、不改连接池**（只读 ``in`` 判定），与 ``push_to_session`` 严格分离；
    - 空值 / 非预期异常 ⇒ False（调用方自行按「视为通知面」兜底，见
      ``message_generator._predict_notify_surface``）；
    - 判定失败不抛：探针坏了不该把主动链拖下水。
    """
    try:
        if session_id is None:
            return False
        return int(session_id) in connected_clients
    except Exception:
        return False


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
