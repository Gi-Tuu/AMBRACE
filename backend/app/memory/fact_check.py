"""异步 LLM 事实核查（World & Cognition P5，2026-08-15；不阻塞回复）

对话后异步检查 AI 回复是否与已知记忆矛盾（轻量、低频）：

- 门槛：角色 memory_v2_enabled=True；每角色每日 ≤ 30 次（进程内计数，重启重置）；
  用户消息 ≥ 15 字（闲聊跳过）→ 成本可控（方案模型 ~$0.90/月/角色）
- 流程：检索 top3 相关记忆 → LLM 判断矛盾 → 命中则 contradiction+1 + FACT→UNVERIFIED
- 范围（2026-08-15 用户拍板）：只做可靠度纠正，不做违规重生成/内容拦截
"""
import json
from datetime import datetime, timezone

from app.memory.flags import memory_v2_enabled as _memory_v2_enabled
from app.utils.logger import get_logger

_logger = get_logger("memory.fact_check")

DAILY_LIMIT_PER_CHAR = 30
MIN_USER_MSG_LEN = 15

_daily_count: dict[int, int] = {}
_count_day: str = ""


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _check_limit(character_id: int) -> bool:
    """当日配额判断（进程内计数，重启清零可接受）。"""
    global _count_day, _daily_count
    today = _today()
    if _count_day != today:
        _count_day = today
        _daily_count.clear()
    if _daily_count.get(character_id, 0) >= DAILY_LIMIT_PER_CHAR:
        return False
    _daily_count[character_id] = _daily_count.get(character_id, 0) + 1
    return True


async def async_fact_check(character_id: int, user_id: int, user_msg: str,
                           ai_response: str) -> None:
    """异步事实核查：回复与已知记忆矛盾 → 标记矛盾并降级。失败静默。"""
    user_msg = (user_msg or "").strip()
    ai_response = (ai_response or "").strip()
    if len(user_msg) < MIN_USER_MSG_LEN or not ai_response:
        return
    if not await _memory_v2_enabled(character_id):
        return
    if not _check_limit(character_id):
        return
    try:
        from app.memory import search_memories
        from app.memory.reliability import apply_correction
        hits = await search_memories(character_id=character_id, query=user_msg, limit=3)
        if not hits:
            return
        facts = "\n".join(f"- [{h.get('id')}] {h.get('content', '')[:80]}" for h in hits)
        prompt = (
            "你是记忆一致性检查器。判断下面的 AI 回复是否与已知记忆条目矛盾。\n"
            f"已知记忆：\n{facts}\n\n"
            f"AI 回复：{ai_response[:400]}\n\n"
            "只输出 JSON：{\"contradictions\": [{\"memory_id\": 数字, \"reason\": \"一句话原因\"}]}。"
            "没有矛盾则输出 {\"contradictions\": []}。不要输出其他内容。"
        )
        from app.agent.llm_client import chat_completion
        text = (await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150, temperature=0,
            task="memory", user_id=user_id,
        ) or "").strip()
        raw = text.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
        for item in (data.get("contradictions") or [])[:3]:
            mid = int(item.get("memory_id") or 0)
            if mid and any(h.get("id") == mid for h in hits):
                await apply_correction(mid)
                _logger.info("fact check contradiction: char=%d mem=%d reason=%.40s",
                             character_id, mid, str(item.get("reason") or ""))
    except Exception as e:
        _logger.warning("async_fact_check failed char=%d: %s", character_id, e)


def schedule_fact_check(character_id: int, user_id: int, user_msg: str, ai_response: str) -> None:
    """fire-and-forget 入口（不阻塞回复）。P0-1b：经内部统一工具入口（生命周期/事件/异常隔离）"""
    import asyncio
    try:
        from app.agent.internal_runner import run_internal
        asyncio.ensure_future(run_internal(
            "memory_fact_check",
            {"character_id": character_id, "user_id": user_id,
             "user_msg": user_msg, "ai_response": ai_response},
            character_id=character_id, user_id=user_id,
        ))
    except Exception as e:
        _logger.warning("schedule_fact_check failed: %s", e)
