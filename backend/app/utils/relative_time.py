# -*- coding: utf-8 -*-
"""相对时间词绝对化（Q2，2026-09-25）：把「明天 / 后天 / 下周三」按基准日换成绝对日期。

为什么需要：计划正文（``prospective_intents.content``）是**当初写下的那句话**，其中的相对时间词
锚定的是写计划那天；two-pass 现状 trace 在主动消息生成前把它原样注入时，「明天」往往早已过去，
会误导生成端——与该层「注入用日期锚点、不带相对时间词」的原则冲突。

口径：
- 基准日按**北京时间**取（库内 naive UTC，先换算再取 date），与 ``survival_checklist._due_label`` 同源；
- 输出格式 ``M月D日``（例：9月24日），只替换时间词本身，句子其余部分逐字保留；
- **fail-open**：``base`` 缺失/不可解析/任何异常 → 原样返回 ``text``，绝不抛给主链路；
- 长词优先 + 屏蔽表：「前几天」「以后天气」这类靠子串误命中的不改写原文。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.utils.logger import get_logger
from app.utils.timeutil import app_tz_offset_hours, shift_utc_naive, to_naive_utc

_logger = get_logger("utils.relative_time")

# 与 scheduling/life_regression._CN_WEEKDAYS 同表（周一＝0）
_CN_WEEKDAYS = "一二三四五六日"

# 词 → 相对基准日的天数偏移。**长词必须排在短词前面**（大前天 先于 前天、大后天 先于 后天）
_DAY_OFFSETS: tuple[tuple[str, int], ...] = (
    ("大前天", -3), ("大后天", 3),
    ("前天", -2), ("后天", 2),
    ("昨天", -1), ("昨日", -1),
    ("明天", 1), ("明日", 1),
    ("今天", 0), ("今日", 0), ("本日", 0),
)
_DAY_OFFSET = dict(_DAY_OFFSETS)

# 屏蔽表：词前出现这些字时，命中的其实是另一个词（「前几天/前些天」含「前天」、
# 「以后天气/之后天气」含「后天」）——宁可不改，也不改写原文。
_GUARD_PREFIX = {"前天": "几些这那每哪", "后天": "以之然"}

_DAY_RE = re.compile("|".join(
    (f"(?<![{_GUARD_PREFIX[word]}]){re.escape(word)}" if word in _GUARD_PREFIX else re.escape(word))
    for word, _offset in _DAY_OFFSETS
))
# 下周X / 本周X / 这周X（X＝一二三四五六日天）；下周末 / 本周末 / 这周末＝该周周六
_WEEK_RE = re.compile(r"(?P<prev>[下本这])周(?P<tail>末|[一二三四五六日天])")


def _fmt(day: date) -> str:
    return f"{day.month}月{day.day}日"


def _base_day(base) -> date | None:
    """base（naive/aware datetime、date、ISO 串）→ 北京时间基准日；不可解析返回 ``None``。"""
    if isinstance(base, str):
        base = datetime.fromisoformat(base.strip().replace("T", " "))
    if isinstance(base, datetime):
        return shift_utc_naive(to_naive_utc(base), app_tz_offset_hours()).date()
    if isinstance(base, date):
        return base
    return None


def _week_day(base_day: date, prefix: str, tail: str) -> date:
    """（下/本/这）周（X｜末）→ 那一周（周一为一周起点）的目标日期。"""
    index = 5 if tail == "末" else (6 if tail in "日天" else _CN_WEEKDAYS.index(tail))
    monday = base_day - timedelta(days=base_day.weekday())
    if prefix == "下":
        monday += timedelta(days=7)
    return monday + timedelta(days=index)


def absolutize_relative_dates(text: str, base) -> str:
    """``text`` 里的相对时间词 → 以 ``base`` 为基准日的绝对日期（``M月D日``）。

    ``base`` 为库内时间口径（naive UTC）。同一段里多处出现逐处替换；无法处理时原样返回。
    """
    if not isinstance(text, str):
        return ""
    if not text:
        return text
    try:
        base_day = _base_day(base)
        if base_day is None:
            return text
        out = _DAY_RE.sub(lambda m: _fmt(base_day + timedelta(days=_DAY_OFFSET[m.group()])), text)
        out = _WEEK_RE.sub(lambda m: _fmt(_week_day(base_day, m.group("prev"), m.group("tail"))), out)
        return out or text
    except Exception as e:
        _logger.warning("absolutize relative dates failed, keep raw: %s", e)
        return text
