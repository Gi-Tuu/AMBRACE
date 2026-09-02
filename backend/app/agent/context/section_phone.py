"""phone sections（步骤5）：手机感知 / 小手机 → 模板槽「phone_perception」「phone_desktop」。

从 ``context_builder.build_context_legacy`` 迁出，逻辑与旧版完全一致（零行为变化）：
- phone_perception：用户授权采集的屏幕/剪贴板/相册快照（仅文本注入；失败静默「无」）；
- phone_desktop：角色日历备注 + 浏览器最近搜索（仅文本注入；失败静默「无」）。

避免 agent → services 硬 import：builder 内部惰性引入 phone_service / phone_desktop_service
（与 section_mcp 引用 permission_service 同模式）。
"""
from __future__ import annotations

import logging

from app.agent.context.sections import ContextSection, register_section, TARGET_TEMPLATE

_logger = logging.getLogger("agent.context.section_phone")


async def phone_perception_section(state: dict, ctx: dict) -> str:
    """phone_perception 分区：手机感知（template 槽；无则缺省「无」）。"""
    from app.application.phone_service import get_recent_perception_text

    phone_perception = "无"
    try:
        phone_text = await get_recent_perception_text(state.get("user_id", 1))
        if phone_text:
            phone_perception = phone_text
    except Exception as e:
        _logger.warning("Failed to load phone perception: %s", e)
    return phone_perception


async def phone_desktop_section(state: dict, ctx: dict) -> str:
    """phone_desktop 分区：小手机（角色日历备注 + 浏览器最近搜索）（template 槽；无则缺省「无」）。"""
    from app.application.phone_desktop_service import get_phone_desktop_inject_text

    phone_desktop = "无"
    try:
        _cid = state.get("character_id")
        if _cid:
            _pdt = await get_phone_desktop_inject_text(int(_cid))
            if _pdt:
                phone_desktop = _pdt
    except Exception as e:
        _logger.warning("Phone desktop inject failed: %s", e)
    return phone_desktop


register_section(ContextSection(
    key="phone_perception",
    builder=phone_perception_section,
    target=TARGET_TEMPLATE,
    slot="phone_perception",
    quota_tokens=400,
    order=61,
))
register_section(ContextSection(
    key="phone_desktop",
    builder=phone_desktop_section,
    target=TARGET_TEMPLATE,
    slot="phone_desktop",
    quota_tokens=400,
    order=62,
))
