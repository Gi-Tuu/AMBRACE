"""聊天→生活意图提取（设计稿 §10）。零 LLM，本地规则，与 topic_tracker 同构。

在 chat_service._run_post_processing 中异步调用，不阻塞回复。
提取结果写入 life_chat_intents 缓冲表，Life Loop 下拉消费。
修正 2026-08-26：priority>=3（this_turn）写库后立即触发该角色一个 Life Loop 回合，不等 30min tick。
修正 2026-09-08（F2 语用收敛）：仅"明确让角色去做什么"才触发；转述/元讨论/自述/疑问/否定零触发
（曾致用户一提"吃饭"→ Sam 立即 eat、自述「我去洗澡了」被固化为 Sam 待办永挂）。
"""
from __future__ import annotations
import asyncio
from app.utils.async_tasks import spawn_background
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
# F2d（2026-09-08）语用收敛：想吃/要去/好饿/想学等欲望类属**用户自身**行为，不再固化为
# 角色待办（曾致自述「我去洗澡了/我陪你吃烧烤」→ Sam 待执行意图长期 pending）。
# 仅保留语义明确为"用户要求角色做"的模式（pet_care 帮我喂类）。
_PATTERNS = [
    ("pet_care", "today", re.compile(
        r"(?:帮我喂|喂一下猫|喂狗|照顾好它|给它喂食|铲屎)"
    )),
]

# 显式指令（当轮立即执行）——F2c（2026-09-08）：仅显式针对角色的祈使（第二人称
# "你去/你现在去/你先去"或带"吧"的纯祈使）才 this_turn；无主语裸动作（去吃饭/去洗澡）
# 出现在用户消息中按用户自述处理，不再触发。
_IMMEDIATE = re.compile(
    r"(?:你去|你现在去|你先去|去睡吧|睡吧|去吃饭吧|去洗澡吧|去休息吧|去学习吧|去工作吧|出去转转吧)"
)

# 元讨论/转述守卫（F2a 2026-09-08）：命中即整句视为在讨论系统/事件/bug 本身 → 零触发
_META_CONTEXT = re.compile(
    r"你说|你又说|你说过|你说来|你刚才|生成了?|事件|bug|报错|出了?问题|有问题|问题|"
    r"修一下|修好|修复|理解错|误会|搞错|弄错|错了|就是|其实"
)

# 第一人称自述守卫（F2b 2026-09-08）：用户自身行为（我去/我要/我陪/我们…）→ 不触发角色
_FIRST_PERSON = re.compile(
    r"我(?:去|要|会|想|先|马上|等下|等会|待会|一会|一会儿|下午|晚上|今天|明天|陪)|我们"
)

# 否定/疑问/假设守卫（F2e 2026-09-08）
_NEGATIVE_OR_QUESTION = re.compile(
    r"别|不(?:想|要|去|吃|睡|洗)|？|\?|吗|要不|要不要|好不好|行不行"
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _schedule_immediate_tick(character_id: int, user_id: int) -> None:
    """即时指令写库后触发该角色一个 Life Loop 回合（后台任务，不阻塞回复）。"""
    from app.life.life_loop import run_character_tick
    spawn_background(run_character_tick(character_id, user_id))


def detect_life_intent(text: str) -> dict | None:
    """零 IO 纯函数：从用户消息检测生活意图（v3.3.6 CI 加固，便于稳定单测）。

    返回 {"action_type", "horizon", "priority"} 或 None。
    """
    text = (text or "").strip()
    if len(text) < 2 or len(text) > 100:
        return None
    # F2a 元讨论/转述守卫：讨论系统/事件/bug 本身（如 09-08 19:27「你说去吃饭……生成了吃饭事件」）
    if _META_CONTEXT.search(text):
        return None
    # F2e 否定/疑问/假设守卫
    if _NEGATIVE_OR_QUESTION.search(text):
        return None
    # F2b 第一人称自述守卫：用户自己的行为，不固化为角色待办
    if _FIRST_PERSON.search(text):
        return None
    # 显式指令优先（F2c：仅显式针对角色的祈使）
    if _IMMEDIATE.search(text):
        if "睡" in text:
            return {"action_type": "sleep", "horizon": "this_turn", "priority": 3}
        if "吃" in text:
            return {"action_type": "eat", "horizon": "this_turn", "priority": 3}
        if "学习" in text or "工作" in text:
            return {"action_type": "study", "horizon": "this_turn", "priority": 3}
        if "洗澡" in text or "休息" in text:
            return {"action_type": "rest", "horizon": "this_turn", "priority": 3}
        return {"action_type": "walk", "horizon": "this_turn", "priority": 3}
    for action_type, h, pat in _PATTERNS:
        if pat.search(text):
            return {"action_type": action_type, "horizon": h, "priority": 2}
    return None


async def extract_life_intent(
    character_id: int, user_id: int, user_msg: str,
    *, session_factory=None, tick_scheduler=None, throttle_state=None,
) -> str:
    """从用户消息提取生活意图，写入缓冲表。失败静默，返回处理原因码。"""
    import time as _time
    throttle = throttle_state if throttle_state is not None else _throttle
    now_ts = _time.monotonic()
    last = throttle.get(character_id, 0)
    # last=0 表示该角色从未记录过，不能把 0 当成最近时间（fresh boot 时 monotonic < 300s 会误节流）
    if last and now_ts - last < THROTTLE_SECONDS:
        return "throttled"

    text = (user_msg or "").strip()
    if len(text) < 2 or len(text) > 100:
        return "too_short"

    detected_info = detect_life_intent(text)
    if detected_info is None:
        return "no_intent"
    detected = detected_info["action_type"]
    horizon = detected_info["horizon"]
    priority = detected_info["priority"]

    throttle[character_id] = now_ts
    factory = session_factory or async_session_factory
    scheduler = tick_scheduler or _schedule_immediate_tick

    async def _persist() -> None:
        async with factory() as db:
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
                scheduler(character_id, user_id)

    # v3.3.6 CI 加固：aiosqlite 线程残留偶发导致写库失败（异常被吞成 0 条），失败重试 1 次并带 traceback 记录
    for _attempt in range(2):
        try:
            await _persist()
            return "persisted"
        except Exception as e:
            if _attempt == 0:
                await asyncio.sleep(0.3)
                continue
            _logger.warning("extract_life_intent failed: %s", e, exc_info=True)
            return "failed"
