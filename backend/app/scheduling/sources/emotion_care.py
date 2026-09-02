"""AMBRACE 3.10 —— AI 情绪关怀触发源。

原采集逻辑在 app.domain.emotion.care（collect_care_events），本类仅作 TriggerSource 适配。
"""
from __future__ import annotations

from typing import Iterable

from .base import SourceContext, TriggerItem
from .registry import register_source


@register_source(name="emotion_care")
class CareSource:
    """AI 情绪关怀（priority=1）：用户低落 → 角色延迟主动关心（限额/免打扰在 emotion_care 内部处理）。"""

    name = "emotion_care"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.domain.emotion.care import collect_care_events

        return [TriggerItem.from_dict(d) for d in await collect_care_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100
