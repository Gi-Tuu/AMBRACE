"""AMBRACE 3.10 —— 情感渴望触发源（arbiter collect_motivation_events，等价迁入）。"""
from __future__ import annotations

from typing import Iterable

from app.domain.proactivity.decision import MOTIVATION_SPEAK_THRESHOLD
from app.utils.logger import get_logger

from .base import SourceContext, TriggerItem
from .registry import register_source

_logger = get_logger("scheduler.sources.motivation")


@register_source(name="motivation")
class MotivationSource:
    """情感渴望驱动的主动唤醒（priority=1）：渴望度 >= 阈值 → 主动搭话候选。

    仅提供候选，仍受 _execute 的最小间隔/连续不回复冷却/每日上限约束。
    """

    name = "motivation"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling import arbiter

        items: list[TriggerItem] = []
        try:
            for c in await arbiter.get_active_characters():
                cid = c.get("character_id")
                if not cid:
                    continue
                score = await arbiter._compute_motivation(cid)
                if score >= MOTIVATION_SPEAK_THRESHOLD:
                    # P0-4 修复（2026-08-16）：候选补 session_id（_execute proactive 分支直接索引），无会话跳过
                    from app.application.chat_service import get_latest_session_id
                    sid = await get_latest_session_id(c.get("user_id"), cid)
                    if not sid:
                        continue
                    c["session_id"] = sid
                    # P0-1（2026-08-24）：动机候选补最近聊天语境（复用节律类来源 get_last_messages，limit=5），
                    # 使 generate_proactive_event 不再因 motivation 通道语境为空而生成无法承接的消息；失败静默空串
                    context = ""
                    try:
                        from app.scheduling.triggers import get_last_messages
                        context = (await get_last_messages(sid, limit=5)) or ""
                    except Exception:
                        context = ""
                    c["last_context"] = context
                    items.append(TriggerItem(
                        type="motivation",
                        priority=1,
                        candidate=c,
                        motivation=round(score, 4),
                    ))
        except Exception as e:
            _logger.warning("collect_motivation_events failed: %s", e)
        return items

    def quota(self, ctx: SourceContext) -> int:
        return 1000
