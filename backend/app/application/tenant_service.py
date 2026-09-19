# -*- coding: utf-8 -*-
"""统一租户归属判定（账号独立 P1 地基，2026-09-19）。

口径 A（用户拍板）：``tenant_key = 家庭根账号 user_id``（``get_family_root_id``）。
- 跨家庭互相不可见：归属不同 tenant_key → 404/403；
- 家庭内共享：同一 tenant_key（主账号 + 其子账号）互可见、保持既有家庭语义。

口径开关 ``tenant_key_mode``：``family``（默认）| ``user``。切成「按账号彻底独立」只改本模块
一处：``user`` 模式下 tenant_key 退化为账号自身、``tenant_scope_ids`` 退化为 ``[自己]``，
所有走本模块的归属判断同时收紧为每账号隔离（调用方零改动）。

单一出口纪律：用户维度资源的归属判断（角色/记忆/日记/朋友圈/上传/四模态配置/定时器/群聊）
一律走本模块，禁止各处各算。三层语义：

- ``tenant_key``          —— 单账号 → 租户键（写归属、审计、跨家庭比较）；
- ``tenant_scope_ids``    —— 列表/范围查询的 user_id 白名单（family=家庭成员，user=自己）；
- ``ensure_tenant_owner`` —— 取物后的归属校验（不属于本租户 → 404，与「不存在」同观感，
  避免跨家庭探测资源是否存在）。

实现委托 ``app.application.family_service``（家庭关系唯一实现）；渠道绑定既有的
``app.application.tenant_scope`` 已改为委托本模块，保持同源。
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.family_service import get_family_member_ids, get_family_root_id
from app.i18n import tr_lang

# 口径取值（tenant_key_mode）
TENANT_KEY_MODE_FAMILY = "family"
TENANT_KEY_MODE_USER = "user"
_VALID_MODES = (TENANT_KEY_MODE_FAMILY, TENANT_KEY_MODE_USER)

# 运行时覆盖（测试/运维热切用；None=跟随 settings.tenant_key_mode）
_mode_override: str | None = None


def get_tenant_key_mode() -> str:
    """当前口径：``family``（默认，家庭共享）| ``user``（每账号独立）。

    优先级：运行时覆盖（set_tenant_key_mode）> settings.tenant_key_mode（.env: TENANT_KEY_MODE）。
    非法值一律回落 family（fail-safe：默认口径不收紧，行为与改造前一致）。
    """
    if _mode_override in _VALID_MODES:
        return _mode_override
    try:
        from app.config import settings
        raw = str(getattr(settings, "tenant_key_mode", "") or "").strip().lower()
    except Exception:
        raw = ""
    return raw if raw in _VALID_MODES else TENANT_KEY_MODE_FAMILY


def set_tenant_key_mode(mode: str | None) -> str:
    """设置口径（None=清除覆盖，回落到 settings）。返回设置后的生效口径。

    仅接受 family/user；非法值抛 ValueError（显式拒绝，避免静默错配口径）。
    """
    global _mode_override
    if mode is None:
        _mode_override = None
        return get_tenant_key_mode()
    norm = str(mode).strip().lower()
    if norm not in _VALID_MODES:
        raise ValueError(f"invalid tenant_key_mode: {mode!r} (expect {'/'.join(_VALID_MODES)})")
    _mode_override = norm
    return norm


async def tenant_key(db: AsyncSession, user_id: int | None) -> int | None:
    """账号 → 租户键。

    - family 模式：家庭根账号 user_id（独立主账号=自己，子账号=其主账号）；
    - user 模式：账号自身（每账号独立）。
    无 user_id（未登录/服务器级哨兵）→ None。
    """
    if not user_id:
        return None
    if get_tenant_key_mode() == TENANT_KEY_MODE_USER:
        return int(user_id)
    root = await get_family_root_id(db, user_id)
    return int(root) if root else None


async def tenant_scope_ids(db: AsyncSession, user_id: int | None) -> list[int]:
    """列表/范围查询的账号白名单（SQL ``user_id IN (...)`` 用）。

    - family 模式：家庭成员（根账号 + 全部直属子账号）——家庭内共享；
    - user 模式：``[user_id]``——每账号独立。
    无 user_id → 空表（调用方应已由鉴权拦截）。
    """
    if not user_id:
        return []
    if get_tenant_key_mode() == TENANT_KEY_MODE_USER:
        return [int(user_id)]
    ids = await get_family_member_ids(db, user_id)
    return [int(i) for i in ids] or [int(user_id)]


async def same_tenant(db: AsyncSession, actor_user_id: int | None, owner_user_id: int | None) -> bool:
    """actor 与资源归属者是否同一租户（跨家庭 False）。"""
    if not actor_user_id or not owner_user_id:
        return False
    a = await tenant_key(db, actor_user_id)
    b = await tenant_key(db, owner_user_id)
    return a is not None and a == b


async def ensure_tenant_owner(
    db: AsyncSession,
    actor_user_id: int | None,
    owner_user_id: int | None,
    *,
    lang: str = "zh",
    detail: str = "character_not_found",
) -> None:
    """归属校验：资源归属者不在 actor 租户内 → 404（与「不存在」同观感，防跨家庭探测）。

    ``detail`` 用调用方既有 i18n key（默认角色不存在，保持既有文案不变）。
    """
    if not await same_tenant(db, actor_user_id, owner_user_id):
        raise HTTPException(status_code=404, detail=tr_lang(lang, detail))


async def tenant_character_ids(db: AsyncSession, actor_user_id: int | None) -> list[int]:
    """本租户可见的角色 id 列表（按角色维度归属的资源：记忆/日记/朋友圈/定时器/群聊）。"""
    from app.models.character import AICharacter
    scope = await tenant_scope_ids(db, actor_user_id)
    if not scope:
        return []
    rows = (await db.execute(select(AICharacter.id).where(AICharacter.user_id.in_(scope)))).scalars().all()
    return [int(i) for i in rows]


__all__ = [
    "TENANT_KEY_MODE_FAMILY",
    "TENANT_KEY_MODE_USER",
    "get_tenant_key_mode",
    "set_tenant_key_mode",
    "tenant_key",
    "tenant_scope_ids",
    "same_tenant",
    "ensure_tenant_owner",
    "tenant_character_ids",
]
