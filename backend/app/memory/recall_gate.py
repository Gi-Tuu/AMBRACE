# -*- coding: utf-8 -*-
"""召回门（A4 批 2 / T4，P0：纯函数，只算不拦）。

设计红线（方案书 T4 边界 + 批 2 设计草案）：

- **轻量规则或极短判定**，不得把大模型生成放进关键路径；
- **判错默认放行**（宁多检索，不漏）；
- 纯函数、零 IO、零 LLM：只产出「要不要检索 / 为什么 / 置信度」，便于影子埋点与单测。

本模块只提供判定本身；接线分两步（P1 已于 2026-09-27 落地）：
- P1＝影子（只算 + 写一条 trace，不改变是否检索；开关 recall_gate_shadow 默认关）；
- P2＝生效（新开关【recall_gate】默认关；关=逐字节旧行为）。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.utils.logger import get_logger

_logger = get_logger("memory.recall_gate")

# P2 生效开关名（**本批只登记字符串**，不进 AGENT_FLAGS、不进目录、无调用点）
GATE_FLAG_KEY = "recall_gate"

REASON_CONTINUE = "continue"
REASON_EXTRA_QUERIES = "extra_queries"
REASON_TIME_PHRASE = "time_phrase"
REASON_LOOKUP_HINT = "lookup_hint"
REASON_SUBSTANTIVE = "substantive"
REASON_SMALL_TALK = "small_talk"
REASON_DEFAULT_PASS = "default_pass"

# 明确「不需要翻记忆」的短寒暄/应答（宁可少列，绝不把可能含信息的句子列进来）
_SMALL_TALK = frozenset({
    "嗯", "哦", "唔", "啊", "哈", "哈哈", "哈哈哈", "嘿嘿", "在吗", "在么", "在不在",
    "早", "早安", "晚安", "午安", "好的", "好吧", "好啊", "好", "行", "可以", "知道了",
    "收到", "谢谢", "谢谢你", "多谢", "拜拜", "再见", "么么", "么么哒", "抱抱", "亲亲",
    "嗯嗯", "哦哦", "哈哈好", "笑死", "好耶", "喜欢", "爱你",
})

# 「可能在问过去/问记忆」的线索词：命中即必须检索（宁多不漏）
_LOOKUP_HINTS = (
    "记不记得", "还记得", "记得吗", "想起来", "以前", "上次", "那天", "当时", "之前",
    "后来", "什么时候", "为什么", "怎么", "哪年", "哪个月", "那时候", "曾经", "早先",
    "你说过", "我说过", "我们", "约定", "答应", "？", "?",
)

_MIN_SUBSTANTIVE_CHARS = 8
_MAX_SMALL_TALK_CHARS = 2


@dataclass(frozen=True)
class GateDecision:
    """召回门判定：retrieve=是否检索；reason=判据；confidence=high(明确)/low(保守放行)。"""

    retrieve: bool
    reason: str
    confidence: str


def strip_noise(text: str) -> str:
    """去首尾空白与常见语气/标点噪声（不改变语义），供长度与寒暄判定用。"""
    t = (text or "").strip()
    for ch in ("~", "～", "!", "！", "。", "，", ",", ".", "…", " ", "\t"):
        t = t.strip(ch)
    return t


def is_emoji_or_symbol_only(text: str) -> bool:
    """是否只由 emoji / 符号 / 数字组成（没有任何中日韩或拉丁字母）。"""
    t = strip_noise(text)
    if not t:
        return True
    return not any(("\u4e00" <= ch <= "\u9fff") or ch.isalpha() for ch in t)


def is_small_talk(text: str) -> bool:
    """纯寒暄/应答（白名单精确匹配，或 ≤2 字的非疑问短句）。"""
    t = strip_noise(text)
    if not t:
        return True
    if t in _SMALL_TALK:
        return True
    if len(t) <= _MAX_SMALL_TALK_CHARS and not any(k in t for k in ("？", "?", "谁", "哪", "啥")):
        return True
    return False


def has_lookup_hint(text: str) -> bool:
    t = text or ""
    return any(k in t for k in _LOOKUP_HINTS)


def decide_retrieval(
    user_message: str,
    *,
    has_time_phrase: bool = False,
    has_extra_queries: bool = False,
    is_continue: bool = False,
) -> GateDecision:
    """判定本轮是否检索记忆（纯函数；规则顺序＝越靠前越"必须检索"）。

    顺序：继续指令 / 追加查询 / 时间短语 / 问记忆线索 / 长句 ⇒ 检索；
    只有「纯寒暄、纯符号 emoji、≤2 字且非疑问」才明确跳过；其余一律放行（保守）。
    """
    if is_continue:
        return GateDecision(True, REASON_CONTINUE, "high")
    if has_extra_queries:
        return GateDecision(True, REASON_EXTRA_QUERIES, "high")
    if has_time_phrase:
        return GateDecision(True, REASON_TIME_PHRASE, "high")
    t = strip_noise(user_message)
    if has_lookup_hint(t):
        return GateDecision(True, REASON_LOOKUP_HINT, "high")
    if len(t) >= _MIN_SUBSTANTIVE_CHARS:
        return GateDecision(True, REASON_SUBSTANTIVE, "high")
    if is_emoji_or_symbol_only(t) or is_small_talk(t):
        return GateDecision(False, REASON_SMALL_TALK, "high")
    return GateDecision(True, REASON_DEFAULT_PASS, "low")


# ────────────────────────────── P1 影子留痕（2026-09-27） ──────────────────────────────

SHADOW_FLAG_KEY = "recall_gate_shadow"
SHADOW_ROUTE = SHADOW_FLAG_KEY          # agent_task_logs.route（String(30)，本值 18 字符）
SHADOW_TRIGGER = "recall_gate"
_MSG_MAX = 120
_STEPS_MAX = 1200


def shadow_enabled() -> bool:
    """影子留痕总闸（缺省关；连导入都失败也按关处理——观测层不得把业务拖下水）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(SHADOW_FLAG_KEY, False))
    except Exception:
        return False


def plan_shadow_record(user_message, *, has_time_phrase=False, has_extra_queries=False,
                       is_continue=False, hit_count=0) -> dict:
    """影子记录体（纯函数、零 IO）：门的判定 ＋ 与实际检索结果的对照，供事后判效。

    - would_lose：门说「不用检索」但实际检索**命中了内容** ⇒ 门若生效会漏（越低越好）；
    - wasted：门说「要检索」但实际**空手**（无效检索，供上下文成本口径参考）。
    """
    d = decide_retrieval(
        user_message,
        has_time_phrase=has_time_phrase,
        has_extra_queries=has_extra_queries,
        is_continue=is_continue,
    )
    hits = int(hit_count or 0)
    return {
        "retrieve": d.retrieve,
        "reason": d.reason,
        "confidence": d.confidence,
        "hit_count": hits,
        "would_lose": (not d.retrieve) and hits > 0,
        "wasted": d.retrieve and hits == 0,
        "msg": (user_message or "")[:_MSG_MAX],
        "has_time_phrase": bool(has_time_phrase),
        "has_extra_queries": bool(has_extra_queries),
        "is_continue": bool(is_continue),
    }


def observe_retrieval_decision(user_message, *, has_time_phrase=False, has_extra_queries=False,
                               is_continue=False, hit_count=0, character_id=None,
                               user_id=None, task_id=None) -> None:
    """P1 影子挂点：**只留痕、不改变是否检索**（调用方照旧照常检索；本函数无返回值）。

    - 关：首行即返回 ⇒ 不算判定、不建记录、不碰 IO，与「根本没接这层」逐字一致；
    - 开：算一次门判定并写一条 agent_task_logs（route = SHADOW_ROUTE）；
    - fail-open：整体包 try，留痕失败只 WARNING，绝不影响主链路。
    """
    if not shadow_enabled():
        return
    try:
        import json

        from app.agent.trace import enqueue_task_log, new_task_id
        record = plan_shadow_record(
            user_message,
            has_time_phrase=has_time_phrase,
            has_extra_queries=has_extra_queries,
            is_continue=is_continue,
            hit_count=hit_count,
        )
        enqueue_task_log(
            task_id=task_id or new_task_id(),
            character_id=character_id,
            user_id=user_id,
            trigger=SHADOW_TRIGGER,
            route=SHADOW_ROUTE,
            steps_json=json.dumps(record, ensure_ascii=False, default=str)[:_STEPS_MAX],
            latency_ms=0,
            status="ok",
        )
    except Exception as e:
        _logger.warning("Recall gate shadow record failed: %s", e)
