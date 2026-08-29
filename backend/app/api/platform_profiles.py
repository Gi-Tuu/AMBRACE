"""平台档案 API：抖音公开记忆收紧开关（2026-08-12）

memory_restrict：
- off：现状筛选（排除身份画像 identity + 含用户姓名的内容）
- relationship：额外排除 relationship 子类型（表白/金钱等无姓名但私密内容）
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.db.database import get_db
from app.models.social import PlatformProfile

router = APIRouter(prefix="/api/v1/platform-profile", tags=["PlatformProfile"])

_ALLOWED = ("off", "relationship")


@router.get("/douyin")
async def get_douyin_profile(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """读取抖音平台档案（记忆收紧开关）"""
    row = (
        await db.execute(select(PlatformProfile).where(PlatformProfile.platform == "douyin"))
    ).scalar_one_or_none()
    return {
        "platform": "douyin",
        "memory_restrict": (row.memory_restrict if row is not None else "off") or "off",
    }


@router.put("/douyin")
async def update_douyin_profile(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """更新抖音平台档案（仅独立主账号；子账号 403）"""
    from app.services.family_service import is_sub_account
    if await is_sub_account(db, user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_only"))
    value = str(data.get("memory_restrict", "off"))
    if value not in _ALLOWED:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "memory_restrict_invalid"))
    row = (
        await db.execute(select(PlatformProfile).where(PlatformProfile.platform == "douyin"))
    ).scalar_one_or_none()
    if row is None:
        row = PlatformProfile(platform="douyin", memory_restrict=value)
        db.add(row)
    else:
        row.memory_restrict = value
    await db.commit()
    return {"platform": "douyin", "memory_restrict": value}
