# -*- coding: utf-8 -*-
"""念头池 T2 纯函数域（A4 批 4 M0，2026-09-29）：抽取规则 / 入池三道过滤 / 双量动力学。

边界（强约束）：**零 IO、零 ORM、零 DB、零 flag、零网络、零域外业务 import**——三个子模块
只用 stdlib（``math``/``re``/``hashlib``/``datetime``）与本包内互相引用，只接受基础类型入参、
只返回基础类型。落库（表 ``thought_pool``）、三处抽取挂点、供给 prompt 与释放结算全部属于
M1/M2（设计 §7），本包一行都不碰；M0 唯一的消费者是离线回放脚本
``backend/scripts/thought_replay.py``。

口径来源：``AMBRACE_批4_念头池T2_详细设计_v1_20260929.md`` §2.1（六个来源面 F1–F6）、
§2.2（novelty/salt 公式、状态迁移表、挤出、三道过滤）、§2.3（三档释放）、
§3.2（与 life_share / unfinished_topic / 朋友圈原文的排他边界）。

与 ``domain/relational/drives.py``（批 3 T1 M1a）同级同风格。
"""
from __future__ import annotations

from app.domain.thought.dynamics import (
    ACTIVE_STATUSES,
    CAP_OBSESSION,
    CAP_SPARK,
    CAP_TOLD_FLAT,
    MAX_TELL_COUNT,
    MIN_DISTINCT_HIT_SOURCES,
    NOVELTY_HALFLIFE_DAYS,
    REPLY_WINDOW_MINUTES,
    SALT_BUMP_WEIGHT,
    SALT_OBSSESSION_THRESHOLD,
    SALT_TOLD_FLAT_RATIO,
    STATUS_FADED,
    STATUS_OBSESSION,
    STATUS_SPARK,
    STATUS_SPENT,
    STATUS_TOLD_FLAT,
    TTL_DAYS,
    age_days,
    now_utc,
    apply_told_flat,
    classify_release,
    evict,
    is_expired_told_flat,
    novelty,
    salt_of,
    should_fade,
    should_promote,
    strength,
)
from app.domain.thought.extract import (
    F1_ACTIVITY_TYPES,
    F2_MAX_CANDIDATES,
    F2_SUB_TYPES,
    F2_TRIGGER_WORDS,
    F3_ENGAGEMENT_WINDOW_DAYS, F3_TEXT_TEMPLATE,
    F4_IDLE_DAYS,
    F5_EPISTEMIC_ACCEPT,
    FACE_EXTRACTORS,
    SALT_WEIGHT_BY_SOURCE,
    SOURCE_TYPES,
    SRC_ACTIVITY,
    SRC_FACT,
    SRC_INTEREST,
    SRC_MOMENT,
    SRC_REFLECT,
    SRC_USER_HOOK,
    UNFINISHED_TOPIC_KEYWORDS,
    extract_activity,
    extract_fact,
    extract_interest,
    extract_moment,
    extract_reflection,
    extract_user_hook,
    normalized_length,
    normalize_text,
    split_candidates,
    text_hash,
)
from app.domain.thought.filters import (
    EPISTEMIC_BLOCKED,
    INTAKE_REASONS,
    REASON_FICTIONAL,
    REASON_LENGTH,
    REASON_RECENT_OVERLAP,
    RECENT_SENT_LOOKBACK,
    TEXT_MAX_LEN,
    TEXT_MIN_LEN,
    TOPIC_BUCKETS,
    filter_epistemic,
    filter_length,
    filter_recent_overlap,
    intake_reject_reason,
    topic_bucket,
)

__all__ = [
    # 来源面
    "SOURCE_TYPES", "SRC_ACTIVITY", "SRC_REFLECT", "SRC_MOMENT", "SRC_USER_HOOK",
    "SRC_FACT", "SRC_INTEREST", "SALT_WEIGHT_BY_SOURCE", "FACE_EXTRACTORS",
    "F1_ACTIVITY_TYPES", "F2_MAX_CANDIDATES", "F2_SUB_TYPES", "F2_TRIGGER_WORDS",
    "F3_ENGAGEMENT_WINDOW_DAYS", "F3_TEXT_TEMPLATE", "F4_IDLE_DAYS", "F5_EPISTEMIC_ACCEPT",
    "UNFINISHED_TOPIC_KEYWORDS",
    # 文本口径与抽取
    "normalize_text", "normalized_length", "text_hash", "split_candidates",
    "extract_activity", "extract_reflection", "extract_moment", "extract_user_hook",
    "extract_fact", "extract_interest",
    # 三道过滤
    "TEXT_MIN_LEN", "TEXT_MAX_LEN", "RECENT_SENT_LOOKBACK", "EPISTEMIC_BLOCKED",
    "TOPIC_BUCKETS", "INTAKE_REASONS", "REASON_LENGTH", "REASON_RECENT_OVERLAP",
    "REASON_FICTIONAL", "topic_bucket", "filter_length", "filter_recent_overlap",
    "filter_epistemic", "intake_reject_reason",
    # 动力学
    "NOVELTY_HALFLIFE_DAYS", "SALT_BUMP_WEIGHT", "SALT_OBSSESSION_THRESHOLD",
    "MIN_DISTINCT_HIT_SOURCES", "TTL_DAYS", "CAP_SPARK", "CAP_OBSESSION",
    "CAP_TOLD_FLAT", "MAX_TELL_COUNT", "SALT_TOLD_FLAT_RATIO", "REPLY_WINDOW_MINUTES",
    "ACTIVE_STATUSES", "STATUS_SPARK", "STATUS_OBSESSION", "STATUS_TOLD_FLAT",
    "STATUS_SPENT", "STATUS_FADED",
    "age_days", "novelty", "salt_of", "should_promote", "should_fade",
    "is_expired_told_flat", "apply_told_flat", "classify_release", "strength", "evict",
    "now_utc",
]
