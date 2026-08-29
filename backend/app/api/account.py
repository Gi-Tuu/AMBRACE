# -*- coding: utf-8 -*-
"""账号关联 API（/api/v1/account，#68 P3）。

- GET /family : 家庭信息（主账号/子账号两视图）
- POST /invite-code : 独立主账号生成受邀码
- POST /link : 兑换受邀码（5 分钟有效、一次性、防并发）
- DELETE /link : 解除关联（主账号踢人带 target_user_id；子账号自己解除也支持）
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.services import family_service as _svc

router = APIRouter(prefix="/api/v1/account", tags=["Account"])


class RedeemRequest(BaseModel):
    code: str


@router.get("/family")
async def get_family(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """家庭信息：主账号视图（成员数/子账号列表）或子账号视图（主账号昵称）。"""
    return await _svc.get_family_info(db, user_id)


@router.post("/invite-code")
async def generate_invite_code(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """独立主账号生成受邀码（复用未过期码；子账号 403）。"""
    return await _svc.generate_invite_code(db, user_id)


@router.post("/link")
async def redeem_invite_code(data: RedeemRequest, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """兑换受邀码（5 分钟有效、一次性、同事务防并发、禁自己/重复/环状、≤6）。"""
    return await _svc.redeem_invite_code(db, user_id, data.code)


@router.delete("/link")
async def unlink_link(
    target_user_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """解除关联：主账号踢人带 target_user_id；子账号自己解除（target_user_id 省略时=自己）。"""
    target = target_user_id if target_user_id is not None else user_id
    return await _svc.unlink(db, user_id, target)
