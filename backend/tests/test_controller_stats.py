# -*- coding: utf-8 -*-
"""控制台只读统计函数测试（2026-08-23：近 7 天 Token 用量趋势聚合，mock 数据库）

server_controller 位于 backend 之外（backend 的兄弟目录），测试里把该项目目录加到
sys.path 后 import。只测纯聚合函数 + 用临时 SQLite（mode=ro）mock 数据库验证只读读取。
"""
import os
import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))  # backend/tests -> backend -> 项目根
if os.path.join(_ROOT, "server_controller") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "server_controller"))

import server_controller as sc  # noqa: E402  (仅定义函数/常量，不建 Tk 实例)


def test_aggregate_groups_by_beijing_day():
    # created_at 以 UTC 落库：14:00 UTC == 22:00 北京时间（UTC+8），跨日边界按北京时间归桶
    rows = [
        ("2026-08-22 14:00:00", 100),   # 北京 8-22 22:00 -> 8-22
        ("2026-08-22 15:59:59", 200),   # 北京 8-22 23:59:59 -> 8-22
        ("2026-08-22 16:00:00", 400),   # 北京 8-23 00:00:00 -> 8-23
        ("2026-08-23 14:00:00", 50),    # 北京 8-23 22:00 -> 8-23
    ]
    trend = sc._aggregate_token_trend(
        rows,
        days=7,
        today=datetime(2026, 8, 23, 23, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    by_date = {t["date"]: t["tokens"] for t in trend}
    assert len(trend) == 7
    assert trend[0]["date"] == "2026-08-17"      # 近 7 天起点（8-17..8-23）
    assert by_date["2026-08-22"] == 300          # 100 + 200
    assert by_date["2026-08-23"] == 450          # 400 + 50
    assert by_date["2026-08-17"] == 0            # 无数据补 0


def test_aggregate_handles_naive_and_iso_inputs():
    rows = [("2026-08-23T14:00:00+00:00", 10), ("garbage", 999), (None, 999), ("2026-08-23 14:00:00", 5)]
    trend = sc._aggregate_token_trend(
        rows, days=3, today=datetime(2026, 8, 23, 12, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    by_date = {t["date"]: t["tokens"] for t in trend}
    assert by_date["2026-08-23"] == 15           # 10 + 5；垃圾/None 被跳过
    assert len(trend) == 3


def test_read_token_trend_mocked_db(tmp_path):
    """用临时 SQLite 模拟 llm_usage 表（mode=ro），验证只读近 7 天聚合（today 固定，测试与系统日期解耦）。"""
    db = tmp_path / "mock.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE llm_usage(created_at TEXT, total_tokens INTEGER)")
    con.executemany(
        "INSERT INTO llm_usage(created_at, total_tokens) VALUES(?, ?)",
        [("2026-08-22 14:00:00", 100), ("2026-08-23 14:00:00", 250)],
    )
    con.commit()
    con.close()

    trend = sc._read_token_trend(
        db=str(db), today=datetime(2026, 8, 23, 23, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    by_date = {t["date"]: t["tokens"] for t in trend}
    assert by_date["2026-08-22"] == 100
    assert by_date["2026-08-23"] == 250
    assert sum(t["tokens"] for t in trend) == 350


def test_read_token_trend_missing_table_returns_empty(tmp_path):
    db = tmp_path / "empty.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE other(t INTEGER)")
    con.commit()
    con.close()
    assert sc._read_token_trend(db=str(db)) == []


def test_fmt_helpers():
    assert sc._fmt_compact(12345) == "12.3k"
    assert sc._fmt_compact(12000) == "12k"
    assert sc._fmt_compact(1234567) == "1.2M"
    assert sc._fmt_compact(0) == "0"
    assert sc._fmt_short_date("2026-08-23") == "8/23"
