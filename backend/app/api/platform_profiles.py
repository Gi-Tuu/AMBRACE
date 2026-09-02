"""平台档案 API：公开记忆收紧开关（2026-08-12；X5 渠道化——可建档平台=已注册渠道，内核不持有具体平台名）

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


def _channel_platforms() -> set[str]:
    """可建档平台 = 已注册渠道名（X5：内核不持有具体平台名，渠道经注册表上报）"""
    from app.providers.channel import list_channels
    return {c["name"] for c in list_channels()}


@router.get("/{platform}")
async def get_platform_profile(
    platform: str,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """读取平台档案（记忆收紧开关；仅注册渠道可查）"""
    if platform not in _channel_platforms():
        raise HTTPException(status_code=404, detail="unknown platform")
    row = (
        await db.execute(select(PlatformProfile).where(PlatformProfile.platform == platform))
    ).scalar_one_or_none()
    return {
        "platform": platform,
        "memory_restrict": (row.memory_restrict if row is not None else "off") or "off",
    }


@router.put("/{platform}")
async def update_platform_profile(
    platform: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """更新平台档案（仅独立主账号；子账号 403；仅注册渠道可改）"""
    from app.application.family_service import is_sub_account
    if platform not in _channel_platforms():
        raise HTTPException(status_code=404, detail=tr_lang(lang, "platform_not_found"))
    if await is_sub_account(db, user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_only"))
    value = str(data.get("memory_restrict", "off"))
    if value not in _ALLOWED:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "memory_restrict_invalid"))
    row = (
        await db.execute(select(PlatformProfile).where(PlatformProfile.platform == platform))
    ).scalar_one_or_none()
    if row is None:
        row = PlatformProfile(platform=platform, memory_restrict=value)
        db.add(row)
    else:
        row.memory_restrict = value
    await db.commit()
    return {"platform": platform, "memory_restrict": value}
