"""AI 自主评星（P2，2026-08-05）：LLM 按内容批量评估记忆重要性 → 更新 importance/S，标记 ai_rated。

- 每角色每日上限 AI_RATING_MAX_PER_CHAR（10 条），单次 LLM 批量 AI_RATING_BATCH（10 条）
- 只评未评过（ai_rated=False）且非置顶/锁定/归档的记忆
- 评星结果与手动评星同语义：importance = star*20（上限 120%）、S 拉高、刷新遗忘起点、清除删除倒计时
- 成本：每角色每天最多 1 次 LLM 调用（批量 10 条，≈0.5-1k token），可控
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.models.character import AICharacter
from app.utils.logger import get_logger
from app.memory.constants import (
    AI_RATING_MAX_PER_CHAR, AI_RATING_BATCH, S_MAX_DAYS, S_MIN_DAYS,
    DECAY_MAX_PCT, S_DEFAULT,
)

_logger = get_logger("memory.ai_rating")


from app.utils.timeutil import now_naive_utc as _now_naive


async def _daily_rated(db, character_id: int) -> int:
    """今天（北京时间）已评星条数（按 ai_rated + updated_at 近似统计）。"""
    cn_tz = timezone(timedelta(hours=8))
    today_start = datetime.now(cn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start.astimezone(timezone.utc).replace(tzinfo=None)
    return (await db.execute(
        select(func.count(Memory.id)).where(
            Memory.character_id == character_id,
            Memory.ai_rated == True,
            Memory.updated_at >= today_start,
        )
    )).scalar() or 0


async def _pick_candidates(db, character_id: int, limit: int) -> list:
    from app.memory.service import _active_status_clause  # #70-C：失效记忆不再评星（flag 关=永真）
    result = await db.execute(
        select(Memory)
        .where(
            Memory.character_id == character_id,
            Memory.is_archived == False,
            Memory.is_pinned == False,
            Memory.is_locked == False,
            Memory.ai_rated == False,
            _active_status_clause(),
        )
        .order_by(Memory.id.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def _rate_batch(character, items: list) -> list[dict]:
    """一次 LLM 调用批量评星：返回 [{"id", "star"}]；失败/解析失败返回 []。"""
    from app.agent.llm_client import chat_completion
    char_name = character.name if character else "我"
    mem_list = "\n".join(
        f"{i + 1}. id={m.id} 类型={m.memory_type} 内容：{(m.content or '')[:80]}"
        for i, m in enumerate(items)
    )
    hint = (
        f"你是{char_name}。下面是你关于用户的{len(items)}条记忆。"
        "请评估每条记忆对你们关系的重要性，输出严格 JSON 数组，"
        '格式：[{"id": 记忆id, "star": 1到5}]，star 越大越重要。只输出 JSON，不要其他文字。\n'
        f"记忆列表：\n{mem_list}"
    )
    text = await chat_completion(
        messages=[
            {"role": "system", "content": "只输出 JSON 数组。"},
            {"role": "user", "content": hint},
        ],
        temperature=0.2,
        max_tokens=512,
        task="memory", user_id=(character.user_id if character else 1),
    )
    text = (text or "").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except Exception as e:
        _logger.warning("AI rating parse failed: %s", e)
        return []
    if not isinstance(data, list):
        return []
    result = []
    for row in data:
        if isinstance(row, dict) and "id" in row and isinstance(row.get("star"), int):
            result.append({"id": row["id"], "star": max(1, min(5, row["star"]))})
    return result


async def run_ai_rating() -> int:
    """扫描各角色未评记忆 → 批量 LLM 评星（每日限额）→ 更新。返回评星条数。"""
    now = _now_naive()
    async with async_session_factory() as db:
        chars = (await db.execute(
            select(AICharacter).where(AICharacter.is_active == True)
        )).scalars().all()
    rated_total = 0
    for char in chars:
        try:
            async with async_session_factory() as db:
                used = await _daily_rated(db, char.id)
                if used >= AI_RATING_MAX_PER_CHAR:
                    continue
                quota = AI_RATING_MAX_PER_CHAR - used
                items = await _pick_candidates(db, char.id, min(AI_RATING_BATCH, quota))
                if not items:
                    continue
                results = await _rate_batch(char, items)
                if not results:
                    continue
                by_id = {r["id"]: r["star"] for r in results}
                n = 0
                for m in items:
                    star = by_id.get(m.id)
                    if star is None:
                        continue
                    m.importance = min(DECAY_MAX_PCT, float(star * 20))
                    s = float(m.strength_days or S_DEFAULT)
                    m.strength_days = min(S_MAX_DAYS, max(S_MIN_DAYS, max(s, star / 5.0 * S_MAX_DAYS)))
                    m.review_count = (m.review_count or 0) + 1
                    m.last_reinforce_at = now
                    m.delete_at = None
                    m.next_review_at = now + timedelta(days=float(m.strength_days))
                    m.ai_rated = True
                    m.updated_at = now
                    n += 1
                await db.commit()
                if n:
                    _logger.info("AI rating char=%d rated=%d", char.id, n)
                    rated_total += n
        except Exception as e:
            _logger.warning("AI rating char=%d failed: %s", char.id, e)
    if rated_total:
        _logger.info("AI rating total=%d", rated_total)
    return rated_total
