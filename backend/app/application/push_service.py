"""统一推送服务（2026-08-28）：所有离线推送的唯一出口。

投递逻辑：
1. 频控检查（同一用户 30 分钟 ≤5 条，高优先级不限）
2. 先尝试 WebSocket 在线推送（notify_manager.push_to_user）
3. WS 无在线连接 → 查 user_device_tokens → FCM 推送
4. FCM 410/404 → 删除无效 token
5. 全部失败 → 返回 offline=True（消息已落库，等 App 上线拉取）

FCM 未配置时只走 WS 在线层，功能完整可用。
"""
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select

from app.db.database import async_session_factory
from app.models.device import UserDeviceToken
from app.utils.logger import get_logger

_logger = get_logger("push_service")

# 频控：30 分钟窗口最多 5 条/用户（高优先级 channel=alert 豁免）
_RATE_WINDOW = 1800
_RATE_MAX = 5
_rate_buckets: dict[int, list[float]] = {}  # user_id -> [timestamps]


@dataclass
class PushResult:
    delivered_ws: bool = False
    delivered_fcm: int = 0
    invalid_tokens: int = 0
    offline: bool = False
    rate_limited: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def delivered(self) -> bool:
        return self.delivered_ws or self.delivered_fcm > 0


def _check_rate_limit(user_id: int, priority: str) -> bool:
    """返回 True 表示允许发送，False 表示被频控。高优先级（alert）豁免。只检查不计数。"""
    if priority == "high":
        return True
    now = time.time()
    bucket = _rate_buckets.setdefault(user_id, [])
    # 清理过期时间戳
    cutoff = now - _RATE_WINDOW
    bucket[:] = [t for t in bucket if t > cutoff]
    if len(bucket) >= _RATE_MAX:
        return False
    return True


def _consume_rate_slot(user_id: int) -> None:
    """FCM 实际发送时消耗一个频控配额。"""
    now = time.time()
    bucket = _rate_buckets.setdefault(user_id, [])
    cutoff = now - _RATE_WINDOW
    bucket[:] = [t for t in bucket if t > cutoff]
    bucket.append(now)


async def notify_user(
    user_id: int,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    *,
    priority: str = "normal",
    channel: str = "chat",
    ws_payload: dict | None = None,
) -> PushResult:
    """统一推送入口。

    Args:
        user_id: 目标用户
        title: 通知标题
        body: 通知正文（不包含完整聊天内容，只放预览）
        data: FCM data 字段（route/character_id/session_id 等）
        priority: "normal" | "high"（high 豁免频控）
        channel: "chat" | "alert"（通知渠道）
        ws_payload: 自定义 WS payload；为 None 时自动构造 ai_response 格式
    """
    result = PushResult()

    # 1. 频控
    if not _check_rate_limit(user_id, priority):
        result.rate_limited = True
        _logger.debug("Push rate-limited user=%d", user_id)
        return result

    # 2. 先尝试 WebSocket 在线推送
    try:
        from app.ws.notify_manager import push_to_user

        payload = ws_payload
        if payload is None:
            payload = {
                "type": "push_notification",
                "data": {
                    "title": title,
                    "body": body,
                    **(data or {}),
                },
            }
        ws_ok = await push_to_user(user_id, payload)
        if ws_ok:
            result.delivered_ws = True
            return result
    except Exception as e:
        _logger.warning("WS push failed user=%d: %s", user_id, e)
        result.errors.append(f"ws: {e}")

    # 3. WS 不在线 → FCM 离线推送
    fcm_channel = "ai_companion_alert" if channel == "alert" else "ai_companion_chat"
    invalid_tokens: list[int] = []

    try:
        async with async_session_factory() as db:
            stmt = select(UserDeviceToken).where(
                UserDeviceToken.user_id == user_id,
                UserDeviceToken.push_provider == "fcm",
            )
            tokens = (await db.execute(stmt)).scalars().all()

            if not tokens:
                result.offline = True
                return result

            from app.application.push import fcm_provider

            for tok in tokens:
                fcm_result = await fcm_provider.send(
                    tok.push_token, title, body,
                    {**(data or {}), "channel": channel},
                    channel_id=fcm_channel,
                )
                if fcm_result.success:
                    result.delivered_fcm += 1
                    # 只有实际发送 FCM（非高优先级）才消耗频控配额；高优先级保持豁免
                    if priority != "high":
                        _consume_rate_slot(user_id)
                elif fcm_result.invalid_token:
                    invalid_tokens.append(tok.id)
                    result.invalid_tokens += 1
                else:
                    result.errors.append(f"fcm:{tok.id}:{fcm_result.error}")

            # 删除无效 token
            if invalid_tokens:
                await db.execute(
                    delete(UserDeviceToken).where(UserDeviceToken.id.in_(invalid_tokens))
                )
                await db.commit()
    except Exception as e:
        _logger.warning("FCM push failed user=%d: %s", user_id, e)
        result.errors.append(f"fcm: {e}")

    if not result.delivered:
        result.offline = True
    return result
