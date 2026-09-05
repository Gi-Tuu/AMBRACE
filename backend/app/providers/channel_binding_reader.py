# -*- coding: utf-8 -*-
"""渠道绑定兼容读取层（一机多主灰度期双读，2026-09-05）。

渠道插件禁止再直读插件全局 config 的 allowed_character_ids，统一走本模块：
- flag channel_binding_v2 开且新表有该租户数据 → 读 channel_bindings（按租户隔离）；
- flag 关 / 新表无该租户数据 → 回落旧全局 config 串（单主部署语义等价，零行为变化）。

回落口径（交接拍板）：新表优先；空表或 flag 关 → 回落旧全局 config 串。
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_FLAG_KEY = "channel_binding_v2"

# 渠道名（注册表 key）→ 绑定插件名（plugins 表 name）
_PLUGIN_OF = {
    "wechat": "wechat_ilink",
    "douyin": "douyin_mcp",
}


def channel_binding_v2_enabled() -> bool:
    """读 AGENT_FLAGS 的 channel_binding_v2（runtime_flags 可热覆盖；默认关=回落旧路径）。"""
    try:
        from app.agent.loop import AGENT_FLAGS  # noqa: PLC0415 - 惰性 import 防加载环

        return bool(AGENT_FLAGS.get(_FLAG_KEY, False))
    except Exception:
        return False


def plugin_of(channel: str) -> str:
    """渠道名 → 绑定插件名（未映射渠道原样返回）。"""
    return _PLUGIN_OF.get(channel, channel)


async def _fallback_global_ids(db: AsyncSession, channel: str) -> list[int]:
    """回落：读插件全局 config 的 allowed_character_ids（逗号分隔单选串）。"""
    from app.models.plugin import Plugin

    row = (await db.execute(select(Plugin).where(Plugin.name == plugin_of(channel)))).scalar_one_or_none()
    cfg = {}
    if row is not None:
        try:
            cfg = json.loads(row.config_json or "{}")
        except Exception:
            cfg = {}
    raw = cfg.get("allowed_character_ids", "")
    if isinstance(raw, list):
        return [int(x) for x in raw if str(x).strip().isdigit()]
    return [int(x) for x in str(raw or "").split(",") if x.strip().isdigit()]


async def fallback_global_ids(db: AsyncSession, channel: str) -> list[int]:
    """公开包装：读插件全局 config 的 allowed_character_ids（API 灰度合成行也用它）。"""
    return await _fallback_global_ids(db, channel)


async def channel_taken_over(db: AsyncSession, channel: str) -> bool:
    """该渠道是否已被 v2 接管（channel_bindings 中任意租户存在该渠道行）。

    C2（2026-09-05 落地审查）：接管后，无行租户一律回落空（杜绝跨租户回落与解绑幽灵）；
    渠道表全空（尚无任何租户走过 v2 写路径）才回落旧全局串。
    """
    from app.models.channel import ChannelBinding

    row = (await db.execute(select(ChannelBinding.id).where(
        ChannelBinding.channel == channel).limit(1))).first()
    return row is not None


async def filter_ids_by_tenant(db: AsyncSession, ids: list[int], tenant_id: int) -> list[int]:
    """按角色归属家庭 root 过滤（C2：回落全局串只保留属于该租户的角色）。"""
    from app.application.family_service import get_family_root_id
    from app.models.character import AICharacter

    out: list[int] = []
    for cid in ids:
        ch = await db.get(AICharacter, cid)
        if ch is None:
            continue
        root = await get_family_root_id(db, ch.user_id)
        if root == int(tenant_id):
            out.append(cid)
    return out


async def bound_characters_for_runtime(db: AsyncSession, channel: str, tenant_id: int | None) -> list[int]:
    """运行时取某租户在某渠道启用的绑定角色。

    flag 开（C2 判据）：
    1) 该租户新表有行 → 新表（租户隔离）；
    2) 新表无行但渠道已被 v2 接管（任意租户有行）→ 返回 []（隔离/防解绑幽灵，绝不跨租户回落）；
    3) 渠道表全空 → 回落旧全局串，但按角色归属家庭 root 过滤后返回（等价单主，不越权）。
    flag 关 → 旧全局串原样（单主部署语义等价）。
    """
    if tenant_id is not None and channel_binding_v2_enabled():
        from app.models.channel import ChannelBinding

        rows = (await db.execute(select(ChannelBinding.character_id).where(
            ChannelBinding.channel == channel, ChannelBinding.tenant_id == int(tenant_id),
            ChannelBinding.enabled.is_(True),
        ))).scalars().all()
        if rows:
            return [int(x) for x in rows]
        if await channel_taken_over(db, channel):
            return []
        ids = await _fallback_global_ids(db, channel)
        return await filter_ids_by_tenant(db, ids, int(tenant_id))
    return await _fallback_global_ids(db, channel)


async def all_bound_characters(db: AsyncSession, channel: str) -> list[tuple[int, int]]:
    """无租户上下文的插件侧遍历用：[(tenant_id, character_id), ...]（tenant 为角色归属家庭 root）。

    - flag 开：channel_bindings 启用行直接返回（tenant 即租户键）；
    - flag 关：旧全局 config 串 → 逐角色解析其归属家庭 root（单主部署等价）。
    解析不出归属的角色跳过（不猜租户）。
    """
    from app.application.family_service import get_family_root_id
    from app.models.character import AICharacter

    if channel_binding_v2_enabled():
        from app.models.channel import ChannelBinding

        rows = (await db.execute(select(ChannelBinding).where(
            ChannelBinding.channel == channel, ChannelBinding.enabled.is_(True),
        ).order_by(ChannelBinding.id))).scalars().all()
        if rows:
            return [(int(r.tenant_id), int(r.character_id)) for r in rows]

    out: list[tuple[int, int]] = []
    for cid in await _fallback_global_ids(db, channel):
        ch = await db.get(AICharacter, cid)
        if ch is None:
            continue
        root = await get_family_root_id(db, ch.user_id)
        if root:
            out.append((int(root), cid))
    return out
