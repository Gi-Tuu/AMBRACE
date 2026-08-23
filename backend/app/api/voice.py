"""语音实时链路 API：WS /api/v1/voice/stream（Phase A 轻量实时）"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.utils.logger import get_logger

_logger = get_logger("api.voice")

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])


@router.websocket("/stream")
async def voice_stream(websocket: WebSocket):
    """语音实时会话（?token= JWT 鉴权；文本帧控制 + 二进制帧语音）"""
    from jose import jwt, JWTError
    from app.auth.config import auth_settings as _as

    token = websocket.query_params.get("token", "")
    try:
        payload = jwt.decode(token, _as.secret_key, algorithms=[_as.algorithm])
        ws_user_id = payload.get("user_id")
    except JWTError:
        ws_user_id = None
    if ws_user_id is None:
        await websocket.close(code=4401)
        return

    from app.voice.gateway import handle_voice_session
    try:
        await handle_voice_session(websocket, int(ws_user_id))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        _logger.warning("Voice ws error: %s", e)
        try:
            await websocket.close()
        except Exception:
            pass
