"""记忆架构 v2.1：意义记忆提取（Phase 2）+ 关系事件判定（Phase 3b）。

均为"记忆落库后的异步低频 LLM 后处理"，不进入聊天主链路（主链路保持每回复 1 次 LLM）：
- 意义记忆：event / relationship 里程碑（importance>=3 星）→ LLM 提炼 why_it_matters（≤80 字，AI 第一人称）→ 更新原记忆，不新增重复条目；
- 关系事件：relationship 类记忆 → LLM 判定 change_type / trust_delta → 写 relationship_events（溯源 memory_id，不双写记忆）。

节流：每角色 6 小时一批、单批 ≤3 条（进程内节流，低频任务重启丢失可接受）；
开关：ai_characters.memory_v2_enabled（默认关），关闭时全部跳过。
"""
import json
import time

from sqlalchemy import select

from app.db.database import async_session_factory
from app.utils.logger import get_logger
from app.memory.flags import memory_v2_enabled as _memory_v2_enabled
from app.utils.timeutil import now_naive_utc as _now_naive

_logger = get_logger("memory.meaning")

MEANING_THROTTLE_SECONDS = 6 * 3600      # 每角色 6 小时一批
MEANING_BATCH_SIZE = 3                    # 单批最多 3 条
MEANING_MIN_IMPORTANCE_PCT = 60.0         # 里程碑：importance >= 60%（3 星）
MEANING_MAX_LEN = 80                      # why_it_matters 长度上限
RELATION_CHANGE_TYPES = (
    "trust_up", "trust_down", "closer", "distant", "care", "apology", "cold_war", "other",
)

_meaning_last_run: dict[int, float] = {}


def _is_milestone(memory_type: str, sub_type: str | None, importance_pct: float) -> bool:
    """里程碑判定：event，或 relationship/meaning 类 insight，且重要度达标"""
    if float(importance_pct or 0) < MEANING_MIN_IMPORTANCE_PCT:
        return False
    if memory_type == "event":
        return True
    if memory_type == "insight" and (sub_type or "") in ("relationship", "meaning"):
        return True
    return False


async def maybe_extract_meaning(
    character_id: int, user_id: int, memory_id: int,
    memory_type: str, sub_type: str | None, content: str, importance_pct: float,
) -> None:
    """记忆落库后调用（异步）：里程碑且开关开启时登记待提炼，到节流窗口统一批量执行。"""
    try:
        if not await _memory_v2_enabled(character_id):
            return
        if not _is_milestone(memory_type, sub_type, importance_pct):
            return
        now = time.time()
        if now - _meaning_last_run.get(character_id, 0) < MEANING_THROTTLE_SECONDS:
            return
        await run_meaning_extraction(character_id, user_id)
    except Exception as e:
        _logger.warning("Meaning extraction trigger failed char=%d: %s", character_id, e)


async def run_meaning_extraction(character_id: int, user_id: int) -> int:
    """批量提炼：取未提炼的里程碑记忆（≤3 条）→ LLM 输出 JSON → 更新 why_it_matters / 写关系事件。"""
    from app.agent.llm_client import chat_completion
    from app.models.character import AICharacter
    from app.models.memory import Memory
    from app.models.relationship_event import RelationshipEvent

    _meaning_last_run[character_id] = time.time()
    async with async_session_factory() as db:
        char = await db.get(AICharacter, character_id)
        if char is None or not char.memory_v2_enabled:
            return 0
        from sqlalchemy import or_
        rows = (await db.execute(
            select(Memory)
            .where(
                Memory.character_id == character_id,
                Memory.is_archived == False,
                Memory.why_it_matters.is_(None),
                Memory.importance >= MEANING_MIN_IMPORTANCE_PCT,
                or_(
                    Memory.memory_type == "event",
                    (Memory.memory_type == "insight") & (Memory.sub_type.in_(["relationship", "meaning"])),
                ),
            )
            .order_by(Memory.importance.desc(), Memory.id.desc())
            .limit(MEANING_BATCH_SIZE)
        )).scalars().all()
        items = [m for m in rows if _is_milestone(m.memory_type, m.sub_type, float(m.importance or 0))]
        if not items:
            return 0
        char_name = char.name or "AI"
        mem_list = "\n".join(
            f"{i + 1}. id={m.id} 类型={m.memory_type}({m.sub_type or ''}) 内容：{(m.content or '')[:80]}"
            for i, m in enumerate(items)
        )
        hint = (
            f"你是{char_name}。以下是你关于用户的记忆条目（事件/关系里程碑）。\n"
            "对每条输出：1) why——这件事对用户或你们关系**为什么重要**（≤80字，第一人称'我'视角，"
            "要讲出背后的意义/情绪/动机，不是复述事实）；2) 若是关系类记忆，额外判断 change_type"
            f"（{'/'.join(RELATION_CHANGE_TYPES)}）与 trust_delta（-10到+10整数）。\n"
            '严格输出 JSON 数组：[{"id": 记忆id, "why": "...", "change_type": "..."|null, "trust_delta": 0|null}]，只输出 JSON。\n'
            'why 中禁止使用"今天/昨天/最近"等相对时间词，涉及时间写具体日期（以记忆发生时间为准，不确定用"有一次/某天"）。\n'
            f"记忆列表：\n{mem_list}"
        )
        text = await chat_completion(
            messages=[
                {"role": "system", "content": "只输出 JSON 数组，不要其他文字。"},
                {"role": "user", "content": hint},
            ],
            temperature=0.3, max_tokens=1024,
            task="memory", user_id=user_id,
        )
        text = (text or "").strip()
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            _logger.warning("Meaning extraction parse failed char=%d", character_id)
            return 0
        try:
            data = json.loads(text[start:end + 1])
        except Exception as e:
            _logger.warning("Meaning extraction json failed: %s", e)
            return 0
        if not isinstance(data, list):
            return 0

        by_id = {r["id"]: r for r in data if isinstance(r, dict) and r.get("id") is not None}
        updated = 0
        now = _now_naive()
        # 注意：items 来自上一个已关闭的 session（分离对象），必须在本 session 重新 get 绑定后才能写库
        async with async_session_factory() as db:
            for m in items:
                row = by_id.get(m.id)
                if not row:
                    continue
                fresh = await db.get(Memory, m.id)
                if fresh is None:
                    continue
                why = str(row.get("why") or "").strip()
                if why and len(why) > MEANING_MAX_LEN:
                    why = why[:MEANING_MAX_LEN]
                if why:
                    fresh.why_it_matters = why
                    fresh.updated_at = now
                    updated += 1
                # 关系事件（仅关系类记忆）
                if (fresh.memory_type == "insight" and (fresh.sub_type or "") == "relationship"):
                    ctype = str(row.get("change_type") or "other").strip()
                    if ctype not in RELATION_CHANGE_TYPES:
                        ctype = "other"
                    try:
                        delta = int(row.get("trust_delta") or 0)
                    except Exception:
                        delta = 0
                    delta = max(-10, min(10, delta))
                    if ctype != "other" or delta != 0:
                        db.add(RelationshipEvent(
                            character_id=character_id, user_id=user_id,
                            event=str(fresh.content or "")[:300], content=(fresh.content or "")[:500],
                            change_type=ctype, trust_delta=delta, memory_id=fresh.id,
                        ))
            await db.commit()
        if updated:
            _logger.info("Meaning extraction char=%d updated=%d", character_id, updated)
        return updated
