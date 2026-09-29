"""状态情绪时间线 IO 端口的生产实现（架构地图断点 #1 · domain 去 IO 铺开，2026-09-29）。

原先写在 app/domain/emotion/timeline.py 函数体里的三段查询原样搬到此处：select 语句、where
条件（含 #70-C 的 _active_status_clause 惰性 import）、order_by 口径逐字保留，只多做一步
「ORM 实体 → 只读快照」的字段投影。每段仍各自开一个会话（与改动前一模一样：三源原本就是
三个独立的 `async with async_session_factory()`，无跨源事务）。

接线点（domain 不反向依赖本模块，由上层注入）：
- app/application/characters.py —— get_emotion_timeline（API：角色状态情绪记忆时间线）
"""
from datetime import datetime

from sqlalchemy import select

from app.db.database import async_session_factory
from app.domain.emotion.timeline_ports import (
    EmotionMemoryView,
    StateTriggerLogView,
    StorylineEventView,
)
from app.models.character import StateTriggerLog, StorylineEvent
from app.models.memory import Memory


class ProductionEmotionTimelinePorts:
    """EmotionTimelinePorts 生产实现（原 timeline.py 的三源查询，行为不变）。"""

    async def recent_emotion_memories(self, character_id: int,
                                      start: datetime) -> list[EmotionMemoryView]:
        from app.memory.service import _active_status_clause  # #70-C：仅 active（flag 关=永真）
        async with async_session_factory() as db:
            mems = (await db.execute(
                select(Memory).where(
                    Memory.character_id == character_id,
                    Memory.sub_type == "emotion",
                    Memory.is_archived == False,
                    Memory.created_at >= start,
                    _active_status_clause(),
                ).order_by(Memory.created_at.desc())
            )).scalars().all()
        return [EmotionMemoryView(id=m.id, created_at=m.created_at, content=m.content or "")
                for m in mems]

    async def recent_state_trigger_logs(self, character_id: int,
                                        start: datetime) -> list[StateTriggerLogView]:
        async with async_session_factory() as db:
            logs = (await db.execute(
                select(StateTriggerLog).where(
                    StateTriggerLog.character_id == character_id,
                    StateTriggerLog.created_at >= start,
                ).order_by(StateTriggerLog.created_at.desc())
            )).scalars().all()
        return [StateTriggerLogView(id=lg.id, created_at=lg.created_at,
                                    trigger_key=lg.trigger_key, value=lg.value or "",
                                    recovered=bool(lg.recovered)) for lg in logs]

    async def recent_storyline_events(self, character_id: int,
                                      start: datetime) -> list[StorylineEventView]:
        async with async_session_factory() as db:
            st_events = (await db.execute(
                select(StorylineEvent).where(
                    StorylineEvent.character_id == character_id,
                    StorylineEvent.created_at >= start,
                ).order_by(StorylineEvent.created_at.desc())
            )).scalars().all()
        return [StorylineEventView(id=se.id, created_at=se.created_at,
                                   storyline_key=se.storyline_key or "",
                                   node_index=se.node_index or 0,
                                   output_text=se.output_text or "",
                                   user_context=se.user_context or "",
                                   trigger_source=se.trigger_source or "") for se in st_events]


production_emotion_timeline_ports = ProductionEmotionTimelinePorts()
