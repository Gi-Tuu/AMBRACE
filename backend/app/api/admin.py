"""主账号管理 API（#46 简化版「主账号管理（选择型）」，2026-08-24）

- 主账号 = users.is_admin=1 的可勾选账号集合，由主账号在设置页直接管理。
- 替代原「批准子账号 + 同步权限」复杂方案（用户 2026-08-24 拍板）。
- 判定统一走 app.services.permission_service.is_admin_user（DB 权威 + 30s 缓存，env 兜底）。
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select

from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.i18n import tr_lang
from app.models.user import User
from app.services.permission_service import _invalidate_admin_cache, is_admin_user
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
_logger = get_logger("api.admin")


@router.get("/accounts")
async def list_accounts(
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """列出全部账号 {id, username, nickname, avatar_url, is_admin, parent_id}，不含 password_hash（仅主账号）"""
    if not await is_admin_user(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_manage_only"))
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(
                    User.id,
                    User.username,
                    User.nickname,
                    User.avatar_url,
                    User.is_admin,
                    User.parent_id,
                ).order_by(User.id)
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
    """设置/取消主账号（仅主账号）；护栏：至少保留一个主账号（取消最后一个 → 400）"""
    if not await is_admin_user(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_manage_only"))
    if "enabled" not in body:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "admin_enabled_invalid"))
    enabled = bool(body["enabled"])

    async with async_session_factory() as db:
        target = (
            await db.execute(select(User).where(User.id == target_user_id))
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "user_not_found"))

        if enabled:
            target.is_admin = True
        else:
            admin_ids = (
                await db.execute(select(User.id).where(User.is_admin.is_(True)))
            ).scalars().all()
            # 护栏：取消最后一个主账号 → 400（含操作者取消自己且无别的 admin 的情况）
            if target_user_id in admin_ids and len(admin_ids) <= 1:
                raise HTTPException(status_code=400, detail=tr_lang(lang, "admin_keep_one"))
            target.is_admin = False

        await db.commit()

    _invalidate_admin_cache()
    _logger.info("admin account set user=%d enabled=%s by=%d", target_user_id, enabled, user_id)
    return {"status": "ok", "user_id": target_user_id, "is_admin": enabled}
