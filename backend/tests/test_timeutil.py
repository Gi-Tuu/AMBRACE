"""timeutil 纯函数测试：UTC naive 约定、北京时间日界、作者时区换算、应用时区偏移/日界。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.utils.timeutil import (
    app_day_start_utc,
    app_local_hour,
    app_local_now,
    app_tz_offset_hours,
    beijing_day_start_utc,
    now_naive_utc,
    shift_utc_naive,
    to_naive_utc,
)


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


def test_app_tz_offset_hours_default_8():
    """默认（settings.APP_TZ_OFFSET_HOURS 未配置）应返回 +8。"""
    assert app_tz_offset_hours() == 8


def test_app_tz_offset_hours_and_local_now(monkeypatch):
    """读 settings.APP_TZ_OFFSET_HOURS；app_local_now/app_local_hour 随之偏移。"""
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "app_tz_offset_hours", 9)
    assert app_tz_offset_hours() == 9
    now = app_local_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(hours=9)
    # local_now 的本地小时 == app_local_hour() 的返回值；且比 UTC 偏移 9 小时（跨日取模）
    assert app_local_hour() == now.hour
    utc_hour = now.astimezone(timezone.utc).hour
    assert (now.hour - utc_hour) % 24 in (9, -15)


def test_app_local_now_default_utc8():
    """默认偏移 +8：本地小时与 UTC 小时相差 8（跨日取模）。"""
    now = app_local_now()
    assert now.utcoffset() == timedelta(hours=8)
    utc_hour = now.astimezone(timezone.utc).hour
    assert (now.hour - utc_hour) % 24 in (8, -16)


def test_to_naive_utc_none透传():
    assert to_naive_utc(None) is None


def test_to_naive_utc_naive原样():
    dt = datetime(2026, 8, 12, 4, 20, 0)
    assert to_naive_utc(dt) is dt  # 已 naive 原对象透传


def test_to_naive_utc_aware_utc转naive():
    dt = datetime(2026, 8, 12, 4, 20, 0, tzinfo=timezone.utc)
    out = to_naive_utc(dt)
    assert out.tzinfo is None
    assert out == datetime(2026, 8, 12, 4, 20, 0)


def test_to_naive_utc_aware_别时区归一():
    # UTC+8 的 12:20 == UTC 的 04:20
    dt = datetime(2026, 8, 12, 12, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    out = to_naive_utc(dt)
    assert out.tzinfo is None
    assert out == datetime(2026, 8, 12, 4, 20, 0)


# ---------------- app_day_start_utc（应用时区日界，P3-2 新增）----------------

def test_app_day_start_utc_default偏移与北京日界一致():
    """默认 APP_TZ_OFFSET_HOURS=8：与 beijing_day_start_utc 同值（行为不变的钉）"""
    assert abs(app_day_start_utc() - beijing_day_start_utc()) < timedelta(seconds=1)


def test_app_day_start_utc_返回naive且不晚于当前时刻():
    start = app_day_start_utc()
    assert start.tzinfo is None
    now = now_naive_utc()
    assert start <= now < start + timedelta(days=1)


@pytest.mark.parametrize(
    "offset, now_utc, expected",
    [
        (8, datetime(2026, 5, 19, 17, 0), datetime(2026, 5, 19, 16, 0)),   # 本地 05-20 01:00
        (8, datetime(2026, 5, 19, 16, 0), datetime(2026, 5, 19, 16, 0)),   # 恰好本地午夜整点
        (9, datetime(2026, 1, 1, 15, 30), datetime(2026, 1, 1, 15, 0)),    # 本地 01-02 00:30
        (-5, datetime(2026, 1, 7, 22, 0), datetime(2026, 1, 7, 5, 0)),     # 本地 01-07 17:00
        (0, datetime(2026, 1, 7, 3, 0), datetime(2026, 1, 7, 0, 0)),       # 与 UTC 重合
        (14, datetime(2026, 1, 1, 23, 0), datetime(2026, 1, 1, 10, 0)),    # 本地次日 13:00
    ],
)
def test_app_day_start_utc_按应用偏移取日界(monkeypatch, offset, now_utc, expected):
    """日界 = 应用本地当天 00:00 对应的 UTC naive（跨日/跨月自动进位）"""
    import app.config as cfg
    import app.utils.timeutil as tu

    tz = timezone(timedelta(hours=offset))
    monkeypatch.setattr(cfg.settings, "app_tz_offset_hours", offset)
    monkeypatch.setattr(tu, "app_local_now", lambda: now_utc.replace(tzinfo=timezone.utc).astimezone(tz))
    got = tu.app_day_start_utc()
    assert got.tzinfo is None
    assert got == expected


def test_app_day_start_utc_偏移9与北京日界分道(monkeypatch):
    """非 +8 时口径必须真的跟着应用时区走：本地(+9) 01-02 00:30 时北京日界仍是前一天 16:00"""
    import app.config as cfg
    import app.utils.timeutil as tu

    now_utc = datetime(2026, 1, 1, 15, 30)
    tz9 = timezone(timedelta(hours=9))
    monkeypatch.setattr(cfg.settings, "app_tz_offset_hours", 9)
    monkeypatch.setattr(tu, "app_local_now",
                        lambda: now_utc.replace(tzinfo=timezone.utc).astimezone(tz9))
    got = tu.app_day_start_utc()
    assert got == datetime(2026, 1, 1, 15, 0)
    # 同一瞬间按北京口径的日界参照值（beijing_day_start_utc 读真实时钟，这里按算式给参照）
    bj = now_utc.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
    bj_start = datetime(bj.year, bj.month, bj.day, tzinfo=timezone(timedelta(hours=8)))
    bj_start = bj_start.astimezone(timezone.utc).replace(tzinfo=None)
    assert got != bj_start



