"""AMBRACE 3.10 —— arbiter 触发源基础类型。

一个 arbiter 事件源 = 一个 TriggerSource 实现类，经 registry.register_source 注册。
TriggerItem 承载 arbiter 现有「候选事件 dict」所需的全部字段，保证与重构前逐字节等价。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable


@dataclass
class SourceContext:
    """传给 TriggerSource.collect / quota 的运行时上下文。

    当前 arbiter 各事件源自行读库、自行取时（与重构前逐字节等价），本对象保持空壳占位；
    后续若需在 run_tick 统一注入时间/用户等，可在本对象上扩展，供各源读取，不影响等价性。
    """


@dataclass
class TriggerItem:
    """触发源产出的候选事件。

    字段与 arbiter 现有候选 dict 完全一致：
      {type, priority, event | candidate, motivation?}
    - event：仅 timer 等携带 ORM 对象的源使用（recover_on_startup/执行分支直接索引）；
    - candidate：其余源统一携带候选字典；
    - motivation：仅 motivation 源采集时自带（0-1 渴望度）；其余源采集时为 None
      （run_tick 汇总阶段再统一按「该角色渴望度」补齐）。
    """

    type: str
    priority: int
    candidate: dict | None = None
    event: Any | None = None
    motivation: float | None = None

    def to_dict(self) -> dict:
        """还原为 arbiter 合并/排序/_execute 所用的候选 dict（与旧采集函数输出逐字节等价）。"""
        d = {"type": self.type, "priority": self.priority}
        if self.event is not None:
            d["event"] = self.event
        if self.candidate is not None:
            d["candidate"] = self.candidate
        if self.motivation is not None:
            d["motivation"] = self.motivation
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TriggerItem":
        """从 arbiter 现有候选 dict 还原为 TriggerItem（供包装既有采集函数时使用）。"""
        return cls(
            type=d["type"],
            priority=d["priority"],
            candidate=d.get("candidate"),
            event=d.get("event"),
            motivation=d.get("motivation"),
        )


def to_item_dict(item: "TriggerItem | dict") -> dict:
    """把 collect() 返回的元素归一化为候选 dict（兼容 TriggerItem 或原生 dict）。"""
    if isinstance(item, TriggerItem):
        return item.to_dict()
    return item


@runtime_checkable
class TriggerSource(Protocol):
    """arbiter 触发源契约。

    - name：源标识（registry 键）；
    - collect(ctx)：产出候选事件（Iterable[TriggerItem]）；
    - quota(ctx)：该源单次 tick 的候选配额。当前各源「每源限额」内嵌在 collect() 内
      （与原实现一致），arbiter 汇总层不据此裁剪候选（保持逐字节等价）；本方法仅作
      registry/观测用，返回该源典型的单次 tick 候选上限。
    """

    name: str

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        ...

    def quota(self, ctx: SourceContext) -> int:
        ...
