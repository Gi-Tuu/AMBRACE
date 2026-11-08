"""outreach 投放口径三闸 IO 端口的生产实现（架构地图断点 #1 · V2b，2026-09-29）。

原先写在 app/domain/proactivity/pacing.py ``flag_on`` 函数体里的取开关代码原样搬到此处：
``from app.agent.loop import AGENT_FLAGS`` 仍是**函数级惰性 import**（与旧版一致），
因此既有测试对 AGENT_FLAGS 的 setitem / setattr 打桩照旧生效，读到的仍是同一个 dict。

接线点（domain 不反向依赖本模块，必须由调用方显式注入；迁移期惰性绑定已于 2026-11-08 批 3 S5 删除）：
- app/scheduling/outreach_gates.py —— ``_pacing_gate`` 三闸判定（四处 gate_active 显式传 ports）
- app/scheduling/memory_review.py —— ``replyable_question_enabled``（显式传 ports；flags 已给出时端口不参与）
"""
from __future__ import annotations

from typing import Any, Mapping


class ProductionPacingPorts:
    """PacingPorts 生产实现（原 pacing.flag_on 里的 AGENT_FLAGS 取用，行为不变）。"""

    def flags(self) -> Mapping[str, Any]:
        from app.agent.loop import AGENT_FLAGS
        return AGENT_FLAGS


production_pacing_ports = ProductionPacingPorts()
