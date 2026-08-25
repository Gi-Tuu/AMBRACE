"""pets section（步骤5）：宠物信息 → 模板槽「pets_info」。

从 ``context_builder.build_context_legacy`` 迁出，逻辑与旧版完全一致（零行为变化）：
- 只注入用户养的宠物 + 当前角色自己养的 AI 宠物（其他角色养的 AI 宠物不注入，防归属串扰）；
- 只读注入不落库；失败静默返回「无」。

为避免 agent → services 硬 import（依赖方向 api → services → agent），本文件不在模块顶层
import pet_service，而是在 builder 内部惰性引入其数据函数（与 section_mcp 引用 permission_service
同模式）。
"""
from __future__ import annotations

import logging

from app.agent.context.sections import ContextSection, register_section, TARGET_TEMPLATE

_logger = logging.getLogger("agent.context.section_pet")


async def pets_section(state: dict, ctx: dict) -> str:
    """pets 分区：宠物信息（template 槽；无则缺省「无」）。"""
    from sqlalchemy import select, or_ as _or_
    from app.db.database import async_session_factory
    from app.models.pet import Pet as PetModel
    # 惰性引入 service 数据函数（避免 agent → services 硬 import）
    from app.services.pet_service import (
        apply_decay as pet_apply_decay,
        species_label as pet_species_label,
        species_fact as pet_species_fact,
    )

    pets_text = "无"
    try:
        _uid = state.get("user_id", 1)
        _cid = state.get("character_id")
        async with async_session_factory() as db:
            pets_result = await db.execute(
                select(PetModel).where(_or_(
                    (PetModel.user_id == _uid) & (PetModel.owner_type.is_(None)),   # 旧数据（无归属）视为用户宠物
                    (PetModel.user_id == _uid) & (PetModel.owner_type == "user"),   # 用户宠物
                    (PetModel.owner_type == "ai") & (PetModel.owner_id == _cid),    # 当前角色自己养的 AI 宠物
                )).order_by(PetModel.created_at.asc())
            )
            user_pets = pets_result.scalars().all()
        if user_pets:
            pet_lines = []
            for p in user_pets:
                pet_apply_decay(p)
                owner_prefix = "你养的" if (p.owner_type == "ai" and p.owner_id == _cid) else "用户家的"
                pet_lines.append(
                    f"- {owner_prefix}{p.name}（{pet_species_label(p.species)}）：{p.status_text}，"
                    f"饱食度 {p.hunger}%、心情 {p.mood}%、精力 {p.energy}%、清洁度 {p.cleanliness}%"
                    + (f"；习性：{pet_species_fact(p.species)}" if pet_species_fact(p.species) else "")
                )
            pets_text = "\n".join(pet_lines)
    except Exception as e:
        _logger.warning("Failed to load pets: %s", e)
    return pets_text


register_section(ContextSection(
    key="pets",
    builder=pets_section,
    target=TARGET_TEMPLATE,
    slot="pets_info",
    quota_tokens=400,
    order=60,
))
