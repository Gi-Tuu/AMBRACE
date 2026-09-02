"""AMBRACE 3.10 —— 随机节律触发源（arbiter collect_rhythm_events，原逻辑整体迁入，等价）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from app.utils.logger import get_logger

from .base import SourceContext, TriggerItem
from .registry import register_source

_logger = get_logger("scheduler.sources.rhythm")


@register_source(name="rhythm")
class RhythmSource:
    """随机节律采样：时间窗 + 概率 + 每日上限 + 计时器/剧情线互斥（priority=1）。"""

    name = "rhythm"

    async def collect(self, ctx: SourceContext) -> Iterable[TriggerItem]:
        from app.scheduling import arbiter
        from app.scheduling.life_rhythm import get_time_window, sample_should_trigger, pick_behavior
        from app.scheduling.triggers import get_daily_count, get_latest_session, get_last_messages

        window = get_time_window()
        if window is None:
            return []
        items: list[TriggerItem] = []
        for char_info in await arbiter.get_active_characters():
            try:
                char_id = char_info["character_id"]
                # 有 pending 计时器 → 跳过随机节律（AI 正在"洗澡/睡觉"）
                if await arbiter.has_pending_timer(char_id):
                    continue
                # 有未发完的剧情切片 → 跳过随机节律，避免剧情重叠
                if await arbiter.has_pending_storyline(char_id):
                    continue
                # 每日上限
                if await get_daily_count(char_id) >= char_info["max_daily_proactive"]:
                    continue
                # 概率采样
                if not sample_should_trigger(char_info["frequency"], window):
                    continue

                # 会话
                session = await get_latest_session(char_id, char_info["user_id"])
                if not session:
                    continue
                context = await get_last_messages(session["id"])

                # 闲置时长（分钟）—— 告知 AI 上次聊天已过去多久
                # P-fix（2026-08-31）：idle 基准改用会话最后一条消息 created_at（naive UTC），
                # 不用 session.updated_at —— SSE 流式路径落用户/AI 消息时不更新该字段，会虚高闲置。
                last_active = await arbiter._session_last_message_at(session["id"])
                if last_active is None:
                    last_active = session["updated_at"]
                if last_active.tzinfo is not None:
                    last_active = last_active.replace(tzinfo=None)
                idle_minutes = max(0, int((datetime.now(timezone.utc).replace(tzinfo=None) - last_active).total_seconds() / 60))

                behavior = pick_behavior(window)
                items.append(TriggerItem(
                    type=behavior,
                    priority=1,
                    candidate={
                        **char_info,
                        "session_id": session["id"],
                        "window": window,
                        "behavior": behavior,
                        "last_context": context,
                        "idle_minutes": idle_minutes,
                    },
                ))
            except Exception as e:
                _logger.warning("rhythm sample error char=%d: %s", char_info["character_id"], e)
        return items

    def quota(self, ctx: SourceContext) -> int:
        return 100
