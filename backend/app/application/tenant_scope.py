# -*- coding: utf-8 -*-
"""统一租户解析（一机多主 / SaaS S0 共用，2026-09-05）。

tenant_id = 独立主账号（家庭 root）的 user_id，经 get_family_root_id 解析：
独立主账号返回自己，子账号返回其主账号。渠道绑定、SaaS 隔离都用它，禁止各处各算。

账号独立 P1（2026-09-19）：资源隔离的租户键定义在 ``app.application.tenant_service``
（含 ``tenant_key_mode`` family|user 口径开关）。本模块是**渠道绑定面**的入口，语义恒为家庭根，
**刻意不跟随口径开关**（见 ``resolve_tenant`` docstring）。
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.family_service import get_family_root_id, is_sub_account


class SubAccountForbidden(PermissionError):
    """子账号不可绑定渠道（→403，i18n channel_bind_main_only）。

    定义在本模块（family 之上的最低公共层），ChannelBindingService 再导出，避免加载环。
    """


async def resolve_tenant(db: AsyncSession, actor_user_id: int | None) -> int:
    """统一租户键：独立主账号=自己；子账号=其主账号。解析失败抛 ValueError。

    **口径护栏（2026-09-19 P2 前置）**：渠道绑定的「主账号 + 其子账号共用一个绑定」是产品契约
    （channel_binding_v2 已转正），因此本函数**恒按家庭根解析**，**不跟随** `tenant_key_mode` 开关——
    否则把资源隔离口径切到 `user` 时会静默改掉渠道绑定语义。资源隔离请用
    `app/application/tenant_service.py`（那才是跟随口径开关的那一套）。
    """
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
