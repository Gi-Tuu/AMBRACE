"""AMBRACE 3.10 —— 生日/节日/认识纪念日触发源（arbiter collect_special_events，等价迁入）。"""
from __future__ import annotations

from typing import Iterable

from .base import SourceContext, TriggerItem
from .registry import register_source


@register_source(name="special")
class SpecialSource:
    """生日 / 节日 / 认识纪念日（priority=3）；类型按候选来源区分 birthday/holiday/anniversary。"""

    name = "special"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.triggers import (
            get_birthday_candidates,
            get_holiday_candidates,
            get_anniversary_candidates,
        )

        items: list[TriggerItem] = []
        for c in await get_birthday_candidates():
            items.append(TriggerItem(type="birthday", priority=3, candidate=c))
        for c in await get_holiday_candidates():
            items.append(TriggerItem(type="holiday", priority=3, candidate=c))
        for c in await get_anniversary_candidates():
            items.append(TriggerItem(type="anniversary", priority=3, candidate=c))
        return items

    def quota(self, ctx: SourceContext) -> int:
        return 100
