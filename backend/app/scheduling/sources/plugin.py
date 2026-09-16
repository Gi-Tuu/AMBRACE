"""AMBRACE 3.10 —— 插件主动候选触发源（arbiter collect_plugin_events，等价迁入）。

X6（2026-09-16）：flag ``proactive_strategy_plugins`` 开且有策略包接管类别时，本源额外做两件事
（flag 关 / 无包接管时全部跳过，与迁移前逐字节一致）：

1. **下发 roster**：把内核「选人」结果经 ``ctx`` 传给 proactive_candidate hook
   （``{"strategy_categories": [...], "roster": [...]}``），策略包只做内容判定；
2. **内核去重**：策略候选按 ``(character_id, message_type, 北京日界)`` 去重
   —— 策略包无状态、每 tick 都投同样的候选，靠这里保证同一触发日只发一次。
"""
from __future__ import annotations

from typing import Iterable

from app.utils.logger import get_logger

from .base import SourceContext, TriggerItem
from .registry import register_source

_logger = get_logger("scheduler.sources.plugin")


@register_source(name="plugin")
class PluginSource:
    """插件主动消息候选（proactive_candidate hook，priority=1；日限额由插件内部维护）。"""

    name = "plugin"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        hook_ctx: dict = {}
        claims: set[str] = set()
        try:
            from .strategy import build_hook_ctx, build_roster, claimed_categories, strategy_enabled

            if strategy_enabled():
                claims = claimed_categories()
                if claims:
                    hook_ctx = build_hook_ctx(claims, await build_roster())
        except Exception as e:
            _logger.warning("strategy ctx build failed: %s", e)
            claims = set()

        try:
            from app.plugins.registry import run_hook_collect
            results = await run_hook_collect("proactive_candidate", hook_ctx)
        except Exception as e:
            _logger.warning("collect_plugin_events failed: %s", e)
            return []
        items: list[TriggerItem] = []
        for r in results:
            cand = r.get("result")
            # 支持插件一次返回多个候选（list[dict]，如渠道评论回复 + 主动提及；2026-08-10 社交交互层 v2）
            if isinstance(cand, list):
                for c in cand:
                    if not isinstance(c, dict):
                        continue
                    if not c.get("character_id") or not c.get("user_id"):
                        continue
                    if not await self._keep(c, claims):
                        continue
                    items.append(TriggerItem(
                        type="plugin", priority=1,
                        candidate={**c, "plugin": r.get("plugin", "")},
                    ))
                continue
            if not isinstance(cand, dict):
                continue
            cid = cand.get("character_id")
            uid = cand.get("user_id")
            if not cid or not uid:
                continue
            if not await self._keep(cand, claims):
                continue
            items.append(TriggerItem(
                type="plugin", priority=1,
                candidate={**cand, "plugin": r.get("plugin", "")},
            ))
        return items

    async def _keep(self, cand: dict, claims: set[str]) -> bool:
        """策略候选的内核侧去重（非策略候选 / 无接管类别 → 恒 True，零变化）。"""
        if not claims:
            return True
        try:
            from .strategy import category_of, message_type_of, sent_today

            if category_of(cand) not in claims:
                return True
            mt = message_type_of(cand)
            if not mt:
                return True
            return not await sent_today(int(cand["character_id"]), mt)
        except Exception as e:
            _logger.warning("strategy dedup failed: %s", e)
            return True

    def quota(self, ctx: SourceContext) -> int:
        return 1000
