"""编排端口（F2-b）：cognition（在线）与 proactivity（离线）的抽象契约。

scheduling 触发器与跨域调用只依赖端口，不 import 引擎内部（跨层 import 收敛为 1-2 处）。
实现侧：domain/cognition.runtime（在线）、domain/proactivity（离线，逐步落地）。
"""
from typing import Protocol


class CognitionPort(Protocol):
    """在线认知：一次用户事件 → 一次回复（同步低延迟）。"""

    async def run_online(self, event: dict) -> dict: ...


class ProactivityPort(Protocol):
    """离线主动：一次 tick 扫描 → 产出一组待执行动作（异步批量）。"""

    async def run_tick(self, trigger: dict) -> list[dict]: ...
