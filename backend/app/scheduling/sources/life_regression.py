"""AMBRACE 3.10 —— 生活回归摘要触发源（arbiter 事件源 life_regression）。

原采集逻辑在 app.scheduling.life_regression（collect_life_regression_events），本类仅作 TriggerSource 适配。
"""
from __future__ import annotations

from typing import Iterable

from .base import SourceContext, TriggerItem
from .registry import register_source


@register_source(name="life_regression")
class LifeRegressionSource:
    """近 24h 生活记忆 → 回归摘要候选（priority=2，每角色每日 <=1 次在 collect 内处理）。"""

    name = "life_regression"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.life_regression import collect_life_regression_events

        return [TriggerItem.from_dict(d) for d in await collect_life_regression_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100
