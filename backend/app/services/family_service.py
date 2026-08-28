# -*- coding: utf-8 -*-
"""账号关联（家庭）服务（#68 账号体系 × API 配置整合 P3）。

- get_family_root_id / get_family_member_ids / count_sub_accounts：家庭关系查询。
- generate_invite_code：独立主账号生成受邀码（复用未过期码；子账号不可发码）。
- redeem_invite_code：兑换受邀码（5 分钟有效、一次性、同事务 used_by 检查防并发，
  禁止关联自己/重复/环状、主账号子账号 ≤6）。
- unlink：主账号踢人 / 子账号自我解除；解除后 parent_id=NULL 不删数据。
- get_family_info：家庭信息（前端主账号/子账号两视图）。

兼容：原 app.services.llm_config_service._family_root_id/_is_sub_account 已迁到本模块，
llm_config_service 统一 import 本模块，保持行为不变、避免双份实现。
"""
from datetime import datetime, timedelta
import secrets

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.user.account_invite import AccountInvite
from app.services.permission_service import _invalidate_admin_cache

# 受邀码有效期（分钟）
INVITE_TTL_MINUTES = 5
# 主账号最多子账号数
MAX_SUB_ACCOUNTS = 6


async def get_family_root_id(db: AsyncSession, user_id: int | None) -> int | None:
    """返回 user_id 的家庭根账号（主账号）。独立主账号返回自己；子账号返回其 parent_id。"""
    if not user_id:
        return None
    uid = (await db.execute(select(User.parent_id).where(User.id == user_id))).scalar_one_or_none()
    if uid:
        return int(uid)
    return int(user_id)


async def is_sub_account(db: AsyncSession, user_id: int | None) -> bool:
    """user_id 是否为子账号（parent_id 非空）。"""
    if not user_id:
        return False
    parent = (await db.execute(select(User.parent_id).where(User.id == user_id))).scalar_one_or_none()
    return bool(parent)


async def get_family_member_ids(db: AsyncSession, user_id: int) -> list[int]:
    """返回 user_id 所属家庭的全部成员 id（根账号 + 全部子账号）。"""
    root = await get_family_root_id(db, user_id)
    if not root:
        return []
    sub_ids = (await db.execute(
        select(User.id).where(User.parent_id == root)
    )).scalars().all()
    return [root, *[int(i) for i in sub_ids]]


async def count_sub_accounts(db: AsyncSession, user_id: int) -> int:
    """主账号的直属子账号数（user_id 的 parent_id 指向 user_id 的数量）。"""
    root = await get_family_root_id(db, user_id)
    if not root:
        return 0
    n = (await db.execute(
        select(func.count()).select_from(User).where(User.parent_id == root)
    )).scalar_one()
    return int(n)


async def _in_family(db: AsyncSession, creator_id: int, member_id: int) -> bool:
    """member_id 是否在 creator_id 的家庭成员内（含 creator 自身）——环状防御。"""
    if creator_id == member_id:
        return True
    return member_id in await get_family_member_ids(db, creator_id)


async def generate_invite_code(db: AsyncSession, user_id: int) -> dict:
    """独立主账号生成受邀码（复用未过期码）。子账号返回 403。"""
    creator = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if creator is None:
        raise HTTPException(status_code=404, detail="user not found")
    if creator.parent_id is not None:
        # 仅独立主账号可发码
        raise HTTPException(status_code=403, detail="sub account cannot generate invite")

    now = datetime.utcnow()
    # 复用未过期且未使用的码
    existing = (await db.execute(
        select(AccountInvite).where(
            AccountInvite.creator_id == user_id,
            AccountInvite.used_by.is_(None),
            AccountInvite.expires_at > now,
        ).order_by(AccountInvite.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        return {"code": existing.code, "expires_at": existing.expires_at.isoformat()}

    # 生成唯一 8 位大写 hex 码
    while True:
        code = secrets.token_hex(4).upper()
        dup = (await db.execute(
            select(AccountInvite.id).where(AccountInvite.code == code)
        )).scalar_one_or_none()
        if not dup:
            break
    invite = AccountInvite(
        code=code,
        creator_id=user_id,
        expires_at=now + timedelta(minutes=INVITE_TTL_MINUTES),
    )
    db.add(invite)
    await db.flush()
    return {"code": code, "expires_at": invite.expires_at.isoformat()}


async def redeem_invite_code(db: AsyncSession, user_id: int, code: str | None) -> dict:
    """兑换受邀码：5 分钟有效、一次性、同事务 used_by 检查防并发、禁自己/重复/环状、≤6。"""
    code = (code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="invite code required")

    invite = (await db.execute(
        select(AccountInvite).where(AccountInvite.code == code)
    )).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="invite not found")

    # 禁止关联自己
    if invite.creator_id == user_id:
        raise HTTPException(status_code=400, detail="cannot link self")

    creator = (await db.execute(
        select(User).where(User.id == invite.creator_id)
    )).scalar_one_or_none()
    if creator is None:
        raise HTTPException(status_code=404, detail="creator not found")
    # 发码者须为独立主账号
    if creator.parent_id is not None:
        raise HTTPException(status_code=400, detail="creator not standalone")

    redeemer = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if redeemer is None:
        raise HTTPException(status_code=404, detail="user not found")
    # 兑换者须为独立主账号（未关联）
    if redeemer.parent_id is not None:
        raise HTTPException(status_code=400, detail="already linked")

    now = datetime.utcnow()
    # 一次性：已使用 → 拒绝
    if invite.used_by is not None:
        raise HTTPException(status_code=400, detail="invite already used")
    # 过期（5 分钟有效）
    if invite.expires_at is not None and invite.expires_at < now:
        raise HTTPException(status_code=400, detail="invite expired")
    # 环状/重复防御：两个 root 之间互不隶属
    if await _in_family(db, invite.creator_id, user_id) or await _in_family(db, user_id, invite.creator_id):
        raise HTTPException(status_code=400, detail="cycle detected")
    # 主账号子账号 ≤6
    if await count_sub_accounts(db, invite.creator_id) >= MAX_SUB_ACCOUNTS:
        raise HTTPException(status_code=400, detail="sub account limit reached")

    # 同事务 used_by 条件更新（防并发重复兑换：UPDATE ... WHERE used_by IS NULL，rowcount=1 才成功）
    result = await db.execute(
        update(AccountInvite)
        .where(AccountInvite.id == invite.id, AccountInvite.used_by.is_(None))
        .values(used_by=user_id, used_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="invite already used")

    redeemer.parent_id = invite.creator_id
    redeemer.is_admin = False  # 子账号不保留管理员权限（#68 修订）
    await db.flush()
    _invalidate_admin_cache()
    return {
        "ok": True,
        "root_id": invite.creator_id,
        "main_account": {
            "id": creator.id,
            "username": creator.username,
            "nickname": creator.nickname,
        },
    }


async def unlink(db: AsyncSession, acting_user_id: int, target_user_id: int) -> dict:
    """解除关联：主账号踢人（带 target_user_id）或子账号自我解除。parent_id=NULL，不删数据。"""
    target = (await db.execute(select(User).where(User.id == target_user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")

    if acting_user_id == target_user_id:
        # 子账号自己解除
        if target.parent_id is None:
            raise HTTPException(status_code=400, detail="not linked")
        target.parent_id = None
        target.is_admin = True  # 回到独立主账号，恢复管理员权限（#68 修订）
        await db.flush()
        _invalidate_admin_cache()
        return {"ok": True, "user_id": target_user_id, "parent_id": None}

    # 主账号踢人：acting 必须是 target 的父账号
    if target.parent_id != acting_user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    target.parent_id = None
    target.is_admin = True  # 回到独立主账号，恢复管理员权限（#68 修订）
    await db.flush()
    _invalidate_admin_cache()
    return {"ok": True, "user_id": target_user_id, "parent_id": None}


async def get_family_info(db: AsyncSession, user_id: int) -> dict:
    """家庭信息：主账号视图（成员数/子账号列表）+ 子账号视图（主账号昵称）。"""
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    root_id = await get_family_root_id(db, user_id)
    is_sub = user.parent_id is not None
    member_ids = await get_family_member_ids(db, user_id)
    subs = (await db.execute(
        select(User).where(User.parent_id == root_id).order_by(User.id)
    )).scalars().all()

    sub_list = [
        {
            "id": s.id,
            "username": s.username,
            "nickname": s.nickname,
            "avatar_url": s.avatar_url,
            "is_admin": bool(s.is_admin),
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in subs
    ]

    main_account = None
    if is_sub:
        main = (await db.execute(select(User).where(User.id == root_id))).scalar_one_or_none()
        if main is not None:
            main_account = {
                "id": main.id,
                "username": main.username,
                "nickname": main.nickname,
            }

    return {
        "is_sub": bool(is_sub),
        "parent_id": user.parent_id,
        "root_id": root_id,
        "member_count": len(member_ids),
        "main_account": main_account,
        "sub_accounts": sub_list,
    }
