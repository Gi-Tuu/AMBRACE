"""关系标量衰减 IO 端口（架构地图断点 #1 · domain 去 IO 铺开，2026-09-29）。

约定：domain 侧（app/domain/relationship/decay.py）只保留「闲置天数 → 衰减步长」的判定与下限
钳制，一切 IO（character_states 的读、trust/attachment 的写）经本文件定义的协议由上层注入；
生产实现见 app/application/relationship_decay_ports.py。

本文件是纯类型声明：零 IO、零业务模块 import（只有 dataclasses / datetime / typing）。
方法只有 2 个——正好覆盖 decay.py 真正用到的一读一写，不多不少。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class DecayPortsNotInjected(RuntimeError):
    """未注入 RelationshipDecayPorts 时抛出（不静默降级，问题当场可见）。"""


@dataclass(frozen=True)
class CharacterStateView:
    """character_states 行的只读快照：domain 只用这四列，不跨层传 ORM 实体。

    trust / attachment 保留 None（旧行可能为 NULL，判定按 `or 50` 兜底，语义与改动前一致）。
    """

    id: int
    last_activity_at: datetime | None = None
    trust: int | None = None
    attachment: int | None = None


@dataclass(frozen=True)
class StateDecayUpdate:
    """一行待写入的衰减结果；None = 该列不改（与旧实现「只赋值变化的列」一致）。"""

    state_id: int
    trust: int | None = None
    attachment: int | None = None


class RelationshipDecayPorts(Protocol):
    """decay.py 需要的最小端口集合（全量读状态 + 一批写回，写回在同一会话内一次提交）。"""

    async def fetch_character_states(self) -> list[CharacterStateView]: ...

    async def apply_decay(self, updates: list[StateDecayUpdate]) -> None: ...
