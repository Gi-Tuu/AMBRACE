"""AMBRACE 3.10 —— 主动到期/情境复习触发源。

原采集逻辑在 app.scheduling.memory_review（collect_review_events / collect_contextual_events），
本类仅作 TriggerSource 适配（采集逻辑保持原模块、逐字节等价），保证统一接口。
"""
from __future__ import annotations

from typing import Iterable

from .base import SourceContext, TriggerItem
from .registry import register_source


@register_source(name="memory_review")
class MemoryReviewSource:
    """主动到期复习（priority=1）：扫描 next_review_at 到期且 importance>=40 的记忆（每角色 1 条候选）。"""

    name = "memory_review"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.memory_review import collect_review_events

        return [TriggerItem.from_dict(d) for d in await collect_review_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100


@register_source(name="memory_review_contextual")
class ContextualReviewSource:
    """情境驱动复习（priority=2，与状态触发同级）：感知 deep/emotion 或命中进行中目标 → 自然提及。"""

    name = "memory_review_contextual"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.memory_review import collect_contextual_events

        return [TriggerItem.from_dict(d) for d in await collect_contextual_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100
