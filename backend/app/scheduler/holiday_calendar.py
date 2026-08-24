"""节日日历数据 — 中国常见节日 + 国际节日"""
from datetime import date, timedelta

try:
    from lunardate import LunarDate
    _HAS_LUNARDATE = True
except ImportError:
    LunarDate = None
    _HAS_LUNARDATE = False

# lunardate 数据覆盖范围（约 1900-2100），范围外会返回垃圾数据，需要排除
_LUNAR_MIN_YEAR, _LUNAR_MAX_YEAR = 1900, 2100

# 固定日期节日（MM-DD -> 节日名称，同一日期合并中英文名）
_FIXED_HOLIDAYS: dict[str, list[dict]] = {
    "01-01": [{"name": "元旦", "lang": "zh"}, {"name": "New Year's Day", "lang": "en"}],
    "02-14": [{"name": "情人节", "lang": "zh"}, {"name": "Valentine's Day", "lang": "en"}],
    "03-08": [{"name": "妇女节", "lang": "zh"}, {"name": "International Women's Day", "lang": "en"}],
    "03-12": [{"name": "植树节", "lang": "zh"}],
    "04-01": [{"name": "愚人节", "lang": "zh"}, {"name": "April Fools' Day", "lang": "en"}],
    "05-01": [{"name": "劳动节", "lang": "zh"}, {"name": "International Workers' Day", "lang": "en"}],
    "05-04": [{"name": "青年节", "lang": "zh"}],
    "06-01": [{"name": "儿童节", "lang": "zh"}, {"name": "Children's Day", "lang": "en"}],
    "07-01": [{"name": "建党节", "lang": "zh"}],
    "08-01": [{"name": "建军节", "lang": "zh"}],
    "09-10": [{"name": "教师节", "lang": "zh"}],
    "10-01": [{"name": "国庆节", "lang": "zh"}],
    "10-31": [{"name": "万圣节", "lang": "zh"}, {"name": "Halloween", "lang": "en"}],
    "12-25": [{"name": "圣诞节", "lang": "zh"}, {"name": "Christmas Day", "lang": "en"}],
}

# 农历节日：农历(月, 日) -> 节日名称（用 lunardate 精确换算公历，闰月不匹配）
_LUNAR_FESTIVALS: dict[tuple[int, int], list[dict]] = {
    (1, 1): [{"name": "春节", "lang": "zh"}, {"name": "Spring Festival", "lang": "en"}],
    (1, 15): [{"name": "元宵节", "lang": "zh"}],
    (5, 5): [{"name": "端午节", "lang": "zh"}, {"name": "Dragon Boat Festival", "lang": "en"}],
    (7, 7): [{"name": "七夕节", "lang": "zh"}, {"name": "Qixi Festival", "lang": "en"}],
    (8, 15): [{"name": "中秋节", "lang": "zh"}, {"name": "Mid-Autumn Festival", "lang": "en"}],
    (9, 9): [{"name": "重阳节", "lang": "zh"}],
    (12, 8): [{"name": "腊八节", "lang": "zh"}],
    (12, 23): [{"name": "小年", "lang": "zh"}],
}

# "第X个周X"动态节日：月、星期几(0=周一...6=周日)、第几个
_DYNAMIC_HOLIDAYS: dict[str, dict] = {
    "母亲节": {"month": 5, "weekday": 6, "nth": 2,
              "names": [{"name": "母亲节", "lang": "zh"}, {"name": "Mother's Day", "lang": "en"}]},
    "父亲节": {"month": 6, "weekday": 6, "nth": 3,
              "names": [{"name": "父亲节", "lang": "zh"}, {"name": "Father's Day", "lang": "en"}]},
    "感恩节": {"month": 11, "weekday": 3, "nth": 4,
              "names": [{"name": "感恩节", "lang": "zh"}, {"name": "Thanksgiving Day", "lang": "en"}]},
}


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """某月第 nth 个 weekday（weekday: 0=周一 ... 6=周日）"""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (nth - 1) * 7)


def _dynamic_holidays(today: date) -> list[dict]:
    """动态节日（第X个周X）"""
    result: list[dict] = []
    for spec in _DYNAMIC_HOLIDAYS.values():
        if today == _nth_weekday(today.year, spec["month"], spec["weekday"], spec["nth"]):
            result.extend(spec["names"])
    return result


def get_holidays(today: date | None = None) -> list[dict]:
    """获取指定日期的所有节日（为空则取当天）"""
    if today is None:
        today = date.today()
    key = today.strftime("%m-%d")
    result: list[dict] = []

    # 固定日期节日
    if key in _FIXED_HOLIDAYS:
        result.extend(_FIXED_HOLIDAYS[key])

    # 农历节日精确换算（仅限数据范围，且排除闰月）
    if _HAS_LUNARDATE and _LUNAR_MIN_YEAR <= today.year <= _LUNAR_MAX_YEAR:
        try:
            lunar = LunarDate.from_solar_date(today.year, today.month, today.day)
            if not lunar.is_leap_month:
                lunar_key = (lunar.month, lunar.day)
                if lunar_key in _LUNAR_FESTIVALS:
                    result.extend(_LUNAR_FESTIVALS[lunar_key])
        except Exception:
            pass

    # 动态节日（母亲节/父亲节/感恩节）
    result.extend(_dynamic_holidays(today))

    return result

