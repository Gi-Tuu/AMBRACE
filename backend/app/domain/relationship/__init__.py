"""relationship 域（F2-a，2026-08-31）：信任/亲密度等关系标量的计算与衰减。

唯一归属：本包。decay=长期不互动的标量衰减（原 scheduler/relationship_decay）。
对外门面：`from app.domain.relationship import run_relationship_decay`。
边界：情绪归 emotion 包；八维状态存储在 character_states 表（应用层服务读写）。
"""
from app.domain.relationship.decay import (  # noqa: F401
    DAILY_DECAY_STEP,
    IDLE_DAYS_THRESHOLD,
    RELATION_MIN,
    run_relationship_decay,
)

__all__ = ["run_relationship_decay", "IDLE_DAYS_THRESHOLD", "DAILY_DECAY_STEP", "RELATION_MIN"]
