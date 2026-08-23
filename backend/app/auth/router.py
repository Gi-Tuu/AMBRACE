from app.utils.logger import get_logger
from fastapi import APIRouter, Depends, HTTPException, Request
from app.auth import ratelimit
import bcrypt
from sqlalchemy import select
from app.db.database import async_session_factory
from app.models.user import User
from app.auth.config import create_token
from app.auth.deps import get_current_user_id
from app.auth.schemas import (RegisterRequest, LoginRequest, UpdateProfileRequest, ChangePasswordRequest, ForgotPasswordRequest, AuthResponse, UpdateDndRequest)
from app.i18n import tr
from app.models.user_dnd import UserDndSettings

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
_WEAK_PASSWORDS = {
    "123456", "123456789", "12345678", "1234567890", "password", "password1",
    "qwerty", "abc123", "111111", "123123", "admin", "admin123", "root",
    "toor", "test", "test123", "123321", "1234567", "iloveyou", "666666",
    "888888", "000000", "a123456", "letmein", "welcome", "monkey", "dragon",
    "master", "shadow", "sunshine", "princess", "football", "baseball",
    "superman", "batman", "qwerty123", "passw0rd",
}


def validate_password_strength(request: Request, password: str, username: str = "") -> None:
    """密码强度：长度 8-64 + 字母数字组合 + 弱口令/用户名拦截（不满足抛 400）。"""
    if len(password) < 8:
        raise HTTPException(status_code=400, detail=tr(request, "password_too_short"))
    if len(password) > 64:
        raise HTTPException(status_code=400, detail=tr(request, "password_too_long"))
    if password.lower() in _WEAK_PASSWORDS:
        raise HTTPException(status_code=400, detail=tr(request, "password_too_simple"))
    if username and username.lower() in password.lower():
        raise HTTPException(status_code=400, detail=tr(request, "password_contains_username"))
    if not (any(c.isalpha() for c in password) and any(c.isdigit() for c in password)):
        raise HTTPException(status_code=400, detail=tr(request, "password_need_alpha_digit"));


_logger = get_logger("auth")


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(data: RegisterRequest, request: Request):
    validate_password_strength(request, data.password, data.username)
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.username == data.username))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=tr(request, "username_exists"))
        user = User(
            username=data.username,
            nickname=data.nickname or data.username,
            password_hash=bcrypt.hashpw(data.password.encode(), bcrypt.gensalt()).decode(),
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        await db.commit()
        token = create_token(user.id)
        _logger.info("User registered: id=%d username=%s", user.id, user.username)
        return AuthResponse(access_token=token, user_id=user.id, username=user.username, nickname=user.nickname)


@router.post("/login", response_model=AuthResponse)
async def login(data: LoginRequest, request: Request):
    key = f"{request.client.host or 'unknown'}:{data.username}"
    if ratelimit.is_locked(key):
        remain_min = max(1, ratelimit.remaining_lock_seconds(key) // 60 + 1)
        raise HTTPException(status_code=429, detail=tr(request, "too_many_attempts", minutes=remain_min))
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.username == data.username))
        user = result.scalar_one_or_none()
    if not user or not user.password_hash:
        ratelimit.record_failure(key)
        raise HTTPException(status_code=401, detail=tr(request, "wrong_credentials"))
    if not bcrypt.checkpw(data.password.encode(), user.password_hash.encode()):
        ratelimit.record_failure(key)
        raise HTTPException(status_code=401, detail=tr(request, "wrong_credentials"))
    ratelimit.record_success(key)
    token = create_token(user.id)
    return AuthResponse(access_token=token, user_id=user.id, username=user.username, nickname=user.nickname)


@router.get("/profile")
async def get_profile(request: Request, user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail=tr(request, "user_not_found"))
    return {
        "id": user.id,
        "username": user.username,
        "nickname": user.nickname,
        "birthday": user.birthday,
        "gender": user.gender,
        "height": user.height,
        "weight": user.weight,
        "bio": user.bio,
        "avatar_url": user.avatar_url,
        "ai_social_enabled": bool(user.ai_social_enabled),
        "is_admin": bool(user.is_admin),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.put("/profile")
async def update_profile(
    data: UpdateProfileRequest,
    request: Request,
    user_id: int = Depends(get_current_user_id),
):
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail=tr(request, "user_not_found"))
        
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(user, field, value)
        
        await db.flush()
        await db.commit()
        await db.refresh(user)
        
        return {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname,
            "birthday": user.birthday,
            "gender": user.gender,
            "height": user.height,
            "weight": user.weight,
            "bio": user.bio,
            "avatar_url": user.avatar_url,
            "ai_social_enabled": bool(user.ai_social_enabled),
        }

@router.put("/dnd")
async def update_dnd(
    data: UpdateDndRequest,
    user_id: int = Depends(get_current_user_id),
):
    """同步免打扰设置到服务端（状态触发等主动行为联动用）"""
    async with async_session_factory() as db:
        result = await db.execute(select(UserDndSettings).where(UserDndSettings.user_id == user_id))
        dnd = result.scalar_one_or_none()
        if dnd is None:
            dnd = UserDndSettings(user_id=user_id)
            db.add(dnd)
        dnd.dnd_enabled = data.dnd_enabled
        dnd.notifications_enabled = data.notifications_enabled
        dnd.start_hour = data.start_hour
        dnd.start_minute = data.start_minute
        dnd.end_hour = data.end_hour
        dnd.end_minute = data.end_minute
        await db.commit()
        _logger.info("User %d dnd settings updated: enabled=%s", user_id, data.dnd_enabled)

    return {
        "dnd_enabled": data.dnd_enabled,
        "notifications_enabled": data.notifications_enabled,
        "start_hour": data.start_hour,
        "start_minute": data.start_minute,
        "end_hour": data.end_hour,
        "end_minute": data.end_minute,
    }

@router.put("/password")
async def change_password(
    data: ChangePasswordRequest,
    request: Request,
    user_id: int = Depends(get_current_user_id),
):
    """修改密码：需校验旧密码；新密码本地部署不设长度/字符限制。"""
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user or not user.password_hash:
            raise HTTPException(status_code=404, detail=tr(request, "user_not_found"))
        if not bcrypt.checkpw(data.old_password.encode(), user.password_hash.encode()):
            raise HTTPException(status_code=400, detail=tr(request, "old_password_wrong"))
        user.password_hash = bcrypt.hashpw(data.new_password.encode(), bcrypt.gensalt()).decode()
        await db.commit()
        _logger.info("Password changed user_id=%d", user_id)
    return {"status": "ok"}

@router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordRequest, request: Request):
    """忘记密码（本地部署）：无需旧密码直接重置，不做强度校验。
    P0-1 安全加固（2026-08-16 全项目审查）：主账号禁止该通道重置 + IP+用户名级失败限流，防账户接管。
    """
    client_ip = request.client.host if request.client else "unknown"
    key = f"forgot:{client_ip}:{data.username}"
    if ratelimit.is_locked(key):
        raise HTTPException(status_code=429, detail="too many attempts")
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.username == data.username))
        user = result.scalar_one_or_none()
        if not user or not user.password_hash:
            ratelimit.record_failure(key)
            raise HTTPException(status_code=404, detail=tr(request, "user_not_found"))
        if user.id == 1:
            # 主账号禁止通过 forgot-password 匿名重置（防止接管）
            raise HTTPException(status_code=403, detail="master account cannot be reset via forgot-password")
        user.password_hash = bcrypt.hashpw(data.new_password.encode(), bcrypt.gensalt()).decode()
        await db.commit()
        ratelimit.record_success(key)
        _logger.info("Password reset (forgot) user_id=%d", user.id)
    return {"status": "ok"}
