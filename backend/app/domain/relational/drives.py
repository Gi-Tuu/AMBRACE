# -*- coding: utf-8 -*-
"""关系驱力纯函数域（A4 批3 T1 M1a，2026-09-27）：水位增长 / 封顶 / 夜间倍率 / 释放 / 候选。

边界（强约束）：**零 IO、零 ORM、零 DB、零 flag、零网络**——只接受基础类型入参、只返回基础
类型。落库（表 ``relational_drives``）与三处 settle/释放钩子属调用方（下一单 M1b），本模块
一行都不碰。与 ``domain/proactivity/outreach.py`` 同级同风格。

口径来源：AMBRACE_批3_T1关系驱力层_详细设计_v1_20260927.md §3.1（参数表与懒结算）、§3.2
（有界释放）、§3.3（intent↔drive 映射）、§⑧ 第 4 条（intimacy 不进候选）。

⚠️ 下面三张参数表全部是**初值，必须实测回标定**（设计 §⑥R3：未经回标定直接生效属「水位
失真」风险——长期贴 0 或贴 100）。0.090/小时 ⇒ 满 100 需约 46 天，因此封顶与夜间倍率兜底
是必需的，不是可选修饰。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.domain.proactivity.outreach import (
    CHECK_IN,
    FOLLOW_UP,
    INTEREST_HOOK,
    RECALL_SHARED,
    SHARE_SELF,
)
from app.utils.timeutil import shift_utc_naive, to_naive_utc

# ── 六个驱力键（固定顺序：并列时按此序取第一个，见 top_candidate_drive）──
DRIVE_LONGING = "longing"        # 想念
DRIVE_CONCERN = "concern"        # 牵挂
DRIVE_AFFECTION = "affection"    # 亲昵
DRIVE_SHARING = "sharing"        # 分享欲
DRIVE_CURIOSITY = "curiosity"    # 好奇
DRIVE_INTIMACY = "intimacy"      # 亲密：可观测，**永不进主动候选**（设计 §⑧ 第 4 条）

DRIVE_ALL_KEYS: tuple[str, ...] = (
    DRIVE_LONGING, DRIVE_CONCERN, DRIVE_AFFECTION, DRIVE_SHARING, DRIVE_CURIOSITY, DRIVE_INTIMACY,
)
# 主动候选只从前 5 个里取（不含 intimacy）
DRIVE_CANDIDATE_KEYS: tuple[str, ...] = DRIVE_ALL_KEYS[:5]

# ── 增长：每 60 分钟未经互动的增量（单位＝水位/小时）──
# 初值，必须实测回标定（设计 §3.1 参数表）
DRIVE_GROWTH_PER_HOUR: dict[str, float] = {
    DRIVE_LONGING: 0.090,
    DRIVE_CONCERN: 0.075,
    DRIVE_AFFECTION: 0.060,
    DRIVE_SHARING: 0.050,
    DRIVE_CURIOSITY: 0.035,
    DRIVE_INTIMACY: 0.020,
}

# ── 夜间倍率（作用于落在夜间的时段；初值，必须实测回标定）──
DRIVE_NIGHT_MULTIPLIER: dict[str, float] = {
    DRIVE_LONGING: 0.6,
    DRIVE_CONCERN: 1.0,
    DRIVE_AFFECTION: 1.0,
    DRIVE_SHARING: 1.0,
    DRIVE_CURIOSITY: 1.0,
    DRIVE_INTIMACY: 0.4,
}

# ── 单次开口可释放比例（部分释放，余量留下；初值，必须实测回标定）──
DRIVE_OPEN_RELEASE_RATIO: dict[str, float] = {
    DRIVE_LONGING: 0.35,
    DRIVE_CONCERN: 0.70,
    DRIVE_AFFECTION: 0.40,
    DRIVE_SHARING: 0.45,
    DRIVE_CURIOSITY: 0.50,
    DRIVE_INTIMACY: 0.18,
}

LEVEL_MAX = 100.0  # 与八维情绪同量纲，便于 prompt 说「很惦记」这类措辞

# ── 夜间时段：北京时间 [23:00, 07:00) ──
# 设计里「夜间」原本**未定义**，本单定为与 ``pacing.HOUR_WINDOW_END=23`` 的活动窗口互补的
# [23:00, 07:00)；口径待回标定（是否等于免打扰时段、是否该按角色本地时区，另议）。
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 7

# 库内时间统一 UTC naive，北京时间 = UTC+8（项目口径，见 app/utils/timeutil.py 文件头）
_BJ_TZ_OFFSET_HOURS = 8

# ── intent ↔ drive 静态映射（确定性、零 LLM；设计 §3.3）──
# 意图常量直接引用 outreach 的既有定义（单一事实源，不改 outreach 任何常量）。
# RECALL_SHARED（共同回忆）↔ affection：口径按设计 §3.3 表——「还记得我们那回」靠亲昵驱动。
DRIVE_TO_INTENT: dict[str, str] = {
    DRIVE_LONGING: CHECK_IN,
    DRIVE_CONCERN: FOLLOW_UP,
    DRIVE_SHARING: SHARE_SELF,
    DRIVE_CURIOSITY: INTEREST_HOOK,
    DRIVE_AFFECTION: RECALL_SHARED,
}
INTENT_TO_DRIVE: dict[str, str] = {intent: drive for drive, intent in DRIVE_TO_INTENT.items()}


def is_night_hour(hour_bj: int) -> bool:
    """北京时区小时（0–23）是否落在夜间窗口 [NIGHT_START_HOUR, NIGHT_END_HOUR)（跨零点）。"""
    h = int(hour_bj) % 24
    if NIGHT_START_HOUR > NIGHT_END_HOUR:  # 跨零点窗口：[23,24) ∪ [0,7)
        return h >= NIGHT_START_HOUR or h < NIGHT_END_HOUR
    return NIGHT_START_HOUR <= h < NIGHT_END_HOUR


def _to_float(value) -> float:
    """脏值（None/非数字）按 0 处理——水位算法绝不因单行脏数据抛错（调用方在异步路径上）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def settle_level(
    level: float,
    drive_key: str,
    last_settled_at: datetime | None,
    now: datetime,
) -> tuple[float, datetime]:
    """懒结算：把 [last_settled_at, now) 的静默时长折算成增量，返回 (新水位, 新游标)。

    - 按**北京时区的整点边界**切段，每段乘该段所属时段的夜间倍率（白天 1.0）；
      跨日/跨月因此在 UTC 轴上按北京时间小时对齐，不假设「一小时＝同一倍率」；
    - 结果封顶 ``LEVEL_MAX``（长静默不无限累积）；
    - ``last_settled_at is None``（首次建行）⇒ 增量 0、游标＝now，不从零时刻补算历史；
    - ``now <= last_settled_at``（时钟回拨/脏数据）⇒ 原值返回、游标**不前进**；
    - 未知 ``drive_key`` ⇒ 增速按 0（水位不变，游标照常推进，避免每次重算同一段）；
    - 幂等：同一 (游标, now) 反复调用，第二次起游标已＝now ⇒ 增量为 0。

    入参为 naive UTC（库内约定）；带 tzinfo 的先归一为 naive UTC，两种口径都不会算错段。
    """
    cursor = to_naive_utc(last_settled_at)
    moment = to_naive_utc(now)
    if cursor is None:
        return (_to_float(level), moment)
    if moment <= cursor:
        return (_to_float(level), cursor)

    growth = DRIVE_GROWTH_PER_HOUR.get(drive_key, 0.0)
    night_mult = DRIVE_NIGHT_MULTIPLIER.get(drive_key, 1.0)
    one_hour = timedelta(hours=1)

    increment = 0.0
    cur = cursor
    while cur < moment:
        cur_bj = shift_utc_naive(cur, _BJ_TZ_OFFSET_HOURS)
        boundary_utc = shift_utc_naive(
            cur_bj.replace(minute=0, second=0, microsecond=0) + one_hour, -_BJ_TZ_OFFSET_HOURS
        )
        seg_end = moment if moment < boundary_utc else boundary_utc
        hours = (seg_end - cur).total_seconds() / 3600.0
        factor = night_mult if is_night_hour(cur_bj.hour) else 1.0
        increment += hours * growth * factor
        cur = seg_end

    return (min(LEVEL_MAX, _to_float(level) + increment), moment)


def release_open(level: float, drive_key: str) -> float:
    """开口部分释放：返回**释放后剩下的水位**（``level × (1 − 可释放比例)``，下限 0）。

    即「没聊开就继续惦记」：余量留下、下次开口自然回到那件事。未知 ``drive_key`` ⇒ 比例按 0
    （不释放，水位原样保留），宁可不释放也不把未知键的水位抹平。
    """
    ratio = DRIVE_OPEN_RELEASE_RATIO.get(drive_key, 0.0)
    return max(0.0, _to_float(level) * (1.0 - ratio))


def release_full(level: float) -> float:
    """互动全额释放：用户真实回复 ⇒ 该驱力清零（返回 0.0，与入参水位无关）。"""
    return 0.0


def top_candidate_drive(levels: dict[str, float]) -> str | None:
    """取本次定调的驱力：只在 ``DRIVE_CANDIDATE_KEYS``（前 5，**不含 intimacy**）里取最高。

    - 并列按 ``DRIVE_ALL_KEYS`` 固定顺序取第一个（候选键是其前缀，故等价于按该顺序扫描）；
    - 非候选键（含 intimacy）、非正数（<=0）、脏值一律不参与；
    - 空 dict / 全 0 ⇒ ``None``＝无驱力，回落现有加权随机（设计 §3.2「失败与回退姿态」）。
    """
    best_key: str | None = None
    best_value = 0.0
    for key in DRIVE_CANDIDATE_KEYS:
        value = _to_float(levels.get(key))
        if value > best_value:
            best_key, best_value = key, value
    return best_key
