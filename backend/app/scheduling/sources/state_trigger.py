"""AMBRACE 3.10 —— 状态触发兜底源（arbiter collect_state_trigger_events，等价迁入）。"""
from __future__ import annotations

from typing import Iterable

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.character import AICharacter, ProactiveSettings

from .base import SourceContext, TriggerItem
from .registry import register_source


@register_source(name="state_trigger")
class StateTriggerSource:
    """状态触发兜底（priority=2）：八维状态达阈值 → 主动消息/朋友圈。

    聊天后实时触发（multiplier=0.5）+ tick 兜底（multiplier=1.0）双通道；
    防抖/冷却在 state_trigger_logs，概率门控/免打扰在 state_triggers.check_state_triggers。
    """

    name = "state_trigger"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        async with async_session_factory() as db:
            result = await db.execute(
                select(ProactiveSettings.character_id, AICharacter.user_id)
                .join(AICharacter, AICharacter.id == ProactiveSettings.character_id)
                .where(
                    ProactiveSettings.state_trigger_enabled == True,
                    AICharacter.is_active == True,
                )
            )
            rows = result.all()
        return [
            TriggerItem(
                type="state_trigger", priority=2,
                candidate={"character_id": cid, "user_id": uid},
            )
            for cid, uid in rows if uid
        ]

    def quota(self, ctx: SourceContext) -> int:
        return 1000
