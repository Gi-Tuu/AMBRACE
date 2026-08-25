"""AI 日程（Phase B-2）纯函数测试：时间解析 / [SCHEDULE] 标记提取（2026-08-14）"""
from app.life.schedule import extract_schedule_mark, parse_schedule_time


def test_parse_schedule_time_full():
    # 北京时间 09:00 → UTC 01:00（naive）
    dt = parse_schedule_time("2026-08-16 09:00")
    assert dt is not None and dt.tzinfo is None
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 8, 16, 1)


def test_parse_schedule_time_hhmm():
    # 只有时分：应为今天（未过）或明天，返回 UTC naive 且晚于现在（北京时间口径）
    dt = parse_schedule_time("23:59")
    assert dt is not None and dt.tzinfo is None
    bj = dt + __import__("datetime").timedelta(hours=8)
    assert bj.hour == 23 and bj.minute == 59


def test_extract_schedule_mark():
    text = "今天想了很多。\n[SCHEDULE] 2026-08-17 15:00 整理最近的照片 [/SCHEDULE]"
    clean, sched = extract_schedule_mark(text)
    assert clean == "今天想了很多。"
    assert sched is not None
    assert sched["title"] == "整理最近的照片"
    assert sched["start_time"].tzinfo is None
    assert sched["end_time"] > sched["start_time"]


def test_extract_schedule_mark_no_mark():
    text = "只有反思内容"
    clean, sched = extract_schedule_mark(text)
    assert sched is None
    assert clean == text


def test_extract_schedule_mark_invalid():
    # 缺时间 → 不解析
    clean, sched = extract_schedule_mark("[SCHEDULE] 没有时间的标题 [/SCHEDULE]")
    assert sched is None
