"""情绪域模型（F2-a，2026-08-31 合并）：八维→情感标签（原 utils/ai_emotion）+ 用户消息情绪提示（原 utils/emotion）。

两模块原样合并；旧路径保留薄壳 re-export。关系标量/八维状态存储见 app/services/character_state_service（应用层）。
"""
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


# ── 用户消息情绪规则器（原 utils/emotion.py，原样并入）──

def detect_user_emotion(user_msg: str) -> str:
    """返回一句中文提示（如"低落需要安慰"），无可识别情绪返回空串。"""
    msg = (user_msg or "").strip()
    if not msg:
        return ""
    # 困惑：连续问号或开头"为什么"
    if "？？" in msg or "??" in msg or "？" * 3 in msg or "?" * 3 in msg:
        return "困惑/不耐烦（先直接回答，别绕弯子）"
    # 低落需要安慰
    if any(kw in msg for kw in (
        "好累", "难过", "想哭", "呜呜", "崩溃", "委屈", "心烦", "好烦", "心累", "难受", "失落", "受伤",
        "心情好差", "心情不好", "压力好大", "舍不得", "烦死了", "郁闷", "倒霉", "心态崩",
        "挫败", "失眠", "沮丧", "低落", "焦虑",
    )):
        return "低落需要安慰（多共情、少讲道理，语气温柔）"
    # 开心
    if any(kw in msg for kw in ("哈哈", "嘿嘿", "笑死", "太棒", "开心", "太好了", "耶", "嘻嘻")):
        return "心情不错（语气可以轻松活泼些）"
    # 激动：感叹号密集
    if msg.count("!") + msg.count("！") >= 3:
        return "情绪激动（先接住情绪，别急着讲道理）"
    # 开场/打招呼词：不算结束信号
    if msg.strip("！!。.？?~～ ") in ("在吗", "在么", "在不在", "嗨", "哈喽", "hello", "hi", "在", "早", "早上好", "中午好", "晚上好", "睡了吗"):
        return ""
    # 长篇倾诉
    if len(msg) >= 120:
        return "长篇倾诉（认真读完、逐点回应，别敷衍）"
    # 敷衍/结束信号：短且无情绪词
    if len(msg) <= 6:
        return "简短回应（大概率是结束信号，别硬找话题，回复短一点）"
    return ""
