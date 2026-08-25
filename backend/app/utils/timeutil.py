"""时间工具：库内统一 UTC naive 存储（北京时间 = UTC+8）

集中管理时间约定，避免各处重复定义导致口径不一致（曾因分散定义
出现"北京日期当 UTC 零点"类 8 小时窗口偏差 bug）。
"""
from datetime import datetime, timedelta, timezone

_BJ = timezone(timedelta(hours=8))


def now_naive_utc() -> datetime:
    """当前 UTC 时间（naive，匹配库内存储约定）"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def beijing_day_start_utc() -> datetime:
    """北京时间今天 00:00 对应的 UTC 时间（naive）"""
    now_bj = datetime.now(_BJ)
    start_bj = datetime(now_bj.year, now_bj.month, now_bj.day, tzinfo=_BJ)
    return start_bj.astimezone(timezone.utc).replace(tzinfo=None)


def shift_utc_naive(dt: datetime, offset_hours: int) -> datetime:
    """UTC naive 时间按偏移小时换算，返回 naive（跨日/月/年自动进位）。

    用于"按动态作者所在地区显示时间/日期分组"（朋友圈作者时区）。
    """
    return (
        dt.replace(tzinfo=timezone.utc)
        .astimezone(timezone(timedelta(hours=offset_hours)))
        .replace(tzinfo=None)
    )
