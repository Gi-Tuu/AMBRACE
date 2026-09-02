"""AMBRACE 3.10 —— 宠物相关触发源（宠物提醒 / AI 照顾宠物 / AI 自主领养 / AI 宠物来访）。

原采集逻辑在 app.scheduling.pet_care（collect_pet_events / collect_ai_care_events /
collect_ai_adopt_events / collect_pet_visit_events），本类仅作 TriggerSource 适配。
"""
from __future__ import annotations

from typing import Iterable

from .base import SourceContext, TriggerItem
from .registry import register_source


@register_source(name="pet_remind")
class PetRemindSource:
    """宠物关怀：宠物饿了/脏了 → 角色主动提醒（每角色每日<=2、间隔在 pet_care 内部处理，priority=1）。"""

    name = "pet_remind"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.pet_care import collect_pet_events

        return [TriggerItem.from_dict(d) for d in await collect_pet_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100


@register_source(name="ai_care")
class AiCareSource:
    """AI 照顾自己的宠物：属性/活动/记忆 + 照顾消息（独立限额 <=1 在 pet_care 内部，priority=1）。"""

    name = "ai_care"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.pet_care import collect_ai_care_events

        return [TriggerItem.from_dict(d) for d in await collect_ai_care_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100


@register_source(name="ai_adopt")
class AiAdoptSource:
    """AI 自主领养：创建 AI 宠物 + 告知消息（限额/概率在 pet_care 内部，priority=1）。"""

    name = "ai_adopt"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.pet_care import collect_ai_adopt_events

        return [TriggerItem.from_dict(d) for d in await collect_ai_adopt_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100


@register_source(name="pet_visit")
class PetVisitSource:
    """AI 宠物来访：后台行为（只写互动记录+记忆，不推送消息，priority=1）。"""

    name = "pet_visit"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.pet_care import collect_pet_visit_events

        return [TriggerItem.from_dict(d) for d in await collect_pet_visit_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100
