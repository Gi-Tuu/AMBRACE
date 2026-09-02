"""AMBRACE 3.10 —— 定时承诺触发源（arbiter collect_timer_events，原逻辑整体迁入，等价）。"""
from __future__ import annotations

from typing import Iterable

from .base import SourceContext, TriggerItem
from .registry import register_source


@register_source(name="timer")
class TimerSource:
    """到期定时承诺（最高优先级，priority=4）。"""

    name = "timer"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.promise_service import get_due_events

        events = await get_due_events()
        return [TriggerItem(type="timer", priority=4, event=e) for e in events]

    def quota(self, ctx: SourceContext) -> int:
        return 100
