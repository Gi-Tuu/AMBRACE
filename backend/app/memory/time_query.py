# -*- coding: utf-8 -*-
"""中文时间表达 → [start, end) naive-UTC 区间（Ariadne 模块 A，2026-09-03）。

纯函数、零 LLM、零 DB，便于单测；只处理高置信规则，识别不了返回 None（绝不猜）。
覆盖：今天/昨天/前天/本周/上周/这月/上月/去年/YYYY-MM/YYYY年M月，以及
「刚认识/最早/一开始/那阵/那时候」等模糊早期表达（宽窗口，交给重要度排序收窄）。

flag：memory_temporal_recall（默认关）——调用方在 flag 开时才把解析结果传给检索层，
本模块本身不读 flag（保持纯函数）。时区口径：UTC naive（项目铁律），now 可注入便于测试。
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


def parse_time_range(text: str, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """把中文时间表达解析为 [起, 止) naive-UTC 区间；识别不了返回 None。

    - 绝对年月（2026-07 / 2026年7月）→ 该自然月窗口；
    - 今天/昨天/前天 → 单日窗口；
    - 本周/上周 → 周一起 7 天窗口；
    - 这月/上月 → 自然月窗口；
    - 去年 → 去年全年；
    - 模糊早期（刚认识/最早/一开始/起初/那阵/那时候）→ [now-3650d, now-30d) 宽窗口
      （「按该角色最早记忆动态收窄」由调用方做，此处保持纯函数）。
    """
    if not text:
        return None
    t = text
    now = now or now_naive_utc()

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
        d = (now - timedelta(days=2)).date()
        start = _day(d.year, d.month, d.day)
        return start, start + timedelta(days=1)

    if "昨天" in t:
        d = (now - timedelta(days=1)).date()
        start = _day(d.year, d.month, d.day)
        return start, start + timedelta(days=1)

    if "今天" in t:
        d = now.date()
        start = _day(d.year, d.month, d.day)
        return start, start + timedelta(days=1)

    if _LASTWEEK_RE.search(t):
        monday = (now - timedelta(days=now.weekday())).date() - timedelta(days=7)
        start = _day(monday.year, monday.month, monday.day)
        return start, start + timedelta(days=7)

    if _WEEK_RE.search(t):
        monday = (now - timedelta(days=now.weekday())).date()
        start = _day(monday.year, monday.month, monday.day)
        return start, start + timedelta(days=7)

    if _LASTMONTH_RE.search(t):
        y, mo = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        return _day(y, mo, 1), _day(now.year, now.month, 1)

    if _THISMONTH_RE.search(t):
        return (_day(now.year, now.month, 1),
                _day(now.year + (now.month // 12), (now.month % 12) + 1, 1))

    if _EARLY_RE.search(t):
        return now - timedelta(days=3650), now - timedelta(days=30)

    return None
