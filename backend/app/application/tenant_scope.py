# -*- coding: utf-8 -*-
"""统一租户解析（一机多主 / SaaS S0 共用，2026-09-05）。

tenant_id = 独立主账号（家庭 root）的 user_id，经 get_family_root_id 解析：
独立主账号返回自己，子账号返回其主账号。渠道绑定、SaaS 隔离都用它，禁止各处各算。
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.family_service import get_family_root_id, is_sub_account


class SubAccountForbidden(PermissionError):
    """子账号不可绑定渠道（→403，i18n channel_bind_main_only）。

    定义在本模块（family 之上的最低公共层），ChannelBindingService 再导出，避免加载环。
    """


async def resolve_tenant(db: AsyncSession, actor_user_id: int | None) -> int:
    """统一租户键：独立主账号=自己；子账号=其主账号。解析失败抛 ValueError。"""
    root = await get_family_root_id(db, actor_user_id)
    if not root:
        raise ValueError("tenant root not found")
    return int(root)


async def assert_standalone_owner(db: AsyncSession, actor_user_id: int | None) -> int:
    """渠道绑定仅独立主账号可写；返回 tenant_id。

    子账号抛 PermissionError（调用方转 HTTP 403 / i18n channel_bind_main_only）。
    """
    if await is_sub_account(db, actor_user_id):
        raise SubAccountForbidden("channel_bind_main_only")
    return await resolve_tenant(db, actor_user_id)
