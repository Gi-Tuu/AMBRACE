"""薄壳（F2-a，2026-08-31）：实现迁至 app/domain/relationship/decay.py，旧路径保持兼容。"""
from app.domain.relationship.decay import (  # noqa: F401
    DAILY_DECAY_STEP,
    IDLE_DAYS_THRESHOLD,
    RELATION_MIN,
    run_relationship_decay,
)
