"""AMBRACE 3.10 —— 主动到期/情境复习触发源。

原采集逻辑在 app.scheduling.memory_review（collect_review_events / collect_contextual_events），
本类仅作 TriggerSource 适配（采集逻辑保持原模块、逐字节等价），保证统一接口。

X6-b（2026-09-17）：flag ``proactive_strategy_plugins`` 开 **且** 有已启用策略包接管
``memory_review`` 类别时，本源整体**让位**——「复习哪条记忆」交给策略包
（它经 ``sdk.get_proactive_context(["due_reviews"])`` 取到期条目）。

**执行仍归内核**：策略候选的 message_type 沿用既定口径 ``memory_review``，arbiter 仍走
``run_memory_review``——日上限 / 抽检间隔 / 时态闸门 / 复习成功判定全部不变，策略包绕不开。
flag 关 / 无包接管 → 行为与 X6-b 前逐字节一致。
"""
from __future__ import annotations

from typing import Iterable

from app.utils.logger import get_logger

from .base import SourceContext, TriggerItem
from .registry import register_source

_logger = get_logger("scheduler.sources.memory_review")

_yield_logged = False  # 让位只在首次记一条 info，避免每 tick 刷日志


def _yielded_to_strategy_pack() -> bool:
    """本源是否应让位给策略包（异常→False，即不让位、回退旧行为）。"""
    try:
        from .strategy import category_yielded

        return category_yielded("memory_review")
    except Exception as e:
        _logger.warning("strategy yield check failed: %s", e)
        return False


@register_source(name="memory_review")
class MemoryReviewSource:
    """主动到期复习（priority=1）：扫描 next_review_at 到期且 importance>=40 的记忆（每角色 1 条候选）。"""

    name = "memory_review"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        global _yield_logged
        if _yielded_to_strategy_pack():
            if not _yield_logged:
                _yield_logged = True
                _logger.info("memory_review source yielded: 由 proactive_strategy 策略包接管（防双发）")
            return []

        from app.scheduling.memory_review import collect_review_events

        return [TriggerItem.from_dict(d) for d in await collect_review_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100


@register_source(name="memory_review_contextual")
class ContextualReviewSource:
    """情境驱动复习（priority=2，与状态触发同级）：感知 deep/emotion 或命中进行中目标 → 自然提及。"""

    name = "memory_review_contextual"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling.memory_review import collect_contextual_events

        return [TriggerItem.from_dict(d) for d in await collect_contextual_events()]

    def quota(self, ctx: SourceContext) -> int:
        return 100
