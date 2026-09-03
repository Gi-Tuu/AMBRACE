# -*- coding: utf-8 -*-
"""中文时间表达 → [start, end) naive-UTC 区间（Ariadne 模块 A，2026-09-03）。

纯函数、零 LLM、零 DB，便于单测；只处理高置信规则，识别不了返回 None（绝不猜）。
覆盖：今天/昨天/前天/本周/上周/这月/上月/去年/YYYY-MM/YYYY年M月，以及
「刚认识/最早/一开始/那阵/那时候」等模糊早期表达（宽窗口，交给重要度排序收窄）。

flag：memory_temporal_recall（默认关）——调用方在 flag 开时才把解析结果传给检索层，
本模块本身不读 flag（保持纯函数）。区间一律返回 [start, end) naive-UTC。

时区口径（F-3，2026-09-04）：now 为 UTC naive；调用方可传入用户本地时区分钟偏移
``tz_offset_min``（如 480=UTC+8）。此时「今天/昨天/前天/本周/上周/这月/上月」先按**用户本地自然日/
周/月**取边界，再折回 UTC 区间（本地自然日 → UTC 区间），修复 UTC+8 用户在本地 00:00–08:00
之间「今天/昨天」被按 UTC 错分一天的问题。``tz_offset_min=None`` 时按 UTC 切日，与旧行为
逐字节一致（offset 视为 0，安全灰度/回归）。绝对年月（2026-07 / 2026年7月）与「去年」按
绝对自然月/年，不受时区影响（二跳 [RECALL 时间=YYYY-MM] 同口径不改）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.utils.timeutil import now_naive_utc

_ABS_RE = re.compile(r"(20\d{2})\s*[-年/.]\s*(\d{1,2})")
_EARLY_RE = re.compile(r"刚认识|最早|一开始|起初|那阵|那时候")
_WEEK_RE = re.compile(r"本周|这周|这一周")
_LASTWEEK_RE = re.compile(r"上周")
_THISMONTH_RE = re.compile(r"这月|这个月|本月")
_LASTMONTH_RE = re.compile(r"上月|上个月")


def _day(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d)


def _local_day_bounds(now_utc: datetime, offset_min: int, day_delta: int = 0) -> tuple[datetime, datetime]:
    """返回用户本地某自然日（day_delta：0 今天 / -1 昨天 / -2 前天）对应的 UTC naive [起,止) 区间。

    口径：把 UTC 时刻加 offset 得到本地墙钟，取本地当天 00:00 再减 offset 折回 UTC。
    ``offset_min=0`` 即 UTC 切日，与旧行为逐字节一致。
    """
    local = now_utc + timedelta(minutes=offset_min) + timedelta(days=day_delta)
    local_start = datetime(local.year, local.month, local.day)
    utc_start = local_start - timedelta(minutes=offset_min)
    return utc_start, utc_start + timedelta(days=1)


def _local_week_bounds(now_utc: datetime, offset_min: int, week_delta: int = 0) -> tuple[datetime, datetime]:
    """返回用户本地某周（week_delta：0 本周 / -1 上周）周一起的 UTC naive 7 天 [起,止) 区间。"""
    local = now_utc + timedelta(minutes=offset_min)
    monday0 = local - timedelta(days=local.weekday()) + timedelta(days=7 * week_delta)
    monday0 = datetime(monday0.year, monday0.month, monday0.day)
    utc_start = monday0 - timedelta(minutes=offset_min)
    return utc_start, utc_start + timedelta(days=7)


def _local_month_bounds(now_utc: datetime, offset_min: int, month_delta: int = 0) -> tuple[datetime, datetime]:
    """返回用户本地某月（month_delta：0 本月 / -1 上月）首日折回 UTC 的 naive [起,止) 区间。"""
    local = now_utc + timedelta(minutes=offset_min)
    total = local.year * 12 + (local.month - 1) + month_delta
    y0, m0 = divmod(total, 12)
    y1, m1 = divmod(total + 1, 12)
    return (
        datetime(y0, m0 + 1, 1) - timedelta(minutes=offset_min),
        datetime(y1, m1 + 1, 1) - timedelta(minutes=offset_min),
    )


def parse_time_range(
    text: str,
    now: datetime | None = None,
    tz_offset_min: int | None = None,
) -> tuple[datetime, datetime] | None:
    """把中文时间表达解析为 [起, 止) naive-UTC 区间；识别不了返回 None。

    - 绝对年月（2026-07 / 2026年7月）→ 该自然月窗口（不受时区影响）；
    - 今天/昨天/前天 → 单日窗口；本周/上周 → 周一起 7 天窗口；这月/上月 → 自然月窗口；
    - 去年 → 去年全年；
    - 模糊早期（刚认识/最早/一开始/起初/那阵/那时候）→ [now-3650d, now-30d) 宽窗口
      （「按该角色最早记忆动态收窄」由调用方做，此处保持纯函数）。

    ``tz_offset_min``：用户本地时区分钟偏移（如 480=UTC+8）。非 None 时相对日/周/月按
    **用户本地自然日/周/月**取边界再折回 UTC（修复 UTC+8 凌晨「今天/昨天」按 UTC 错分一天）；
    None 时按 UTC 切日（offset 视为 0），与旧行为逐字节一致（安全灰度）。
    """
    if not text:
        return None
    t = text
    now = now or now_naive_utc()
    offset = tz_offset_min if tz_offset_min is not None else 0

    m = _ABS_RE.search(t)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            start = _day(y, mo, 1)
            end = _day(y + (mo // 12), (mo % 12) + 1, 1)
            return start, end
        return None  # 月份数字非法（如 2026-13）→ 不猜

    if "去年" in t:
        y = now.year - 1
        return _day(y, 1, 1), _day(y + 1, 1, 1)

    if "前天" in t:
        return _local_day_bounds(now, offset, -2)

    if "昨天" in t:
        return _local_day_bounds(now, offset, -1)

    if "今天" in t:
        return _local_day_bounds(now, offset, 0)

    if _LASTWEEK_RE.search(t):
        return _local_week_bounds(now, offset, -1)

    if _WEEK_RE.search(t):
        return _local_week_bounds(now, offset, 0)

    if _LASTMONTH_RE.search(t):
        return _local_month_bounds(now, offset, -1)

    if _THISMONTH_RE.search(t):
        return _local_month_bounds(now, offset, 0)

    if _EARLY_RE.search(t):
        return now - timedelta(days=3650), now - timedelta(days=30)

    return None
