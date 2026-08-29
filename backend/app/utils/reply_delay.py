"""动态回复延迟（#63 机制2，Flag：reply_delay_enabled）。

纯函数，无 IO：根据角色当前状态（疲惫/情绪/怒气）与回复长度估算"输入中..."的
延迟秒数，让 mood/fatigue/anger 影响回复节奏：开心短句秒回、疲惫长句慢回、生气犹豫。

- `estimate_response_chars(user_msg_len)`：按用户消息长度粗估回复长度（字符数）；
- `calc_typing_delay(...)`：基础 0.8s × 疲惫/情绪/怒气/长度系数 × 随机 ±25%，封顶 8s。

纯函数可注入 `rng` 以便固定 seed 单测；业务侧失败一律静默（不阻塞回复）。
"""
from __future__ import annotations

import random

# 基础回复延迟（秒）
BASE_DELAY = 0.8
# 封顶延迟（秒）
MAX_DELAY = 8.0
# 随机波动幅度：±25%
RANDOM_SPREAD = 0.25


def estimate_response_chars(user_msg_len: int) -> int:
    """按用户消息长度粗估回复长度（字符数）。

    经验分档：短句（≤4）→ 短回复；中等 → 中长回复；很长 → 长回复。
    """
    n = max(0, int(user_msg_len or 0))
    if n <= 4:
        return 12
    if n <= 12:
        return 30
    if n <= 30:
        return 70
    if n <= 80:
        return 120
    return 200


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, float(v or 50)))


def calc_typing_delay(
    response_estimate_chars: int,
    mood: float = 50.0,
    fatigue: float = 50.0,
    anger: float = 50.0,
    is_short_reply: bool = False,
    rng: random.Random | None = None,
) -> float:
    """计算动态回复延迟（秒），纯函数。

    系数均为中性值（各维 50）时为 1.0，保证默认行为接近基础 0.8s：
    - 疲惫（fatigue）：0.75~1.25（越高越慢）；
    - 心情（mood）：0.5~1.5（越低越慢，开心加快）；
    - 怒气（anger）：0.75~1.25（越高越犹豫）；
    - 长度（response_estimate_chars）：短句 0.85、中型 1.0、长 1.25、更长 1.5；
      用户短消息（is_short_reply）额外压到 ≤0.85。
    结果再乘随机系数（1±RANDOM_SPREAD），并封顶 MAX_DELAY。
    """
    mood = _clamp(mood)
    fatigue = _clamp(fatigue)
    anger = _clamp(anger)
    ch = max(0, int(response_estimate_chars or 0))

    fatigue_factor = 0.75 + (fatigue / 100.0) * 0.5   # 0.75..1.25
    mood_factor = 1.5 - (mood / 100.0) * 1.0          # 0.5..1.5
    anger_factor = 0.75 + (anger / 100.0) * 0.5       # 0.75..1.25

    if ch >= 150:
        length_factor = 1.5
    elif ch >= 80:
        length_factor = 1.25
    elif ch >= 30:
        length_factor = 1.0
    else:
        length_factor = 0.85
    if is_short_reply:
        length_factor = min(length_factor, 0.85)

    delay = BASE_DELAY * fatigue_factor * mood_factor * anger_factor * length_factor
    srng = rng or random
    delay *= srng.uniform(1.0 - RANDOM_SPREAD, 1.0 + RANDOM_SPREAD)
    return round(min(MAX_DELAY, max(0.0, delay)), 2)
