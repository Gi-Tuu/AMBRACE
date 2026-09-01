"""emotion 域（F2-a，2026-08-31）：情绪模型与主动关心策略的唯一归属。

model=八维→情感标签 + 用户消息情绪提示（原 utils/ai_emotion + utils/emotion）；
care=用户低落时的延迟主动关心（原 scheduler/emotion_care）；
timeline=情绪时间线（原 services/emotion_timeline_service）。
对外门面：本包 re-export 核心函数；旧路径薄壳保持兼容。
"""
from app.domain.emotion.care import collect_care_events, register_care_task, run_emotion_care  # noqa: F401
from app.domain.emotion.model import detect_user_emotion, emotion_from_character_states  # noqa: F401
from app.domain.emotion.timeline import get_emotion_timeline  # noqa: F401

__all__ = [
    "emotion_from_character_states", "detect_user_emotion",
    "collect_care_events", "register_care_task", "run_emotion_care",
    "get_emotion_timeline",
]
