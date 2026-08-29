"""认知循环反思层（v2.1 Reflection）：概率+高风险触发，本地启发式自查，不重生成。

零 LLM 成本：重复检测/情绪匹配/记忆一致性/长度合规均为本地规则；
结果仅落 reflection_log 供评测，不改变输出、不增加延迟。
"""
import json
import random

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.chat_message import ChatMessage
from app.models.reflection_log import ReflectionLog
from app.utils.logger import get_logger

_logger = get_logger("agent.reflection")

# 触发策略：高风险场景（情绪倾诉/深层交流 或 回复超长）直接触发；其他场景随机采样
REFLECT_RANDOM_SAMPLE = 0.05
HIGH_RISK_INTENTS = ("emotion", "deep")
MAX_TEXT_CHARS = 400
# 重复检测：与最近 AI 消息的相似度阈值
REPETITION_THRESHOLD = 0.75
REPETITION_WINDOW = 3
# 情绪匹配：低落/倾诉场景应出现的共情词
_EMPATHY_WORDS = (
    "抱抱", "别难过", "心疼", "陪你", "理解", "我在", "慢慢说", "辛苦了",
    "不着急", "没事的", "别怕", "摸摸", "听你说", "都懂", "别哭", "肩膀",
    "不好受", "委屈", "说说", "怎么了", "怎么啦", "别急", "我在听", "我懂",
    "陪着", "听你", "难受", "别多想", "想哭就哭", "递纸巾",
)
_EMOTION_CHECKS = ("sad", "venting")
# 长度合规：闲聊上限 / 情绪下限
SMALLTALK_MAX_CHARS = 200
EMOTION_MIN_CHARS = 6


async def _repetition_check(session_id: int, text: str) -> dict:
    """与最近 AI 消息的相似度检查（SequenceMatcher）"""
    from difflib import SequenceMatcher
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(ChatMessage.content)
                .where(ChatMessage.session_id == session_id, ChatMessage.sender_type == "ai")
                .order_by(ChatMessage.id.desc())
                .limit(REPETITION_WINDOW)
            )).scalars().all()
        for r in rows:
            other = (r or "").strip()
            if not other:
                continue
            ratio = SequenceMatcher(None, other, text).ratio()
            if ratio >= REPETITION_THRESHOLD:
                return {"pass": False, "reason": f"与最近消息重复度过高({ratio:.2f})"}
        return {"pass": True, "reason": "无明显重复"}
    except Exception as e:
        return {"pass": True, "reason": "skip", "error": str(e)}


def _emotion_check(perception: dict | None, text: str) -> dict:
    """情绪匹配：低落/倾诉场景应有共情表达"""
    label = (perception or {}).get("emotion_label") or ""
    if label not in _EMOTION_CHECKS:
        return {"pass": True, "reason": "非情绪场景，跳过"}
    if any(w in text for w in _EMPATHY_WORDS):
        return {"pass": True, "reason": "包含共情表达"}
    return {"pass": False, "reason": "情绪场景缺少共情表达"}


async def _memory_consistency_check(state: dict, text: str) -> dict:
    """记忆一致性：检索到记忆时，情绪/深层场景不应过于敷衍"""
    mems = state.get("retrieved_memories") or []
    intent = (state.get("perception") or {}).get("intent") or ""
    if not mems:
        return {"pass": True, "reason": "无检索记忆，跳过"}
    if intent in HIGH_RISK_INTENTS and len(text) < EMOTION_MIN_CHARS:
        return {"pass": False, "reason": "检索到记忆但情绪/深层场景回复过于敷衍"}
    return {"pass": True, "reason": "记忆引用正常"}


def _length_check(perception: dict | None, text: str) -> dict:
    """长度合规：闲聊别啰嗦，情绪场景别敷衍"""
    intent = (perception or {}).get("intent") or ""
    n = len(text)
    if intent == "smalltalk" and n > SMALLTALK_MAX_CHARS:
        return {"pass": False, "reason": f"闲聊回复过长({n}字)"}
    if intent in HIGH_RISK_INTENTS and n < EMOTION_MIN_CHARS:
        return {"pass": False, "reason": f"情绪/深层场景回复过短({n}字)"}
    return {"pass": True, "reason": "长度合规"}


async def evaluate_reflection(state: dict) -> dict | None:
    """评估反思：触发判断 + 本地自查。返回 result 或 None（未触发/无内容）。

    不改变 state 输出；由调用方在拿到 AI 消息 id 后调用 persist_reflection 落库。
    """
    text = (state.get("ai_response") or "").strip()
    if not text:
        return None
    perception = state.get("perception") or {}
    intent = perception.get("intent") or ""
    high_risk = intent in HIGH_RISK_INTENTS or len(text) > MAX_TEXT_CHARS
    if not (high_risk or random.random() < REFLECT_RANDOM_SAMPLE):
        return None
    triggers = ["high_risk"] if high_risk else ["random_sample"]
    checks = {
        "repetition": await _repetition_check(state.get("session_id") or 0, text),
        "emotion_match": _emotion_check(perception, text),
        "memory_consistency": await _memory_consistency_check(state, text),
        "length_compliance": _length_check(perception, text),
    }
    passed = all(v.get("pass", True) for v in checks.values())
    return {
        "triggered": True,
        "triggers": triggers,
        "checks": checks,
        "pass_or_fail": "PASS" if passed else "FAIL",
    }


async def persist_reflection(
    character_id: int,
    user_id: int,
    message_id: int | None,
    result: dict | None,
) -> None:
    """反思结果落库（拿到 AI 消息 id 后调用，失败静默）"""
    if not result or not result.get("triggered"):
        return
    try:
        async with async_session_factory() as db:
            db.add(ReflectionLog(
                character_id=character_id,
                user_id=user_id,
                message_id=message_id,
                triggers=json.dumps(result.get("triggers", []), ensure_ascii=False),
                checks=json.dumps(result.get("checks", {}), ensure_ascii=False),
                pass_or_fail=result.get("pass_or_fail", "PASS"),
            ))
            await db.commit()
        _logger.info("Reflection logged: char=%d msg=%s result=%s",
                     character_id, message_id, result.get("pass_or_fail"))
    except Exception as e:
        _logger.warning("Reflection persist failed: %s", e)