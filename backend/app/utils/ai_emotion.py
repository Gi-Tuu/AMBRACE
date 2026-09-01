"""薄壳（F2-a，2026-08-31）：实现迁至 app/domain/emotion/model.py（八维→情感标签），旧路径保持兼容。"""
from app.domain.emotion.model import (  # noqa: F401
    ANGER_ANGER_THRESHOLD,
    FATIGUE_TIRED_THRESHOLD,
    MOOD_HAPPY_THRESHOLD,
    MOOD_SAD_THRESHOLD,
    emotion_from_character_states,
)
