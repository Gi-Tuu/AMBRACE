"""AMBRACE 3.10 —— 插件主动候选触发源（arbiter collect_plugin_events，等价迁入）。"""
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
        try:
            from app.plugins.registry import run_hook_collect
            results = await run_hook_collect("proactive_candidate", {})
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
            items.append(TriggerItem(
                type="plugin", priority=1,
                candidate={**cand, "plugin": r.get("plugin", "")},
            ))
        return items

    def quota(self, ctx: SourceContext) -> int:
        return 1000
