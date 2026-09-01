"""薄壳（F2-a，2026-08-31）：实现迁至 app/domain/emotion/care.py，旧路径保持兼容。"""
from app.domain.emotion.care import (  # noqa: F401
    CARE_TYPE,
    DELAY_MAX_MINUTES,
    DELAY_MIN_MINUTES,
    MAX_PER_DAY,
    TASK_TTL_HOURS,
    collect_care_events,
    register_care_task,
    run_emotion_care,
)
