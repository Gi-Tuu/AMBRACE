# -*- coding: utf-8 -*-
"""decision 域（阶段 0，2026-09-25）：规则与大模型之间的「决策中间层」薄抽象。

边界：本包只负责**统一决策口径 + 影子留痕**，不负责换实现——阶段 0 三原语一律透传各决策点
原有的算法（``legacy``），输出逐字不变；是否采用别的后端（决策模型 / 本地 logits）属阶段 1+，
先决条件是把影子数据攒够并做校准（见 docs/decision-layer-research.md §8.6、§10）。

门面只导出稳定接口；观测出口复用 infra 既有 ``agent_task_logs`` 写入通道（app/agent/trace.py）。
"""
from app.domain.decision.layer import (  # noqa: F401
    FLAG_KEY,
    SHADOW_ROUTE,
    SHADOW_TRIGGER,
    SINK_BUFFER,
    SINK_DIRECT,
    ask_choice,
    ask_noul,
    ask_score,
    flush_shadow_buffer,
    observe_tense_decision,
    reset_shadow_state,
    shadow_buffer_size,
    shadow_dropped_total,
    shadow_enabled,
)

__all__ = [
    "FLAG_KEY", "SHADOW_ROUTE", "SHADOW_TRIGGER", "SINK_DIRECT", "SINK_BUFFER",
    "ask_noul", "ask_choice", "ask_score",
    "observe_tense_decision", "flush_shadow_buffer", "shadow_enabled",
    "shadow_buffer_size", "shadow_dropped_total", "reset_shadow_state",
]
