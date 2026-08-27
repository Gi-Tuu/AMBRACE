"""消息 IO 层：AI 消息落库 + WS 推送（service/streaming/tools 三方共用）。

AMBRACE 重构步骤 3：从 chat_service 拆出消息持久化（_append_ai_*）与 WS 推送
（_push_ws_ai_message/_push_user_notify）到本模块。设计上作为「无副作用依赖」的
消息 IO 层：本模块绝不 import service/streaming/tools（仅函数内懒 import app.ws.*），
供 service.py / streaming.py / tools.py 三方取用，彻底消除 service↔streaming 包内循环。
"""
import json

from app.db.database import async_session_factory
from app.models.chat_message import ChatMessage
from app.utils.logger import get_logger

_logger = get_logger("services.chat")


async def _append_ai_image_message(session_id: int, image_url: str, prompt: str, content: str | None = None) -> None:
    async with async_session_factory() as db:
        msg = ChatMessage(
            session_id=session_id, sender_type="ai",
            content=(content or "给你画好啦～")[:60],
            image_url=image_url,
            extra_meta=json.dumps({"gen_image": True, "prompt": prompt}, ensure_ascii=False),
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
    await _push_ws_ai_message(session_id, msg)


async def _append_ai_text_message(session_id: int, content: str) -> None:
    async with async_session_factory() as db:
        msg = ChatMessage(session_id=session_id, sender_type="ai", content=content)
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
    await _push_ws_ai_message(session_id, msg)


async def _push_ws_ai_message(session_id: int, msg: ChatMessage) -> None:
    """通过 WS 向该会话推送 ai_response（生图消息实时上屏，离线则静默）"""
    try:
        from app.ws.connection_manager import push_to_session
        await push_to_session(session_id, {
            "type": "ai_response",
            "data": {
                "id": msg.id,
                "session_id": session_id,
                "sender_type": "ai",
                "content": msg.content,
                "image_url": msg.image_url,
                "created_at": msg.created_at.isoformat(),
            },
        })
    except Exception as e:
        _logger.warning("WS push ai message failed session=%d: %s", session_id, e)


async def _push_user_notify(user_id: int, session_id: int, character_id: int, content: str) -> None:
    """#55 App 后台保活 + FCM 离线推送：WS 在线实时推送，不在线走 FCM。"""
    try:
        from app.services.push_service import notify_user
        # 通知正文只放预览，不含完整聊天内容（FCM 经 Google 服务器，隐私保护）
        preview = content[:50] + ("…" if len(content) > 50 else "")
        await notify_user(
            user_id,
            title="新消息",
            body=preview,
            data={
                "route": "chat",
                "session_id": str(session_id),
                "character_id": str(character_id),
            },
            channel="chat",
            ws_payload={
                "type": "ai_response",
                "data": {
                    "session_id": session_id,
                    "character_id": character_id,
                    "sender_type": "ai",
                    "content": content,
                },
                "is_proactive": False,
            },
        )
    except Exception as e:
        _logger.warning("Push user notify failed user=%d: %s", user_id, e)
