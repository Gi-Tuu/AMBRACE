"""决策层影子留痕的 IO 端口（架构地图断点 #1 · domain 去 IO 铺开 V2b，2026-09-29）。

约定：domain 侧（app/domain/decision/layer.py）只做「透传 legacy + 组装影子记录」，
一切外部依赖（运行时开关表 / 时钟 / trace 写入通道 / 后台任务调度 / agent_task_logs 落库）
经本文件定义的协议由上层注入；生产实现 =
app/application/decision_layer_ports.production_decision_layer_ports。

本文件是纯类型声明：零 IO、零业务模块 import（只有 typing）。
端口集刻意保持最小：只暴露 layer.py 真正调用的 8 项，计数/查询语义一律不在此层。
"""
from __future__ import annotations

from typing import Any, Coroutine, Mapping, Protocol


class ShadowPortsNotInjected(RuntimeError):
    """未注入 DecisionShadowPorts 且生产实现也绑定不上时抛出（不静默降级，问题当场可见）。"""


ShadowRow = Mapping[str, Any]


class DecisionShadowPorts(Protocol):
    """layer.py 需要的最小端口集合（取开关 / 取时钟 / 写一行 / 交后台任务 / 批量落库）。"""

    # ── 开关与时钟 ──
    def flags(self) -> Mapping[str, Any]:
        """当前运行时开关表（生产实现返回 AGENT_FLAGS 本身，不拷贝、不解释）。"""
        ...

    def perf_counter(self) -> float:
        """决策耗时计时源（生产实现 = time.perf_counter）。"""
        ...

    def monotonic(self) -> float:
        """缓冲年龄计时源（生产实现 = time.monotonic）。"""
        ...

    # ── 单行 fire-and-forget 通道（挂点 A·direct） ──
    def new_task_id(self) -> str:
        """一次决策的短 id（生产实现 = app.agent.trace.new_task_id）。"""
        ...

    def enqueue_task_log(self, **row: Any) -> None:
        """交出一行影子记录，不 await（生产实现 = app.agent.trace.enqueue_task_log）。"""
        ...

    # ── 批量落库通道（挂点 B·buffer） ──
    def spawn_background(self, coro: Coroutine[Any, Any, Any], *,
                        name: str | None = None) -> Any:
        """把整批写库协程交给后台任务（生产实现 = app.utils.async_tasks.spawn_background）。"""
        ...

    async def resolve_owner_user_id(self, character_id: int) -> int | None:
        """按角色解析归属 user_id（生产实现 = app.agent.trace.resolve_owner_user_id，失败返回 None）。"""
        ...

    async def write_shadow_rows(self, rows: list[ShadowRow]) -> None:
        """一次会话、一次 commit 写完整批（每行即 AgentTaskLog 构造参数）。"""
        ...
