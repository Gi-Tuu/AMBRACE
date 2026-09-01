"""轻量角色情感规则器（P2-1，零 LLM、零 token）。

根据角色八维状态（mood/anger/fatigue 0-100，可缺省）映射出可传给 TTS 的
情感标签，与 Phase 0 P0 的 emotion_edge_adjust 标签集对齐：
```text
sad / happy / excited / calm / tired / angry 之一，或空串""。
```

本模块**只做规则映射、不做 LLM 判定**（项目铁律：不新增主链路 LLM 调用）。
任何不可解析的输入 / 异常一律返回 ""（空串），保证零行为变化、不抛断主链路。

映射建议（阈值常量集中在此，可调）：
- mood < 40   → sad（低落）
- anger > 70  → angry（生气）
- fatigue > 70 → tired（疲惫）
- mood > 70   → happy（开心）
- 其余         → ""（中性/平静，TTS 零行为变化）

冲突时按 _PRECEDENCE 从高到低取一个（angry > sad > tired > happy）。
可选 user_emotion_hint 仅作「负向抑制」：用户明显低落/生气/疲惫时，不让 AI 用
happy 这类正向音色，避免语气错位；绝不凭空把中性情绪升级为负向标签。
"""

from __future__ import annotations

# 阈值常量（集中可调）
MOOD_SAD_THRESHOLD = 40.0       # mood < 40 → sad
ANGER_ANGER_THRESHOLD = 70.0    # anger > 70 → angry
FATIGUE_TIRED_THRESHOLD = 70.0  # fatigue > 70 → tired
MOOD_HAPPY_THRESHOLD = 70.0     # mood > 70 → happy

# 多标签冲突时的优先级（从高到低）
_PRECEDENCE = ("angry", "sad", "tired", "happy")

# 用户负向情绪关键词（对 user_emotion_hint 的宽松判定，仅用于抑制正向音色）
_NEGATIVE_USER_KEYWORDS = (
    "低落", "难过", "伤心", "委屈", "崩溃", "想哭", "呜呜", "心烦", "心累",
    "难受", "压力", "焦虑", "失眠", "生气", "愤怒", "烦死了", "好累", "好烦",
    "疲惫", "累", "糟", "倒霉", "受伤", "失落", "沮丧", "郁闷",
)


def _to_int(value, default: int = 50) -> int:
    """转成 0-100 整数，失败/缺省返回 default（缺省安全）。"""
    try:
        iv = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(0, min(100, iv))


def emotion_from_character_states(
    character_states: dict | None = None,
    *,
    mood: float | int | None = None,
    anger: float | int | None = None,
    fatigue: float | int | None = None,
    user_emotion_hint: str = "",
) -> str:
    """由角色八维状态推导 TTS 情感标签；失败/缺省返回 ""（零行为变化）。

    可传入整份 character_states（dict，含 mood/anger/fatigue，可缺省），
    也可直接以关键字 mood/anger/fatigue 传入覆盖值（用于单测/无 DB 场景）。
    user_emotion_hint：可选用户情绪提示/ASR 文本；仅用于抑制正向音色。
    """
    st = character_states or {}
    m_mood = _to_int(mood if mood is not None else st.get("mood"))
    m_anger = _to_int(anger if anger is not None else st.get("anger"))
    m_fatigue = _to_int(fatigue if fatigue is not None else st.get("fatigue"))

    candidates = []
    if m_mood < MOOD_SAD_THRESHOLD:
        candidates.append("sad")
    if m_anger > ANGER_ANGER_THRESHOLD:
        candidates.append("angry")
    if m_fatigue > FATIGUE_TIRED_THRESHOLD:
        candidates.append("tired")
    if m_mood > MOOD_HAPPY_THRESHOLD:
        candidates.append("happy")

    label: str | None = None
    for name in _PRECEDENCE:
        if name in candidates:
            label = name
            break

    # 用户负向情绪时，抑制正向音色（避免语气错位），但不凭空捏造负向标签
    if label in ("happy", "excited") and _user_emotion_negative(user_emotion_hint):
        label = None
    return label or ""


def _user_emotion_negative(hint: str) -> bool:
    """宽松判定用户情绪提示是否偏负向（命中任意关键词即 True）。"""
    text = (hint or "").strip()
    if not text:
        return False
    return any(kw in text for kw in _NEGATIVE_USER_KEYWORDS)


__all__ = [
    "emotion_from_character_states",
    "MOOD_SAD_THRESHOLD",
    "ANGER_ANGER_THRESHOLD",
    "FATIGUE_TIRED_THRESHOLD",
    "MOOD_HAPPY_THRESHOLD",
]
