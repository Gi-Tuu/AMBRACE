"""AMBRACE 3.10 —— 插件主动候选触发源（arbiter collect_plugin_events，等价迁入）。

X6（2026-09-16）：flag ``proactive_strategy_plugins`` 开且有策略包接管类别时，本源额外做三件事
（flag 关 / 无包接管时全部跳过，与迁移前逐字节一致）：

1. **下发 roster**：把内核「选人」结果经 ``ctx`` 传给 proactive_candidate hook
   （``{"strategy_categories": [...], "roster": [...]}``），策略包只做内容判定；
2. **内核去重**：策略候选按 ``(character_id, message_type, 北京日界)`` 去重
   —— 策略包无状态、每 tick 都投同样的候选，靠这里保证同一触发日只发一次；
3. **内核执行路由**（X6-b）：策略候选按类别落回内核既定执行链——
   ``exec_type_of`` 决定 arbiter 事件类型（如 memory_review→run_memory_review、
   rhythm→剧情线），``prepare_candidate`` 做内核保留的频控闸与素材装配；
   未登记类别（第三方策略包）仍走 hint 生成路径（type="plugin"，逐字节旧行为）。
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
                    item = await self._build(c, claims, r.get("plugin", ""))
                    if item is not None:
                        items.append(item)
                continue
            if not isinstance(cand, dict):
                continue
            cid = cand.get("character_id")
            uid = cand.get("user_id")
            if not cid or not uid:
                continue
            item = await self._build(cand, claims, r.get("plugin", ""))
            if item is not None:
                items.append(item)
        return items

    async def _build(self, cand: dict, claims: set[str], plugin: str) -> TriggerItem | None:
        """去重 → 内核执行前处理（频控闸 + 素材装配）→ 定事件类型。None = 丢弃。"""
        if not await self._keep(cand, claims):
            return None
        try:
            from .strategy import exec_type_of, prepare_candidate

            prepared = await prepare_candidate(cand)
        except Exception as e:
            _logger.warning("strategy candidate prepare failed: %s", e)
            return None
        if prepared is None:      # 内核闸未通过（如节律日上限/有未发完剧情）→ 不发
            return None
        return TriggerItem(
            type=exec_type_of(prepared), priority=1,
            candidate={**prepared, "plugin": plugin},
        )

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
