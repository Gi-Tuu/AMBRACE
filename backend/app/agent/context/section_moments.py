"""moments section（步骤5）：朋友圈动态 → 模板槽「moments」。

从 ``context_builder.build_context_legacy`` 迁出，逻辑与旧版完全一致（零行为变化）：
- 角色自己最近 1 条 + 用户最近 3 条（近 7 天）；
- 失败静默返回「暂无」。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from app.agent.context.sections import ContextSection, register_section, TARGET_TEMPLATE

_logger = logging.getLogger("agent.context.section_moments")


async def moments_section(state: dict, ctx: dict) -> str:
    """moments 分区：朋友圈动态（template 槽；无则缺省「暂无」）。"""
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.moment import AIMoment

    moments_text = "\u6682\u65e0"
    try:
        moments_lines = []
        async with async_session_factory() as db:
            own_result = await db.execute(
                select(AIMoment)
                .where(AIMoment.character_id == state["character_id"], AIMoment.is_active == True)  # noqa: E712
                .order_by(AIMoment.created_at.desc())
                .limit(1)
            )
            own = own_result.scalars().all()
            user_result = await db.execute(
                select(AIMoment)
                .where(
                    AIMoment.sender_type == "user",
                    AIMoment.user_id == state.get("user_id", 1),
                    AIMoment.is_active == True,  # noqa: E712
                    AIMoment.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7),
                )
                .order_by(AIMoment.created_at.desc())
                .limit(3)
            )
            user_moments = user_result.scalars().all()
        if own:
            moments_lines.append(f"[\u4f60\u53d1\u7684 {str(own[0].created_at)[:10]}] {own[0].content[:100]}")
        for m in user_moments:
            moments_lines.append(f"[\u7528\u6237\u53d1\u7684 {str(m.created_at)[:10]}] {m.content[:100]}")
        if moments_lines:
            moments_text = "\n".join(moments_lines)
    except Exception as e:
        _logger.warning("Failed to query moments: %s", e)
    return moments_text


register_section(ContextSection(
    key="moments",
    builder=moments_section,
    target=TARGET_TEMPLATE,
    slot="moments",
    quota_tokens=300,
    order=44,
))
