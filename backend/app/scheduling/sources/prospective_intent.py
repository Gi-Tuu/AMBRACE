# -*- coding: utf-8 -*-
"""AMBRACE 3.10 —— 前瞻意图到期触发源（arbiter 事件源 prospective_intent，Ariadne 模块G，2026-09-04）。

真实逻辑在 app.scheduling.prospective_intent（collect_due_promises），本类仅作 TriggerSource 适配。
"""
from __future__ import annotations

from typing import Iterable

from .base import SourceContext, TriggerItem
from .registry import register_source


@register_source(name="prospective_intent")
class ProspectiveIntentSource:
    """到时间窗的 promise → 高优先级自然提起（一次性，兑现即焚）。"""

    name = "prospective_intent"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.agent.loop import AGENT_FLAGS
        # 触发总开关：关时不产生候选（写入开关独立，先攒数据后开触发）
        if not AGENT_FLAGS.get("prospective_intent_trigger", False):
            return []
        from app.scheduling.prospective_intent import collect_due_promises
        due = await collect_due_promises()
        items = []
        for c in due:
            # priority=1：高于随机想起（rhythm），低于生日/节日等 special；复用 arbiter 额度/免打扰
            items.append(TriggerItem(
                type="prospective_intent", priority=1, candidate=c,
            ))
        return items

    def quota(self, ctx: SourceContext) -> int:
        return 20
