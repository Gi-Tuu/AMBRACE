"""状态情绪时间线 IO 端口（架构地图断点 #1 · domain 去 IO 铺开，2026-09-29）。

约定：domain 侧（app/domain/emotion/timeline.py）只保留解析、标签映射、排序与概览统计，
一切 IO（三张表的查询）经本文件定义的协议由上层注入；生产实现见
app/application/emotion_timeline_ports.py。

本文件是纯类型声明：零 IO、零业务模块 import（只有 dataclasses / datetime / typing）。
方法只有 3 个——正好覆盖 timeline.py 真正用到的三段查询，不多不少。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class TimelinePortsNotInjected(RuntimeError):
    """未注入 EmotionTimelinePorts 时抛出（不静默降级，问题当场可见）。"""


@dataclass(frozen=True)
class EmotionMemoryView:
    """情绪事件记忆（sub_type=emotion）行的只读快照；domain 只用 id / created_at / content。"""

    id: int
    created_at: datetime
    content: str = ""


@dataclass(frozen=True)
class StateTriggerLogView:
    """状态触发日志行的只读快照（含八维快照正文与恢复标志）。"""

    id: int
    created_at: datetime
    trigger_key: str = ""
    value: str = ""
    recovered: bool = False


@dataclass(frozen=True)
class StorylineEventView:
    """剧情线事件行的只读快照；正文优先级（output_text/user_context/trigger_source）由 domain 判定。"""

    id: int
    created_at: datetime
    storyline_key: str = ""
    node_index: int = 0
    output_text: str = ""
    user_context: str = ""
    trigger_source: str = ""


class EmotionTimelinePorts(Protocol):
    """timeline.py 需要的最小端口集合（三源各一个查询，均按 created_at 倒序、start 之后）。"""

    async def recent_emotion_memories(self, character_id: int,
                                      start: datetime) -> list[EmotionMemoryView]: ...

    async def recent_state_trigger_logs(self, character_id: int,
                                        start: datetime) -> list[StateTriggerLogView]: ...

    async def recent_storyline_events(self, character_id: int,
                                      start: datetime) -> list[StorylineEventView]: ...
