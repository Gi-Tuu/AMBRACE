# -*- coding: utf-8 -*-
"""Ariadne 模块 A：中文时间表达解析纯函数单测（parse_time_range）。

锚定语义：绝对年月窗口 / 相对日窗口 / 周与月窗口 / 去年 / 模糊早期宽窗口 /
非法输入返回 None（绝不猜）；now 可注入（2026-09-09 周三 12:00 UTC 作基准）。
"""
from datetime import datetime

from app.memory.time_query import parse_time_range

NOW = datetime(2026, 9, 9, 12, 0, 0)  # 周三（weekday=2）


def test_绝对年月():
    assert parse_time_range("2026-07 的事", NOW) == (datetime(2026, 7, 1), datetime(2026, 8, 1))
    assert parse_time_range("2026年7月", NOW) == (datetime(2026, 7, 1), datetime(2026, 8, 1))
    assert parse_time_range("2026/12", NOW) == (datetime(2026, 12, 1), datetime(2027, 1, 1))


def test_相对日():
    assert parse_time_range("今天聊的", NOW) == (datetime(2026, 9, 9), datetime(2026, 9, 10))
    assert parse_time_range("昨天说的", NOW) == (datetime(2026, 9, 8), datetime(2026, 9, 9))
    assert parse_time_range("前天那顿饭", NOW) == (datetime(2026, 9, 7), datetime(2026, 9, 8))


def test_周窗口():
    # 本周：周一 9/7 起 7 天；上周：9/31? 不——8/31（周一）起 7 天
    assert parse_time_range("这周", NOW) == (datetime(2026, 9, 7), datetime(2026, 9, 14))
    assert parse_time_range("本周内", NOW) == (datetime(2026, 9, 7), datetime(2026, 9, 14))
    assert parse_time_range("上周", NOW) == (datetime(2026, 8, 31), datetime(2026, 9, 7))


def test_月窗口():
    assert parse_time_range("这个月", NOW) == (datetime(2026, 9, 1), datetime(2026, 10, 1))
    assert parse_time_range("上个月", NOW) == (datetime(2026, 8, 1), datetime(2026, 9, 1))
    # 跨年：1 月的上月 = 去年 12 月
    jan = datetime(2026, 1, 15)
    assert parse_time_range("上月", jan) == (datetime(2025, 12, 1), datetime(2026, 1, 1))
    # 12 月的这月 → 明年 1 月止
    dec = datetime(2026, 12, 3)
    assert parse_time_range("这月", dec) == (datetime(2026, 12, 1), datetime(2027, 1, 1))


def test_去年():
    assert parse_time_range("去年夏天", NOW) == (datetime(2025, 1, 1), datetime(2026, 1, 1))


def test_模糊早期宽窗口():
    from datetime import timedelta
    r = parse_time_range("我们刚认识那阵", NOW)
    assert r == (NOW - timedelta(days=3650), NOW - timedelta(days=30))
    assert parse_time_range("最早的时候", NOW) == r


def test_非法输入返回None():
    assert parse_time_range("") is None
    assert parse_time_range("随便聊聊，没有时间词") is None
    # 非法月份不猜
    assert parse_time_range("2026-13") is None


def test_无now注入用当前时间():
    # 不带 now 也能出结果（语义烟囱）；区间起点早于终点
    r = parse_time_range("昨天")
    assert r is not None and r[0] < r[1]


# ────────────────────────── F-3 时区口径（2026-09-04）──────────────────────────

def test_F3_UTC8凌晨_今天昨天跨UTC日():
    # UTC+8 本地 2026-09-10 00:30 => UTC 前一天 16:30（2026-09-09 16:30）
    now_utc = datetime(2026, 9, 9, 16, 30, 0)
    tz = 480  # UTC+8
    # 今天 → 本地 9/10 当天窗口（折回 UTC 为 9/9 16:00 ~ 9/10 16:00）
    assert parse_time_range("今天", now_utc, tz) == (datetime(2026, 9, 9, 16, 0), datetime(2026, 9, 10, 16, 0))
    # 昨天 → 本地 9/9（跨 UTC 日：UTC 9/8 16:00 ~ 9/9 16:00）
    assert parse_time_range("昨天", now_utc, tz) == (datetime(2026, 9, 8, 16, 0), datetime(2026, 9, 9, 16, 0))
    # 前天 → 本地 9/8
    assert parse_time_range("前天", now_utc, tz) == (datetime(2026, 9, 7, 16, 0), datetime(2026, 9, 8, 16, 0))


def test_F3_无偏移_与旧UTC行为一致回归():
    # 与旧版逐字节一致：今天=UTC 当日 00:00 起；昨天=UTC 前一日 00:00 起
    now_utc = datetime(2026, 9, 9, 16, 30, 0)
    assert parse_time_range("今天", now_utc, None) == (datetime(2026, 9, 9, 0, 0), datetime(2026, 9, 10, 0, 0))
    assert parse_time_range("昨天", now_utc, None) == (datetime(2026, 9, 8, 0, 0), datetime(2026, 9, 9, 0, 0))
    assert parse_time_range("本周", now_utc, None) == (datetime(2026, 9, 7, 0, 0), datetime(2026, 9, 14, 0, 0))
    assert parse_time_range("这月", now_utc, None) == (datetime(2026, 9, 1, 0, 0), datetime(2026, 10, 1, 0, 0))
    # 旧调用形态（不传 tz_offset_min）与显式 None 等价（回归保护）
    assert parse_time_range("今天", now_utc) == parse_time_range("今天", now_utc, None)


def test_F3_周月边界_本地周一和月首折回UTC():
    # UTC+8 本地 2026-09-10 00:30 => UTC 2026-09-09 16:30（本地周四，周一=9/7）
    now_utc = datetime(2026, 9, 9, 16, 30, 0)
    tz = 480
    # 本周：本地周一 9/7 00:00 => UTC 9/6 16:00 起 7 天
    assert parse_time_range("本周", now_utc, tz) == (datetime(2026, 9, 6, 16, 0), datetime(2026, 9, 13, 16, 0))
    # 上周：本地周一 8/31 00:00 => UTC 8/30 16:00 起 7 天
    assert parse_time_range("上周", now_utc, tz) == (datetime(2026, 8, 30, 16, 0), datetime(2026, 9, 6, 16, 0))
    # 这月：本地 9/1 00:00 => UTC 8/31 16:00 起；下月 10/1 00:00 => UTC 9/30 16:00
    assert parse_time_range("这月", now_utc, tz) == (datetime(2026, 8, 31, 16, 0), datetime(2026, 9, 30, 16, 0))
    # 上月：本地 8/1 00:00 => UTC 7/31 16:00 起
    assert parse_time_range("上月", now_utc, tz) == (datetime(2026, 7, 31, 16, 0), datetime(2026, 8, 31, 16, 0))


def test_F3_绝对年月不受时区影响():
    # 「时间=YYYY-MM」（二跳）与「去年」按绝对自然月/年，tz 偏移不改变结果
    now_utc = datetime(2026, 9, 9, 16, 30, 0)
    assert parse_time_range("2026-07", now_utc, 480) == (datetime(2026, 7, 1), datetime(2026, 8, 1))
    assert parse_time_range("去年", now_utc, 480) == (datetime(2025, 1, 1), datetime(2026, 1, 1))
