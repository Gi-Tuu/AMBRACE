"""AMBRACE 3.10 —— 家庭群聊角色主动冒泡触发源（arbiter 事件源 group_active）。

原采集逻辑在 app.scheduling.group_active（collect_group_events），本类仅作 TriggerSource 适配。
"""
from __future__ import annotations

from typing import Iterable

from .base import SourceContext, TriggerItem
from .registry import register_source


@register_source(name="group_active")
class GroupActiveSource:
    """群内最近一段时间无 AI 消息时，概率性选一个角色主动冒泡 1 句（priority=1）。"""

    name = "group_active"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.group_active import collect_group_events

        return [TriggerItem.from_dict(d) for d in await collect_group_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100
