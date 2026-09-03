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
