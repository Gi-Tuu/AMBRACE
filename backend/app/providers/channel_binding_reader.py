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


async def bound_characters_for_runtime(db: AsyncSession, channel: str, tenant_id: int | None) -> list[int]:
    """运行时取某租户在某渠道启用的绑定角色（灰度期双读，见模块 docstring）。"""
    if tenant_id is not None and channel_binding_v2_enabled():
        from app.models.channel import ChannelBinding

        rows = (await db.execute(select(ChannelBinding.character_id).where(
            ChannelBinding.channel == channel, ChannelBinding.tenant_id == int(tenant_id),
            ChannelBinding.enabled.is_(True),
        ))).scalars().all()
        if rows:
            return [int(x) for x in rows]
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
