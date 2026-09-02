"""AMBRACE 3.10 —— AI 间私聊触发源（arbiter 事件源 ai_social）。

原采集逻辑在 app.scheduling.ai_social（collect_ai_social_events），本类仅作 TriggerSource 适配。
"""
from __future__ import annotations

from typing import Iterable

from .base import SourceContext, TriggerItem
from .registry import register_source


@register_source(name="ai_social")
class AiSocialSource:
    """AI 间私聊：同用户活跃角色两两配对（不同名、非敌对）→ 配对候选（priority=1）。"""

    name = "ai_social"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.ai_social import collect_ai_social_events

        return [TriggerItem.from_dict(d) for d in await collect_ai_social_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100
