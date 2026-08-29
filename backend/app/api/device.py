"""设备推送 token 注册 API（2026-08-28）。

App 端 FCM token 注册/注销/心跳，以及公开的 FCM 客户端配置获取。
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.auth.deps import get_current_user_id
from app.config import settings
from app.db.database import async_session_factory
from app.models.device.device_token import UserDeviceToken

router = APIRouter(prefix="/api/v1/device", tags=["Device Push"])


@router.get("/fcm-config")
async def get_fcm_config():
    """公开接口：返回客户端 Firebase 初始化配置（无需登录）。

    部署者在 .env 中配置 PUSH_FCM_CLIENT_CONFIG（Firebase 控制台 SDK 设置 JSON）。
    未配置时返回 enabled=false，App 端不初始化 FCM。
    """
    if not settings.push_fcm_enabled or not settings.push_fcm_client_config:
        return {"enabled": False}
    try:
        cfg = json.loads(settings.push_fcm_client_config)
        cfg["enabled"] = True
        return cfg
    except (json.JSONDecodeError, TypeError):
        return {"enabled": False}


class RegisterTokenRequest(BaseModel):
    device_id: str
    platform: str  # android | ios
    push_provider: str  # fcm | apns
    push_token: str
    app_version: str | None = None


class HeartbeatRequest(BaseModel):
    device_id: str
    push_provider: str = "fcm"


@router.post("/register")
async def register_token(
    body: RegisterTokenRequest,
    user_id: int = Depends(get_current_user_id),
):
    """注册或更新设备推送 token（登录后调用，token 刷新时也调用）。"""
    async with async_session_factory() as db:
        # 同设备换账号：先清旧账号残留 token，防止登出失败后 token 串号
        await db.execute(
            delete(UserDeviceToken).where(
                UserDeviceToken.device_id == body.device_id,
                UserDeviceToken.push_provider == body.push_provider,
                UserDeviceToken.user_id != user_id,
            )
        )
        stmt = select(UserDeviceToken).where(
            UserDeviceToken.user_id == user_id,
            UserDeviceToken.device_id == body.device_id,
            UserDeviceToken.push_provider == body.push_provider,
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()

        if existing:
            existing.push_token = body.push_token
            existing.platform = body.platform
            existing.app_version = body.app_version
            existing.last_seen_at = datetime.now(timezone.utc)
        else:
            db.add(UserDeviceToken(
                user_id=user_id,
                device_id=body.device_id,
                platform=body.platform,
                push_provider=body.push_provider,
                push_token=body.push_token,
                app_version=body.app_version,
            ))
        await db.commit()
    return {"ok": True}


@router.delete("/unregister")
async def unregister_token(
    device_id: str,
    push_provider: str = "fcm",
    user_id: int = Depends(get_current_user_id),
):
    """注销设备推送 token（退出登录时调用）。"""
    async with async_session_factory() as db:
        stmt = select(UserDeviceToken).where(
            UserDeviceToken.user_id == user_id,
            UserDeviceToken.device_id == device_id,
            UserDeviceToken.push_provider == push_provider,
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            await db.delete(existing)
            await db.commit()
    return {"ok": True}


@router.post("/heartbeat")
async def heartbeat(
    body: HeartbeatRequest,
    user_id: int = Depends(get_current_user_id),
):
    """更新设备 last_seen_at（App 前台每 5 分钟一次）。"""
    async with async_session_factory() as db:
        stmt = select(UserDeviceToken).where(
            UserDeviceToken.user_id == user_id,
            UserDeviceToken.device_id == body.device_id,
            UserDeviceToken.push_provider == body.push_provider,
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.last_seen_at = datetime.now(timezone.utc)
            await db.commit()
    return {"ok": True}
