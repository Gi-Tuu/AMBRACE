"""时间工具：库内统一 UTC naive 存储（北京时间 = UTC+8）

集中管理时间约定，避免各处重复定义导致口径不一致（曾因分散定义
出现"北京日期当 UTC 零点"类 8 小时窗口偏差 bug）。
"""
from datetime import datetime, timedelta, timezone

_BJ = timezone(timedelta(hours=8))


def app_tz_offset_hours() -> int:
    """应用时区偏移（小时）：读 settings.APP_TZ_OFFSET_HOURS，默认 +8（北京时间）。

    ⚠️ 本函数只服务「用户可感知窗口」的运行时判断（主动消息时段 / 日记与反思触发 /
    本地小时换算）。库内存储口径保持 UTC-naive 不变（零数据迁移）：入库请继续用
    now_naive_utc()，不要用本函数的时间做写库。
    """
    from app.config import settings
    try:
        return int(settings.app_tz_offset_hours)
    except Exception:
        return 8


def app_local_now() -> datetime:
    """当前「应用本地时区」时间（带 tzinfo，偏移 APP_TZ_OFFSET_HOURS 小时）。

    等价于 UTC now 偏移 app_tz_offset_hours() 小时；仅供「用户可感知」的本地小时/
    日期窗口判断，绝不用于写库（写库仍走 now_naive_utc）。
    """
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=app_tz_offset_hours()))
    )


def app_local_hour() -> int:
    """当前应用本地小时（0-23），供调度器主动窗口/日记/反思触发判断。"""
    return app_local_now().hour


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
