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
            # A2 M4：本调用点是「全租户广播」（SourceContext 无固定 caller，roster 可含多家庭账号），
            # 故只传调用点标识、不伪造 caller → flag plugin_runtime_scope 开时 fail-closed 到内置插件
            # （内置策略包照常分发；第三方非内置策略包需等「按租户 roster 扇出」另行落地）。
            results = await run_hook_collect(
                "proactive_candidate", hook_ctx,
                callsite="scheduling/sources/plugin.py:proactive_candidate",
            )
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
        if not await self._pairing_ok(cand, plugin):
            return None
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

    async def _pairing_ok(self, cand: dict, plugin: str) -> bool:
        """A2 M4（§4，flag ``plugin_runtime_scope`` 门控）：候选自报 user_id 与角色归属配对校验。

        插件自报一个别的账号的 user_id，就能让内核按那个账号裁决（跨租户越权）——这里用
        ``strategy._user_id_of`` 反查角色归属（该角色最新会话的 user_id）与自报值比对，
        不符 / 反查不到 / 反查异常 → 丢弃候选并告警（**fail-closed**）。
        flag 关 → 恒 True（逐字节旧行为）。
        """
        try:
            from app.plugins.registry import plugin_runtime_scope_enabled
            if not plugin_runtime_scope_enabled():
                return True
        except Exception:
            return True
        try:
            cid = int(cand.get("character_id") or 0)
            uid = int(cand.get("user_id") or 0)
        except (TypeError, ValueError):
            return False
        if not cid or not uid:
            return False
        try:
            from .strategy import _user_id_of
            owner = await _user_id_of(cid)
        except Exception as e:
            _logger.warning("plugin candidate pairing lookup failed plugin=%s char=%s: %s", plugin, cid, e)
            return False
        if owner is None or int(owner) != uid:
            _logger.warning(
                "plugin proactive candidate dropped: user_id 与角色归属不符 plugin=%s char=%s claimed=%s owner=%s",
                plugin, cid, uid, owner,
            )
            return False
        return True

    async def _keep(self, cand: dict, claims: set[str]) -> bool:
        """策略候选的内核侧去重（非策略候选 / 无接管类别 → 恒 True，零变化）。

        去重口径**按类别登记**（``strategy.CATEGORY_DEDUP``）：想念/节律落库是
        ``storyline``，按 proactive_message_logs 查 message_type 会空转，故走触发日志。
        """
        if not claims:
            return True
        try:
            from .strategy import category_of, message_type_of, sent_recently

            cat = category_of(cand)
            if cat not in claims:
                return True
            mt = message_type_of(cand)
            if not mt:
                return True
            return not await sent_recently(int(cand["character_id"]), cat or "", mt)
        except Exception as e:
            _logger.warning("strategy dedup failed: %s", e)
            return True

    def quota(self, ctx: SourceContext) -> int:
        return 1000
