"""AMBRACE 3.10 —— 生日/节日/认识纪念日触发源（arbiter collect_special_events，等价迁入）。

X6（2026-09-16）：flag ``proactive_strategy_plugins`` 开 **且** 有已启用策略包声明接管
``special`` 类别时，本源整体**让位**（返回空），由策略包独占地产出同类候选——防内核与
策略包各发一条。flag 关 / 无包接管 → 行为与迁移前逐字节一致。
"""
from __future__ import annotations

from typing import Iterable

from app.utils.logger import get_logger

from .base import SourceContext, TriggerItem
from .registry import register_source

_logger = get_logger("scheduler.sources.special")

_yield_logged = False  # 让位只在首次记一条 info，避免每 tick 刷日志


def _yielded_to_strategy_pack() -> bool:
    """本源是否应让位给策略包（异常→False，即不让位、回退旧行为）。"""
    try:
        from .strategy import category_yielded

        return category_yielded("special")
    except Exception as e:
        _logger.warning("strategy yield check failed: %s", e)
        return False


@register_source(name="special")
class SpecialSource:
    """生日 / 节日 / 认识纪念日（priority=3）；类型按候选来源区分 birthday/holiday/anniversary。"""

    name = "special"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        global _yield_logged
        if _yielded_to_strategy_pack():
            if not _yield_logged:
                _yield_logged = True
                _logger.info("special source yielded: 由 proactive_strategy 策略包接管（防双发）")
            return []

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
