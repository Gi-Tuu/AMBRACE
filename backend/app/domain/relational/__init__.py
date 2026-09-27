"""relational 域（A4 批3 T1 M1a，2026-09-27）：指向用户的**类型化冲动水位**的算法层。

唯一归属：本包。drives.py = 六驱力的增长/封顶/夜间倍率/释放/候选（纯计算，零 IO）。
边界：
- 八维情绪与角色级关系标量归 ``domain/emotion`` / ``domain/relationship``（本包**不反向写**
  character_states，也不参与 life 需求结算——生活需求是 AI 自满足，与「想跟谁说话」无关）；
- 水位的**存储**在 ``relational_drives`` 表（模型 ``app/models/character``），DB 读写与
  settle/释放钩子留在调用方（scheduling / application 层），本包一行 IO 都没有；
- 意图常量仍由 ``domain/proactivity/outreach`` 独家定义，本包只做 intent↔drive 的静态映射。
对外门面：``from app.domain.relational import settle_level, top_candidate_drive``。
"""
from app.domain.relational.drives import (  # noqa: F401
    DRIVE_AFFECTION,
    DRIVE_ALL_KEYS,
    DRIVE_CANDIDATE_KEYS,
    DRIVE_CONCERN,
    DRIVE_CURIOSITY,
    DRIVE_GROWTH_PER_HOUR,
    DRIVE_INTIMACY,
    DRIVE_LONGING,
    DRIVE_NIGHT_MULTIPLIER,
    DRIVE_OPEN_RELEASE_RATIO,
    DRIVE_SHARING,
    DRIVE_TO_INTENT,
    INTENT_TO_DRIVE,
    LEVEL_MAX,
    NIGHT_END_HOUR,
    NIGHT_START_HOUR,
    is_night_hour,
    release_full,
    release_open,
    settle_level,
    top_candidate_drive,
)

__all__ = [
    "DRIVE_AFFECTION",
    "DRIVE_ALL_KEYS",
    "DRIVE_CANDIDATE_KEYS",
    "DRIVE_CONCERN",
    "DRIVE_CURIOSITY",
    "DRIVE_GROWTH_PER_HOUR",
    "DRIVE_INTIMACY",
    "DRIVE_LONGING",
    "DRIVE_NIGHT_MULTIPLIER",
    "DRIVE_OPEN_RELEASE_RATIO",
    "DRIVE_SHARING",
    "DRIVE_TO_INTENT",
    "INTENT_TO_DRIVE",
    "LEVEL_MAX",
    "NIGHT_END_HOUR",
    "NIGHT_START_HOUR",
    "is_night_hour",
    "release_full",
    "release_open",
    "settle_level",
    "top_candidate_drive",
]
