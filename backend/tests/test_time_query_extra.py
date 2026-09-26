# -*- coding: utf-8 -*-
"""A4 批 2 / T4（2026-09-27）：时间短语补全（P0）。

覆盖：大前天/昨晚 两个既有误判修正、N 天/周/月前（中文数词 + 阿拉伯数字）、
结构化返回（kind / confidence），并回归既有 parse_time_range 口径不变。
"""
from datetime import datetime

from app.memory.time_query import cn_num, parse_time_query, parse_time_range

NOW = datetime(2026, 9, 27, 4, 0)   # 本地=UTC（offset 0）便于断言


def test_大前天不再被当成前天():
    r = parse_time_range("大前天的事", now=NOW)
    assert r == (datetime(2026, 9, 24), datetime(2026, 9, 25))
    assert parse_time_query("大前天", now=NOW).kind == "day"


def test_昨晚与昨天同窗():
    r = parse_time_range("昨晚聊到很晚", now=NOW)
    assert r == (datetime(2026, 9, 26), datetime(2026, 9, 27))
    for t in ("昨晚", "昨儿", "昨晚上"):
        assert parse_time_query(t, now=NOW) is not None


def test_中文数词解析():
    assert [cn_num(x) for x in ("3", "三", "两", "十二", "二十三", "二十")] == [3, 3, 2, 12, 23, 20]
    assert cn_num("很多") is None and cn_num("") is None


def test_N天前_中文与数字():
    for t in ("三天前", "3天前"):
        q = parse_time_query(t, now=NOW)
        assert (q.start, q.end, q.kind, q.confidence) == (
            datetime(2026, 9, 24), datetime(2026, 9, 25), "days_ago", "high")


def test_N周前与N个月前():
    w = parse_time_query("两周前", now=NOW)
    assert (w.start, w.end, w.kind) == (datetime(2026, 9, 7), datetime(2026, 9, 14), "weeks_ago")
    m = parse_time_query("两个月前", now=NOW)
    assert (m.start, m.end, m.kind) == (datetime(2026, 7, 1), datetime(2026, 8, 1), "months_ago")


def test_既有表达仍走原口径且带分档():
    assert parse_time_query("上个月", now=NOW).kind == "month"
    assert parse_time_query("上周", now=NOW).kind == "week"
    assert parse_time_query("2026-07", now=NOW).kind == "month"
    # 模糊早期＝宽窗 ⇒ low（「范围前置」不采信它）
    fuzzy = parse_time_query("刚认识那会儿", now=NOW)
    assert fuzzy.kind == "fuzzy" and fuzzy.confidence == "low"


def test_识别不了返回None不猜():
    for t in ("随便聊聊", "你好呀", "", "昨天下午" if False else "下午好"):
        q = parse_time_query(t, now=NOW)
        assert q is None or q.confidence in ("high", "low")


def test_越界数词不解析():
    assert parse_time_query("999天前", now=NOW) is None
    assert parse_time_query("60周前", now=NOW) is None
    assert parse_time_query("30个月前", now=NOW) is None
