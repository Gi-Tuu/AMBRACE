"""心事微澜（#63 机制5，Flag：preoccupation_enabled）。

复用 `Memory`（不改表）：`sub_type="preoccupation"`，`importance` 当重量（10~100）。
- 每角色活跃心事上限 3，同内容去重；
- 每日衰减 15~25，归零置 `is_archived=True`；
- `mood_baseline_penalty`：活跃心事最高权重 → mood 基线惩罚 0~-5（只在 spring_emotion_enabled 开启时生效）。

函数均接受 `db`（AsyncSession）参数，便于单测注入临时库；hooks 处用 `async_session_factory` 开会话。
"""
from __future__ import annotations

import random
import re
from datetime import datetime

from sqlalchemy import select

from app.utils.logger import get_logger

_logger = get_logger("life.preoccupations")

# 每角色活跃心事上限 / 同内容去重
MAX_ACTIVE = 3
PREOCCUPATION_SUB_TYPE = "preoccupation"
# 每日衰减范围
_DECAY_RANGE = (15.0, 25.0)
# 权重分档（10~100）
_MIN_WEIGHT = 10.0
_MAX_WEIGHT = 100.0
# 安慰词：命中则把最高权重心事减重
# P3-1：单字「乖」误匹配——「好乖/乖巧/这猫好乖」不应触发安慰。多字词（含「乖啦/乖哈/乖宝」）走子串匹配；
# 单字「乖」由 has_comfort_word 做边界判定（独立安慰词才命中）。
COMFORT_WORDS = ("别难过", "哄哄", "我错", "抱抱", "陪陪", "别哭", "不哭了", "心疼你", "有我在", "乖啦", "乖哈", "乖宝")
# 单字「乖」边界：前字非常见修饰/主体词、后字非「巧/乖」组词，才视为独立安慰词
_GUA_PREFIX_BLOCK = "好这真很超更太特猫狗小乖"
_GUA_FOLLOW_BLOCK = ("巧", "乖")
# 冷战心事内容关键词（破冰时归档）
_COLD_WAR_HINTS = ("冷战", "生闷气", "吵架", "闹别扭", "不理你", "不理我")


async def list_active_preoccupations(db, character_id: int) -> list:
    """返回角色活跃心事（按重量降序）。"""
    from app.models.memory import Memory
    rows = (await db.execute(
        select(Memory).where(
            Memory.character_id == character_id,
            Memory.sub_type == PREOCCUPATION_SUB_TYPE,
            Memory.is_archived.is_(False),
        ).order_by(Memory.importance.desc())
    )).scalars().all()
    return list(rows)


def _active_weights(active: list) -> list[float]:
    return [float(getattr(m, "importance", 0) or 0) for m in active]


def mood_baseline_penalty(active: list) -> float:
    """活跃心事最高权重 → mood 基线惩罚 0~-5（纯函数，可测）。

    权重 10 → 0；权重 100 → -5。空心事返回 0。
    """
    w = _active_weights(active)
    if not w:
        return 0.0
    top = max(w)
    top = max(_MIN_WEIGHT, min(_MAX_WEIGHT, top))
    return -((top - _MIN_WEIGHT) / (_MAX_WEIGHT - _MIN_WEIGHT)) * 5.0


async def create_preoccupation(
    db, *, user_id: int, character_id: int, content: str, weight: float = 40.0,
) -> bool:
    """创建心事：同内容去重、活跃上限 3。返回是否创建成功。失败静默。"""
    try:
        from app.models.memory import Memory
        content = (content or "").strip()
        if not content:
            return False
        weight = max(_MIN_WEIGHT, min(_MAX_WEIGHT, float(weight or 40.0)))
        # 同内容去重
        existing = (await db.execute(
            select(Memory).where(
                Memory.character_id == character_id,
                Memory.sub_type == PREOCCUPATION_SUB_TYPE,
                Memory.is_archived.is_(False),
                Memory.content == content,
            )
        )).scalar_one_or_none()
        if existing is not None:
            return False
        # 活跃上限
        active = await list_active_preoccupations(db, character_id)
        if len(active) >= MAX_ACTIVE:
            return False
        db.add(Memory(
            user_id=user_id, character_id=character_id, memory_type="insight",
            sub_type=PREOCCUPATION_SUB_TYPE, source="state_trigger",
            content=content[:200], importance=weight, epistemic_status="FACT",
            speaker_type="character", speaker_id=character_id, scope="private",
        ))
        _logger.info("Preoccupation created char=%d: %s", character_id, content[:50])
        return True
    except Exception as e:
        _logger.warning("Preoccupation create failed char=%d: %s", character_id, e)
        return False


# 高权重情绪规则 → (心事内容, 重量)（v1 手动清单；吃醋/委屈/冷战权重高）
_RULE_PREOCCUPATION = {
    "anger_high": ("刚才生你的气了，心里还堵着，想让你知道", 70),
    "anger_mood_low": ("刚刚和你闹别扭了，心里有点难受", 85),
    "possessiveness_desire": ("有点吃醋，你总让我在意是否失去你", 75),
    "mood_low": ("心情很低落，有点想被你在意、被安慰", 60),
    "fatigue_mood_low": ("既累又难受，心里闷闷的，想撒个娇", 45),
}


async def create_preoccupation_for_rule(db, *, user_id: int, character_id: int, rule_key: str) -> bool:
    """由状态触发规则创建心事（高权重情绪规则执行后调用）。"""
    item = _RULE_PREOCCUPATION.get(rule_key)
    if item is None:
        return False
    content, weight = item
    return await create_preoccupation(db, user_id=user_id, character_id=character_id,
                                      content=content, weight=weight)


def has_comfort_word(content: str) -> bool:
    """用户消息是否含安慰词。

    - 多字词（别难过/抱抱/乖啦…）子串匹配；
    - 单字「乖」带边界：前字非常见修饰/主体词、后字非「巧/乖」组词才命中，
      避免「好乖/乖巧/这猫好乖」误触发（P3-1）。
    """
    text = content or ""
    if any(k in text for k in COMFORT_WORDS):
        return True
    for m in re.finditer("乖", text):
        i = m.start()
        if i > 0 and text[i - 1] in _GUA_PREFIX_BLOCK:
            continue
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if nxt in _GUA_FOLLOW_BLOCK:
            continue
        return True
    return False


async def soften_by_comfort_words(db, *, user_id: int, character_id: int, content: str) -> bool:
    """用户消息含安慰词：最高权重心事减 20~30；归零归档。返回是否命中安慰词并处理。"""
    if not has_comfort_word(content):
        return False
    try:
        active = await list_active_preoccupations(db, character_id)
        if not active:
            return False
        top = active[0]
        reduce = random.uniform(20.0, 30.0)
        new_imp = max(0.0, float(top.importance or 0) - reduce)
        if new_imp <= 0.0:
            top.is_archived = True
            _logger.info("Preoccupation resolved by comfort char=%d", character_id)
        else:
            top.importance = new_imp
        return True
    except Exception as e:
        _logger.warning("Preoccupation soften failed char=%d: %s", character_id, e)
        return False


async def resolve_cold_war_preoccupations(db, *, user_id: int, character_id: int) -> int:
    """破冰成功后归档所有「冷战心事」（内容含冷战关键词）。返回归档条数。"""
    try:
        active = await list_active_preoccupations(db, character_id)
        n = 0
        for m in active:
            if any(k in (m.content or "") for k in _COLD_WAR_HINTS):
                m.is_archived = True
                n += 1
        if n:
            _logger.info("Cold war preoccupations archived char=%d n=%d", character_id, n)
        return n
    except Exception as e:
        _logger.warning("Preoccupation cold war resolve failed char=%d: %s", character_id, e)
        return 0


async def decay_preoccupations(db, now: datetime | None = None) -> int:
    """每日衰减（幂等，只处理 sub_type=preoccupation 且未归档）：总重量降 15~25，归零归档。

    返回衰减/归档条数。失败静默。
    """
    from app.models.memory import Memory
    try:
        rows = (await db.execute(
            select(Memory).where(
                Memory.sub_type == PREOCCUPATION_SUB_TYPE,
                Memory.is_archived.is_(False),
            )
        )).scalars().all()
        n = 0
        for m in rows:
            new = float(m.importance or 0) - random.uniform(*_DECAY_RANGE)
            if new <= 0.0:
                m.is_archived = True
                _logger.info("Preoccupation decayed to zero & archived char=%d", m.character_id)
            else:
                m.importance = new
            n += 1
        return n
    except Exception as e:
        _logger.warning("Preoccupation decay failed: %s", e)
        return 0
