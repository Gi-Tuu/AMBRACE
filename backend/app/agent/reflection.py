"""认知循环反思层（v2.1 Reflection）：概率+高风险触发，本地启发式自查，不重生成。

零 LLM 成本：重复检测/情绪匹配/记忆一致性/长度合规均为本地规则；
结果仅落 reflection_log 供评测，不改变输出、不增加延迟。
"""
import json
import random

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.chat import ChatMessage
from app.models.memory import ReflectionLog
from app.utils.logger import get_logger

_logger = get_logger("agent.reflection")

# 触发策略：高风险场景（情绪倾诉/深层交流 或 回复超长）直接触发；其他场景随机采样
REFLECT_RANDOM_SAMPLE = 0.05
HIGH_RISK_INTENTS = ("emotion", "deep")
MAX_TEXT_CHARS = 400
# P1-7（2026-09-16）：提高日常抽检率——含现状锚点词的回复也纳入抽检，阈值给常量便于调
STATUS_ANCHOR_WORDS = ("宿舍", "学校", "校区", "在读", "学生", "常驻", "现在住", "常住")
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

# P1-7（2026-09-16）：现状一致性——旧场景/旧地当现状判 FAIL。
# 现状锚点（C3，app.memory.current_state）权威声明「TA 现在怎样」（常驻城市/校区/住所/在读身份等）。
# 回复若把另一地点当此刻现状（典型：长沙窜回正），判 FAIL 并留痕处置。
_STALE_LOCATION_WORDS = (
    "长沙", "北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京",
    "老家", "故乡", "西安", "重庆", "苏州", "天津", "青岛", "沈阳", "哈尔滨",
)
# 把回顾/计划类记忆当「最近发生」的近期时间词
_RECENT_TIME_WORDS = ("最近", "刚刚", "刚", "才", "前两天", "前几天", "这阵子")
_PLAN_MEMORY_SUBTYPES = ("ai_reflection", "review", "plan", "plan_summary")


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


def _extract_anchor_location(anchor_text: str) -> str | None:
    """从现状锚点文本（形如「位置：<城市>；状态：…」）提取权威当前城市。"""
    import re
    m = re.search(r"位置[：:]\s*([\u4e00-\u9fa5]{2,8}?(?:市|省|区|县|州)?)", anchor_text or "")
    return m.group(1) if m else None


async def _memory_consistency_check(state: dict, text: str) -> dict:
    """记忆一致性：检索到记忆时校验——是否「旧场景/旧地当现状」「复习当最近发生」。

    P1-7（2026-09-16）：原实现只查「有没有引用记忆」永远 pass；改为对接现状锚点
    （C3，app.memory.current_state）做时态/现状一致性校验。判 FAIL 时返回 disposition
    （downweight）与 offending_memory_id，由 persist_reflection 闭环留痕（绝不只记日志）。
    """
    mems = state.get("retrieved_memories") or []
    intent = (state.get("perception") or {}).get("intent") or ""
    if not mems:
        return {"pass": True, "reason": "无检索记忆，跳过"}
    if intent in HIGH_RISK_INTENTS and len(text) < EMOTION_MIN_CHARS:
        return {"pass": False, "reason": "检索到记忆但情绪/深层场景回复过于敷衍"}

    # 1) 现状锚点：旧场景/旧地当现状
    anchor = ""
    try:
        from app.memory.current_state import current_user_state_anchor
        anchor = await current_user_state_anchor(
            character_id=state.get("character_id") or 0,
            user_id=state.get("user_id") or 0,
        ) or ""
    except Exception:
        anchor = ""
    if anchor:
        cur_loc = _extract_anchor_location(anchor)
        stale = [w for w in _STALE_LOCATION_WORDS if w in text]
        if cur_loc:
            stale = [w for w in stale if w != cur_loc]  # 锚点城市本身出现在文本不算
        if stale:
            offending = next((m.get("id") for m in mems if isinstance(m, dict) and m.get("id")), None)
            return {
                "pass": False,
                "reason": f"回复把旧场景地（{'/'.join(stale)}）当现状（现状锚点：{cur_loc or anchor[:24]}）",
                "disposition": "downweight",
                "offending_memory_id": offending,
            }

    # 2) 计划/回顾类记忆当最近发生
    for m in mems:
        if not isinstance(m, dict):
            continue
        sub = m.get("sub_type") or ""
        if sub in _PLAN_MEMORY_SUBTYPES and any(k in text for k in _RECENT_TIME_WORDS):
            return {
                "pass": False,
                "reason": f"把回顾/计划类记忆（{sub}）当最近发生",
                "disposition": "downweight",
                "offending_memory_id": m.get("id"),
            }

    return {"pass": True, "reason": "记忆引用与现状一致"}


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
    status_word_hit = any(w in text for w in STATUS_ANCHOR_WORDS)
    triggered_random = random.random() < REFLECT_RANDOM_SAMPLE
    if not (high_risk or triggered_random or status_word_hit):
        return None
    # 真实触发源（不臆造 random_sample）：高风险的标 high_risk，抽样的标 random_sample，
    # 含现状词的标 status_word（P1-7 提高日常抽检率）。
    triggers: list[str] = []
    if high_risk:
        triggers.append("high_risk")
    if triggered_random:
        triggers.append("random_sample")
    if status_word_hit:
        triggers.append("status_word")
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
    """反思结果落库（拿到 AI 消息 id 后调用，失败静默）。

    P1-7（2026-09-16）：FAIL 必须闭环——把每条 FAIL 自查的处置（downweight/拦截）留痕，
    绝不只记日志：除 reflection_logs 外，对命中的问题记忆写 memory_write_receipts 降级回执。
    """
    if not result or not result.get("triggered"):
        return
    # 闭环：FAIL 处置留痕（降级回执，失败静默、不阻塞）
    if result.get("pass_or_fail") == "FAIL":
        try:
            from app.memory.receipt import ACTION_DOWNGRADE, emit_memory_receipt
            for chk in (result.get("checks") or {}).values():
                disp = (chk or {}).get("disposition")
                off = (chk or {}).get("offending_memory_id")
                if disp and off:
                    emit_memory_receipt(
                        character_id, off, ACTION_DOWNGRADE,
                        reason=f"reflection FAIL 闭环处置: {disp}",
                    )
        except Exception as e:
            _logger.warning("Reflection disposition receipt failed: %s", e)
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