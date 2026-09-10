# -*- coding: utf-8 -*-
"""current_state_anchor section（C3，2026-09-10）：用户当前现状权威锚点（追加块）。

- 紧邻记忆/世界区注入（order=42，在 memories=40 之后、user_now=44/world_facts=45 之前）；
- include_profile_location=False：城市交给既有 section_world.location（order=81），避免重复；
  本 section 主要承载 GlobalUserFact 非位置槽（job/relationship…，flag 门控）与 per-char 用户现状；
- 无任何授权现状时返回空列表（不注入，默认零行为变化）。
"""
from __future__ import annotations

from app.agent.context.sections import ContextSection, register_section, TARGET_APPEND


async def current_state_section(state: dict, ctx: dict) -> list[str]:
    from app.memory.current_state import current_user_state_anchor
    txt = await current_user_state_anchor(
        character_id=state.get("character_id"),
        user_id=state.get("user_id", 1),
        include_profile_location=False,
    )
    return [txt] if txt else []


register_section(ContextSection(
    key="current_state_anchor", builder=current_state_section, target=TARGET_APPEND,
    quota_tokens=220, order=42,
))
