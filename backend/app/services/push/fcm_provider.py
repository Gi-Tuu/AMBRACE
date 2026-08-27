"""FCM 推送 provider（2026-08-28）。

使用 Firebase Admin SDK 发送离线推送。
- 单例懒初始化：首次发送时才加载服务账号凭证。
- messaging.send 是同步阻塞调用，用 asyncio.to_thread 包裹。
- 未配置凭证（push_fcm_enabled=false 或 credentials_path 为空）时 send() 返回失败。
- 410/404 无效 token 由调用方负责删除。
"""
import asyncio
import os

from app.config import settings
from app.utils.logger import get_logger

_logger = get_logger("push.fcm")

_app = None
_init_attempted = False


def _ensure_app():
    """懒初始化 Firebase Admin 应用。"""
    global _app, _init_attempted
    if _app is not None or _init_attempted:
        return _app
    _init_attempted = True

    if not settings.push_fcm_enabled:
        return None
    cred_path = settings.push_fcm_credentials_path
    if not cred_path or not os.path.isfile(cred_path):
        _logger.warning("FCM enabled but credentials file not found: %s", cred_path)
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(cred_path)
        _app = firebase_admin.initialize_app(cred, name="ambrace-push")
        _logger.info("Firebase Admin initialized for FCM (project=%s)", settings.push_fcm_project_id)
    except ImportError:
        _logger.warning("firebase-admin not installed; FCM push disabled")
    except Exception as e:
        _logger.error("Firebase Admin init failed: %s", e)
    return _app


class FcmSendResult:
    """FCM 发送结果。"""

    def __init__(self, success: bool, message_id: str | None = None,
                 invalid_token: bool = False, error: str | None = None):
        self.success = success
        self.message_id = message_id
        self.invalid_token = invalid_token
        self.error = error


async def send(
    token: str,
    title: str,
    body: str,
    data: dict | None = None,
    *,
    channel_id: str = "ai_companion_chat",
) -> FcmSendResult:
    """通过 FCM 发送一条 notification+data 混合消息。

    系统直接展示通知（App 被杀也能收到），data 字段供点击深链使用。
    """
    app = _ensure_app()
    if app is None:
        return FcmSendResult(success=False, error="fcm_not_configured")

    def _send_sync():
        from firebase_admin import messaging

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=token,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    channel_id=channel_id,
                    click_action="FLUTTER_NOTIFICATION_CLICK",
                ),
            ),
        )
        try:
            msg_id = messaging.send(message, app=app)
            return ("ok", msg_id, None)
        except messaging.UnregisteredError as e:
            return ("invalid", None, str(e))
        except messaging.SenderIdMismatchError as e:
            return ("invalid", None, str(e))
        except Exception as e:
            ename = type(e).__name__
            if "InvalidArgument" in ename or "404" in str(e) or "410" in str(e):
                return ("invalid", None, str(e))
            return ("error", None, f"{ename}: {e}")

    for attempt in range(3):
        try:
            status, msg_id, err = await asyncio.to_thread(_send_sync)
            if status == "ok":
                return FcmSendResult(success=True, message_id=msg_id)
            if status == "invalid":
                return FcmSendResult(success=False, invalid_token=True, error=err)
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            return FcmSendResult(success=False, error=err)
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            return FcmSendResult(success=False, error=str(e))

    return FcmSendResult(success=False, error="max_retries")
