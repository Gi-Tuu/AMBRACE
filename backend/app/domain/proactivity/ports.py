"""outreach 投放口径三闸的 IO 端口（架构地图断点 #1 · V2b，2026-09-29）。

约定：domain 侧（app/domain/proactivity/pacing.py）只做纯判定（闸门阈值 / 灰度桶 / 半开区间），
唯一的外部依赖——读运行时开关 ``AGENT_FLAGS``——经本文件定义的协议由上层注入；
生产实现 = app/application/proactivity_pacing_ports.production_pacing_ports。

本文件是纯类型声明：零 IO、零业务模块 import（只有 typing）。
端口集刻意保持最小：pacing 真正调用的 IO 只有「取当前开关表」这一项
（计数 / 最近发送时间 / 活跃时段等 DB 查询仍在 scheduling/arbiter.py，不属于本端口）。
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol


class PacingPortsNotInjected(RuntimeError):
    """未注入 PacingPorts 且生产实现也绑定不上时抛出（不静默降级，问题当场可见）。"""


class PacingPorts(Protocol):
    """pacing.py 需要的最小端口集合（只暴露它真正调用的 IO）。"""

    def flags(self) -> Mapping[str, Any]:
        """当前运行时开关表（生产实现返回 AGENT_FLAGS 本身，不做拷贝、不加解释）。"""
        ...
