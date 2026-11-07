"""KPI 卡右半（第七十四批＝用户 10-11 拍板 C＋B）：水印打底 ＋ 两张迷你趋势线。

锁的是四件容易退回去的事：
① 日界换算只有一把尺（记忆数与 token 趋势必须同口径，否则一张卡按 UTC 一张按北京）；
② 趋势线**不许把"没数据"画成一条线**（贴底的假线比留白更坏）；
③ 六张卡都有水印，但只有两张有趋势线（登记面要能数出来）；
④ 远程监控模式不读本地库 ⇒ 两条线为空，卡片仍然完整。

口径沿用 `test_console_smoke.py`：HTTP 与本地库全打桩，窗口 `withdraw()` 离屏构造。
"""
from __future__ import annotations

import datetime as dt
import sqlite3 as real_sqlite3

import conftest as cf  # noqa: F401  （触发 sys.path 装配）
import pytest
import console_core as sc  # W6-A：桩/断言目标改为共享真源（纯标准库）

TZ = dt.timezone(dt.timedelta(hours=8))


# ── ① 日界换算：一把尺 ────────────────────────────────────────────

def test_北京日界把UTC深夜算成次日():
    """库里是 naive UTC：23:30Z 是北京**次日** 07:30，不能落进当天桶。"""
    assert sc._bj_day_of("2026-10-10 23:30:00", TZ) == "2026-10-11"
    assert sc._bj_day_of("2026-10-10T01:00:00Z", TZ) == "2026-10-10"


def test_坏时间串返回空串而不是抛():
    for bad in (None, "", "昨天", "2026-13-45 99:99:99"):
        assert sc._bj_day_of(bad, TZ) == ""


def test_token趋势改用同一把尺后读数不变():
    """反向钉：`_aggregate_token_trend` 收敛到 `_bj_day_of` 之后，日界结果必须照旧。"""
    rows = [("2026-10-10 23:30:00", 10), ("2026-10-11 00:30:00", 5)]
    out = sc._aggregate_token_trend(rows, days=2, today=dt.datetime(2026, 10, 11, 12, 0, tzinfo=TZ))
    # 两条 naive UTC 都落北京 10-11（07:30 与 08:30）⇒ 10-10 桶空、10-11 桶 15
    assert [r["tokens"] for r in out] == [0, 15], out


# ── ② 折线归一化：没数据不许画成线 ────────────────────────────────

def test_少于两点不画():
    assert sc._spark_points([], 88, 24) == []
    assert sc._spark_points([7], 88, 24) == []


def test_全零不画而全平正数画在中线():
    assert sc._spark_points([0, 0, 0, 0], 88, 24) == []
    flat = sc._spark_points([6, 6, 6], 88, 24)
    assert len(flat) == 3 and {round(y, 3) for _, y in flat} == {12.0}


def test_最低落底最高落顶():
    """Canvas 的 y 向下长：最大值贴顶（小 y）、最小值贴底（大 y）。"""
    pts = sc._spark_points([1, 9, 5], 88, 24, pad=3)
    ys = [y for _, y in pts]
    assert min(ys) == pytest.approx(3.0)    # 9 是最大值 → 顶部
    assert max(ys) == pytest.approx(21.0)   # 1 是最小值 → 底部
    assert pts[0][0] == pytest.approx(3.0) and pts[-1][0] == pytest.approx(85.0)


# ── ③ 趋势数据面：热力图复用 + 未来占位日 ─────────────────────────

def test_趋势线排除未来占位日():
    heat = [{"date": "2026-10-09", "tokens": 100, "future": False},
            {"date": "2026-10-10", "tokens": 200, "future": False},
            {"date": "2026-10-11", "tokens": 0, "future": True}]
    assert sc._spark_from_heat(heat, days=14) == [100, 200]
    assert sc._spark_from_heat([], days=14) == []
    assert sc._spark_from_heat(None, days=14) == []


def test_记忆日新增按北京分桶且只读窗口内(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    con = real_sqlite3.connect(db)
    con.execute("CREATE TABLE memories (created_at TEXT)")
    # 北京 10-11 的三条（其中一条 UTC 还是 10-10 深夜）＋ 窗口外的一条
    con.executemany("INSERT INTO memories VALUES (?)", [
        ("2026-10-11 02:00:00",), ("2026-10-10 23:30:00",), ("2026-10-11 07:00:00",),
        ("2026-01-01 00:00:00",)])
    con.commit()
    con.close()
    monkeypatch.setattr(sc, "sqlite3", real_sqlite3)
    monkeypatch.setattr(sc, "_is_remote", lambda: False)
    series = sc._read_mem_daily_new(days=2, db=str(db),
                                    today=dt.datetime(2026, 10, 11, 12, 0, tzinfo=TZ))
    # 三条全落北京 10-11：02:00Z→10:00、23:30Z→次日 07:30、07:00Z→15:00
    assert series == [0, 3], series
    # 反证＝按 UTC 日界分桶会得到 [1, 2]（10-10 那条被留在前一天）；这正是要锁住的错法
    assert series != [1, 2]


def test_远程模式不读库(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_is_remote", lambda: True)
    assert sc._read_mem_daily_new(days=14, db=str(tmp_path / "nope.db")) == []


# ── ④ 卡片构造：六张有水印、两张登记趋势线 ────────────────────────
