"""timeutil 纯函数测试：UTC naive 约定、北京时间日界、作者时区换算。"""
from datetime import datetime, timedelta, timezone

from app.utils.timeutil import beijing_day_start_utc, now_naive_utc, shift_utc_naive


def test_now_naive_utc_无时区():
    dt = now_naive_utc()
    assert dt.tzinfo is None


def test_beijing_day_start_utc_等于北京当天零点():
    start = beijing_day_start_utc()
    assert start.tzinfo is None
    # 北京当天 00:00 == UTC 前一天 16:00
    bj = start.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
    assert bj.hour == 0 and bj.minute == 0


def test_shift_utc_naive_北京时间():
    dt = datetime(2026, 8, 12, 4, 20, 0)
    assert shift_utc_naive(dt, 8) == datetime(2026, 8, 12, 12, 20, 0)


def test_shift_utc_naive_东京时间():
    dt = datetime(2026, 8, 12, 4, 20, 0)
    assert shift_utc_naive(dt, 9) == datetime(2026, 8, 12, 13, 20, 0)


def test_shift_utc_naive_伦敦与纽约():
    dt = datetime(2026, 8, 12, 4, 20, 0)
    assert shift_utc_naive(dt, 0) == datetime(2026, 8, 12, 4, 20, 0)
    assert shift_utc_naive(dt, -5) == datetime(2026, 8, 11, 23, 20, 0)  # 跨日


def test_shift_utc_naive_跨月进位():
    dt = datetime(2026, 8, 31, 23, 0, 0)
    assert shift_utc_naive(dt, 8) == datetime(2026, 9, 1, 7, 0, 0)


def test_shift_utc_naive_跨年进位():
    dt = datetime(2026, 12, 31, 20, 0, 0)
    assert shift_utc_naive(dt, 8) == datetime(2027, 1, 1, 4, 0, 0)


def test_shift_utc_naive_日期分组_key():
    # 归档 day_key 场景：UTC 23:30 + 东京 9 小时 -> 次日
    dt = datetime(2026, 8, 12, 15, 30, 0)
    assert shift_utc_naive(dt, 9).strftime("%Y-%m-%d") == "2026-08-13"
    assert shift_utc_naive(dt, 8).strftime("%Y-%m-%d") == "2026-08-12"
