# -*- coding: utf-8 -*-
"""渠道绑定服务（一机多主 / per-账号化，2026-09-05）——租户化绑定裁决唯一写入口。

- 绑定三元组 (channel, tenant_id, bot_account_id) → character_id，DB 唯一约束兜底并发双绑；
- binding mode：一律按 bot_single 建模；family_single（一个家庭该渠道只一个角色）由应用层
  强制 bot_account_id="default" 表达（渠道 meta["binding"]["mode"] 上报，缺省 family_single）；
- 错误语义沿用既有 i18n key（channel_bind_main_only / channel_bind_cross_family /
  channel_bind_occupied 等），以异常类型承载、由调用方（API/插件路由）转 HTTP 状态码；
- flag channel_binding_v2 默认关：关闭时渠道插件走旧全局 config 路径（见
  app/providers/channel_binding_reader.py 回落分支），本服务仅被 flag 开路径或测试调用。
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import ChannelBinding
from app.models.character import AICharacter
from app.application.family_service import get_family_member_ids
from app.application.tenant_scope import SubAccountForbidden  # noqa: F401 - 再导出（插件路由经本模块 isinstance 判型）

DEFAULT_BOT = "default"


class CrossFamilyCharacter(PermissionError):
    """所选角色不属于调用者家庭（→403，i18n channel_bind_cross_family）。"""


class ChannelOccupied(ValueError):
    """family_single 渠道该家庭已绑定其它角色（→400，i18n channel_bind_occupied）。"""


class BotAccountRequired(ValueError):
    """bot_single 渠道缺 bot_account_id（→400）。"""


class PhysicalSingletonTaken(ValueError):
    """物理单实例渠道（如抖音：单浏览器 profile）已被其它租户绑定（→409，i18n channel_bind_physical_taken）。

    C3 路线 A（最小止血，2026-09-05）：抖音在多开风控/路线 B（profile 分目录+全查询租户化）
    立项前，第二主账号绑定明确拒绝，防浏览器 profile 串号。
    """


def _binding_mode(channel: str) -> str:
    """渠道绑定模式（渠道 meta["binding"] 上报；未注册渠道缺省 family_single）。"""
    from app.providers.channel import channel_meta

    meta = channel_meta(channel) or {}
    b = meta.get("binding") or {}
    return str(b.get("mode") or "family_single")


def _is_physical_singleton(channel: str) -> bool:
    """渠道是否上报物理单实例（meta["binding"]["physical_singleton"]=True）。"""
    from app.providers.channel import channel_meta

    meta = channel_meta(channel) or {}
    b = meta.get("binding") or {}
    return bool(b.get("physical_singleton"))


async def list_bindings(db: AsyncSession, tenant_id: int, channel: str | None = None) -> list[ChannelBinding]:
    """列出租户名下的绑定（可按渠道过滤；按 id 稳定排序）。"""
    q = select(ChannelBinding).where(ChannelBinding.tenant_id == int(tenant_id))
    if channel:
        q = q.where(ChannelBinding.channel == channel)
    return list((await db.execute(q.order_by(ChannelBinding.id))).scalars().all())


async def upsert_binding(db: AsyncSession, actor_user_id: int, channel: str,
                         character_id: int, bot_account_id: str = DEFAULT_BOT,
                         bot_label: str = "", extra: dict | None = None) -> ChannelBinding:
    """绑定/换绑（唯一写入口）：
    1) 仅独立主账号可写（子账号 → SubAccountForbidden）；
    2) family_single 强制 bot="default"；bot_single 必须给非空稳定 bot 键；
    3) 角色必须属于调用者家庭（跨家庭 → CrossFamilyCharacter）；
    4) family_single：该家庭此渠道已有别的角色 → ChannelOccupied（先解绑/换绑）；
    4.5) physical_singleton 渠道（C3 路线 A）：已有**其它租户** enabled 行 → PhysicalSingletonTaken；
    5) upsert (channel,tenant,bot)（并发双绑由 DB 唯一约束兜底，撞约束抛 IntegrityError 由调用方处理）。
    """
    from app.application.tenant_scope import assert_standalone_owner

    tenant_id = await assert_standalone_owner(db, actor_user_id)

    mode = _binding_mode(channel)
    raw_bot = str(bot_account_id or "").strip()
    if mode == "family_single":
        bot_account_id = DEFAULT_BOT
    elif not raw_bot:
        raise BotAccountRequired("bot_account_id required for bot_single channel")
    else:
        bot_account_id = raw_bot

    if _is_physical_singleton(channel):
        other = (await db.execute(select(ChannelBinding).where(
            ChannelBinding.channel == channel,
            ChannelBinding.tenant_id != int(tenant_id),
            ChannelBinding.enabled.is_(True),
        ))).scalars().first()
        if other is not None:
            raise PhysicalSingletonTaken("channel_bind_physical_taken")

    family_ids = await get_family_member_ids(db, actor_user_id)
    ch = await db.get(AICharacter, int(character_id))
    if ch is None or ch.user_id not in family_ids:
        raise CrossFamilyCharacter("channel_bind_cross_family")

    if mode == "family_single":
        occ = (await db.execute(select(ChannelBinding).where(
            ChannelBinding.channel == channel,
            ChannelBinding.tenant_id == tenant_id,
            ChannelBinding.character_id != int(character_id),
        ))).scalars().first()
        if occ is not None:
            raise ChannelOccupied("channel_bind_occupied")

    row = (await db.execute(select(ChannelBinding).where(
        ChannelBinding.channel == channel,
        ChannelBinding.tenant_id == tenant_id,
        ChannelBinding.bot_account_id == bot_account_id,
    ))).scalar_one_or_none()
    if row is None:
        row = ChannelBinding(channel=channel, tenant_id=tenant_id, owner_user_id=int(actor_user_id),
                             bot_account_id=bot_account_id)
        db.add(row)
    row.character_id = int(character_id)
    row.bot_label = bot_label or row.bot_label
    row.enabled = True
    if extra is not None:
        row.extra_json = json.dumps(extra, ensure_ascii=False)
    await db.flush()
    return row


async def remove_binding(db: AsyncSession, actor_user_id: int, channel: str,
                         bot_account_id: str = DEFAULT_BOT) -> bool:
    """解绑（物理删绑定行；渠道自有绑定/凭据行由各渠道插件自行清理）。返回是否删除了行。"""
    from app.application.tenant_scope import assert_standalone_owner

    tenant_id = await assert_standalone_owner(db, actor_user_id)
    row = (await db.execute(select(ChannelBinding).where(
        ChannelBinding.channel == channel, ChannelBinding.tenant_id == tenant_id,
        ChannelBinding.bot_account_id == str(bot_account_id or DEFAULT_BOT),
    ))).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.flush()
        return True
    return False


async def resolve_character(db: AsyncSession, channel: str, tenant_id: int,
                            bot_account_id: str = DEFAULT_BOT) -> ChannelBinding | None:
    """入站路由用：按 (渠道,租户,bot) 取启用中的绑定。"""
    return (await db.execute(select(ChannelBinding).where(
        ChannelBinding.channel == channel, ChannelBinding.tenant_id == int(tenant_id),
        ChannelBinding.bot_account_id == str(bot_account_id or DEFAULT_BOT),
        ChannelBinding.enabled.is_(True),
    ))).scalar_one_or_none()
