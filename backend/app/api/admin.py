"""主账号管理 API（#68 修订：按家庭范围隔离，2026-08-28）

- 主账号 = users.is_admin=1；账号关联通过 users.parent_id 建立家庭关系。
- 主账号只能查看和管理自己家庭内（自己 + 直属子账号）的账号，不能看到其他家庭。
- 判定统一走 app.services.permission_service.is_admin_user（DB 权威 + 30s 缓存，env 兜底）。
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select

from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.i18n import tr_lang
from app.models.user import User
from app.services.family_service import get_family_member_ids
from app.services.permission_service import _invalidate_admin_cache, is_admin_user
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
_logger = get_logger("api.admin")


@router.get("/accounts")
async def list_accounts(
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """列出当前用户家庭内的账号 {id, username, nickname, avatar_url, is_admin, parent_id, is_self}。

    独立主账号只看到自己；有子账号的主账号看到自己 + 直属子账号。
    """
    if not await is_admin_user(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_manage_only"))

    async with async_session_factory() as db:
        member_ids = await get_family_member_ids(db, user_id)
        rows = (
            await db.execute(
                select(
                    User.id,
                    User.username,
                    User.nickname,
                    User.avatar_url,
                    User.is_admin,
                    User.parent_id,
                )
                .where(User.id.in_(member_ids))
                .order_by(User.id)
            )
        ).all()

    return {
        "accounts": [
            {
                "id": r.id,
                "username": r.username,
                "nickname": r.nickname,
                "avatar_url": r.avatar_url,
                "is_admin": bool(r.is_admin),
                "parent_id": r.parent_id,
                "is_self": r.id == user_id,
            }
            for r in rows
        ]
    }


@router.put("/accounts/{target_user_id}/admin")
async def set_account_admin(
    target_user_id: int,
    body: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """设置/取消主账号（仅主账号，目标必须在同一家庭内）。

    护栏：
    - 不能操作自己（主账号自身始终是 admin）；
    - 目标必须在当前用户的家庭成员列表内；
    - 子账号可以被授予/取消 admin（主账号决定子账号能否使用管理功能）；
    - 家庭内至少保留一个 admin（取消最后一个 → 400）。
    """
    if not await is_admin_user(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_manage_only"))
    if "enabled" not in body:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "admin_enabled_invalid"))
    enabled = bool(body["enabled"])

    # 不能操作自己
    if target_user_id == user_id:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "admin_cannot_toggle_self"))

    async with async_session_factory() as db:
        # 目标必须在同一家庭内
        member_ids = await get_family_member_ids(db, user_id)
        if target_user_id not in member_ids:
            raise HTTPException(status_code=403, detail=tr_lang(lang, "admin_target_not_in_family"))

        target = (
            await db.execute(select(User).where(User.id == target_user_id))
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "user_not_found"))

        if enabled:
            target.is_admin = True
        else:
            # 家庭内至少保留一个 admin（主账号自己）
            admin_ids = (
                await db.execute(
                    select(User.id).where(
                        User.is_admin.is_(True),
                        User.id.in_(member_ids),
                    )
                )
            ).scalars().all()
            if target_user_id in admin_ids and len(admin_ids) <= 1:
                raise HTTPException(status_code=400, detail=tr_lang(lang, "admin_keep_one"))
            target.is_admin = False

        await db.commit()

    _invalidate_admin_cache()
    _logger.info("admin account set user=%d enabled=%s by=%d", target_user_id, enabled, user_id)
    return {"status": "ok", "user_id": target_user_id, "is_admin": enabled}
