from app.utils.logger import get_logger
from fastapi import APIRouter, Depends, HTTPException, Request
from app.auth import ratelimit
import bcrypt
from sqlalchemy import func, select
from app.db.database import async_session_factory
from app.models.user import User
from app.auth.config import create_token
from app.auth.deps import get_current_user_id
from app.auth.schemas import (RegisterRequest, LoginRequest, UpdateProfileRequest, ChangePasswordRequest, ForgotPasswordRequest, AuthResponse, UpdateDndRequest)
from app.i18n import tr
from app.models.user import UserDndSettings

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


async def _require_registration_invite(db, code: str | None, request: Request):
    """invite_only 注册：校验受邀码（存在 / 未消费 / 未过期 / 发码者为主账号 / 未超子账号上限）。

    契约 §1.4 只规定 ``closed`` 的行为；``invite_only`` 的具体形态契约未覆盖，本轮实现为
    「注册须携带主账号发出的受邀码（POST /api/v1/account/invite-code），成功后新账号直接挂在
    该主账号下（子账号）并一次性消费该码」——复用既有 account_invites 原语，不新造一套。
    """
    from datetime import datetime, timezone

    from app.application.family_service import MAX_SUB_ACCOUNTS, count_sub_accounts
    from app.models.user import AccountInvite

    c = (code or "").strip().upper()
    if not c:
        raise HTTPException(status_code=403, detail=tr(request, "registration_invite_required"))
    invite = (await db.execute(
        select(AccountInvite).where(AccountInvite.code == c)
    )).scalar_one_or_none()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if (invite is None or invite.used_by is not None
            or (invite.expires_at is not None and invite.expires_at < now)):
        raise HTTPException(status_code=403, detail=tr(request, "invite_code_invalid"))
    creator = (await db.execute(select(User).where(User.id == invite.creator_id))).scalar_one_or_none()
    if creator is None or creator.parent_id is not None:
        raise HTTPException(status_code=403, detail=tr(request, "invite_code_invalid"))
    if await count_sub_accounts(db, creator.id) >= MAX_SUB_ACCOUNTS:
        raise HTTPException(status_code=403, detail=tr(request, "sub_account_limit_reached"))
    return invite


async def _consume_registration_invite(db, invite, new_user_id: int, request: Request) -> None:
    """一次性消费受邀码（同事务条件更新防并发；rowcount=0 → 409）。"""
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.models.user import AccountInvite

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    res = await db.execute(
        update(AccountInvite)
        .where(AccountInvite.id == invite.id, AccountInvite.used_by.is_(None))
        .values(used_by=new_user_id, used_at=now)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        raise HTTPException(status_code=409, detail=tr(request, "invite_code_invalid"))


_logger = get_logger("auth")


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(data: RegisterRequest, request: Request):
    validate_password_strength(request, data.password, data.username)
    async with async_session_factory() as db:
        # 注册策略门禁（账号独立 P2，契约 §1.4）：closed → 403 可读；invite_only → 须带受邀码。
        # 缺行/非法值/读失败 → open（与现状一致，fail-open）。
        from app.application.server_settings_service import (
            REGISTRATION_MODE_CLOSED,
            REGISTRATION_MODE_INVITE_ONLY,
            get_registration_mode,
        )
        reg_mode = await get_registration_mode(db)
        if reg_mode == REGISTRATION_MODE_CLOSED:
            raise HTTPException(status_code=403, detail=tr(request, "registration_closed"))
        invite = None
        if reg_mode == REGISTRATION_MODE_INVITE_ONLY:
            invite = await _require_registration_invite(db, data.invite_code, request)
        result = await db.execute(select(User).where(User.username == data.username))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=tr(request, "username_exists"))
        # 新部署引导（2026-08-24 修复）：users 表中尚无任何主账号（is_admin=1）时，
        # 第一个注册的账号自动成为主账号，避免「无主账号→无法进入主账号管理→死锁」。
        has_admin = (await db.execute(
            select(func.count()).select_from(User).where(User.is_admin.is_(True))
        )).scalar_one()
        user = User(
            username=data.username,
            nickname=data.nickname or data.username,
            password_hash=bcrypt.hashpw(data.password.encode(), bcrypt.gensalt()).decode(),
            # 受邀注册（invite_only）：新账号直接挂到发码主账号下 → 子账号（is_admin=0，
            # 与 init_db 的 parent_id/is_admin 一致性自愈同口径）。
            is_admin=bool(has_admin == 0) if invite is None else False,
            parent_id=invite.creator_id if invite is not None else None,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        if invite is not None:
            await _consume_registration_invite(db, invite, user.id, request)
        await db.commit()
        token = create_token(user.id)
        _logger.info("User registered: id=%d username=%s mode=%s", user.id, user.username, reg_mode)
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
    # 账号门禁（账号独立 P2，契约 §4）：被控制台禁用的账号登录直接 403。放在凭据校验之后，
    # 避免未认证请求凭状态码探测账号是否被禁用；不计入失败限流（这不是凭据攻击）。
    if getattr(user, "disabled_at", None) is not None:
        raise HTTPException(status_code=403, detail=tr(request, "account_disabled"))
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
        # A2 M2（2026-09-20）：服务器控制台管理员标记（插件/市场管理权口径）；
        # 既有字段一个不动，此处仅追加（Flutter 侧由另一路读取该字段决定插件管理入口）。
        "server_admin": bool(user.server_admin),
        # #68 P3 账号关联：parent_id（NULL=独立主账号）/ is_sub
        "parent_id": user.parent_id,
        "is_sub": bool(user.parent_id),
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
            # #68 P3 账号关联
            "parent_id": user.parent_id,
            "is_sub": bool(user.parent_id),
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
