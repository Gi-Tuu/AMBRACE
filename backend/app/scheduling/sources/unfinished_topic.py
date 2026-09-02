"""AMBRACE 3.10 —— 对话未收尾跟进触发源（arbiter 事件源 unfinished_topic）。

原采集逻辑在 app.scheduling.unfinished_topic（collect_unfinished_events），本类仅作 TriggerSource 适配。
"""
from __future__ import annotations

from typing import Iterable

from .base import SourceContext, TriggerItem
from .registry import register_source


@register_source(name="unfinished_topic")
class UnfinishedTopicSource:
    """对话未收尾跟进：用户抛了话头（下次/改天/有空）→ 自然捡起话题（每日 1 次/角色，collect 内去重，priority=1）。"""

    name = "unfinished_topic"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.unfinished_topic import collect_unfinished_events

        return [TriggerItem.from_dict(d) for d in await collect_unfinished_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100
