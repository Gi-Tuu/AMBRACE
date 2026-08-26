"""聊天→生活意图提取（设计稿 §10）。零 LLM，本地规则，与 topic_tracker 同构。

在 chat_service._run_post_processing 中异步调用，不阻塞回复。
提取结果写入 life_chat_intents 缓冲表，Life Loop 下拉消费。
修正 2026-08-26：priority>=3（this_turn）写库后立即触发该角色一个 Life Loop 回合，不等 30min tick。
"""
from __future__ import annotations
import re
from datetime import datetime, timezone
from sqlalchemy import select
from app.db.database import async_session_factory
from app.models.life import LifeChatIntent  # 需在 models/life/life.py 加
from app.utils.logger import get_logger

_logger = get_logger("life.chat_intent")

THROTTLE_SECONDS = 300  # 同角色 5 分钟最多提取 1 次
_throttle: dict[int, float] = {}

# (action_type, horizon, pattern)
_PATTERNS = [
    ("go_out", "this_week", re.compile(
        r"(?:想去|要去|打算去|准备去|去一趟)([^，。！？!?,;；\s]{2,10})"
        r"(?:公园|海边|山|商场|超市|书店|咖啡厅|外面|外面走走)?"
    )),
    ("walk", "today", re.compile(r"(?:出去走走|散散步|出门转转|出去转转|下楼走走)")),
    ("eat", "today", re.compile(
        r"(?:想吃|要吃|去吃|吃什么|好饿|想吃点)([^，。！？!?,;；\s]{0,10})"
    )),
    ("visit_friend", "this_week", re.compile(
        r"(?:去找|去看看|拜访|串门|约了)([^，。！？!?,;；\s]{2,8})"
    )),
    ("pet_care", "today", re.compile(
        r"(?:帮我喂|喂一下猫|喂狗|照顾好它|给它喂食|铲屎)"
    )),
    ("create", "this_week", re.compile(
        r"(?:写一首|画一幅|做个视频|写篇|创作|拍个)([^，。！？!?,;；\s]{0,10})"
    )),
    ("study", "this_week", re.compile(
        r"(?:想学|要学|开始学|准备学|学一下)([^，。！？!?,;；\s]{2,10})"
    )),
]

# 显式指令（当轮立即执行）
_IMMEDIATE = re.compile(
    r"(?:你去|你现在去|你先去|去睡吧|去洗澡|去吃饭|去休息|去学习|去工作|出去转转)"
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def extract_life_intent(
    character_id: int, user_id: int, user_msg: str,
) -> None:
    """从用户消息提取生活意图，写入缓冲表。失败静默。"""
    import time as _time
    now_ts = _time.monotonic()
    last = _throttle.get(character_id, 0)
    if now_ts - last < THROTTLE_SECONDS:
        return

    text = (user_msg or "").strip()
    if len(text) < 2 or len(text) > 100:
        return

    detected = None
    horizon = "today"
    priority = 1

    # 显式指令优先
    if _IMMEDIATE.search(text):
        if "睡" in text:
            detected, horizon, priority = "sleep", "this_turn", 3
        elif "吃" in text:
            detected, horizon, priority = "eat", "this_turn", 3
        elif "学习" in text or "工作" in text:
            detected, horizon, priority = "study", "this_turn", 3
        elif "洗澡" in text or "休息" in text:
            detected, horizon, priority = "rest", "this_turn", 3
        else:
            detected, horizon, priority = "walk", "this_turn", 3
    else:
        for action_type, h, pat in _PATTERNS:
            m = pat.search(text)
            if m:
                detected = action_type
                horizon = h
                priority = 2
                break

    if detected is None:
        return

    _throttle[character_id] = now_ts

    async def _persist() -> None:
        async with async_session_factory() as db:
            # 去重：同角色同动作类型 24h 内已有 pending 则不重复写
            from datetime import timedelta
            existing = (await db.execute(
                select(LifeChatIntent).where(
                    LifeChatIntent.character_id == character_id,
                    LifeChatIntent.action_type == detected,
                    LifeChatIntent.status == "pending",
                    LifeChatIntent.created_at >= _now() - timedelta(hours=24),
                )
            )).scalar_one_or_none()
            if existing:
                return
            db.add(LifeChatIntent(
                character_id=character_id, user_id=user_id,
                action_type=detected, horizon=horizon,
                raw_text=text[:100], priority=priority,
                status="pending",
            ))
            await db.commit()
            _logger.info("life intent extracted: char=%d action=%s horizon=%s",
                         character_id, detected, horizon)
            # 即时指令（修正 2026-08-26）：priority>=3（this_turn）不等 30min tick，立即触发该角色一个 Life Loop 回合
            if priority >= 3:
                import asyncio
                from app.life.life_loop import run_character_tick
                asyncio.ensure_future(run_character_tick(character_id, user_id))

    # v3.3.6 CI 加固：aiosqlite 线程残留偶发导致写库失败（异常被吞成 0 条），失败重试 1 次并带 traceback 记录
    for _attempt in range(2):
        try:
            await _persist()
            return
        except Exception as e:
            if _attempt == 0:
                import asyncio
                await asyncio.sleep(0.3)
                continue
            _logger.warning("extract_life_intent failed: %s", e, exc_info=True)
            return
