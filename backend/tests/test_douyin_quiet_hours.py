# -*- coding: utf-8 -*-
"""抖音插件深夜静默顺延：随机分钟取值回归测试（P2-1）。

缺陷：_random_execute_at 在「北京时间 0-7 点顺延到 7 点后」分支里用
random.randint(30, 60) 取分钟，randint 是闭区间 → 可能取到 60 →
datetime.replace(minute=60) 抛 ValueError（深夜任务直接崩）。

测法（不靠「打桩固定时间绕开」）：
- 冻结 now 到北京深夜若干小时，用记录型假 random 捕获代码**实际请求的闭区间**，
  断言分钟上界 <= 59；
- 再穷举该区间内**每一个可能被返回的取值**，断言算出的分钟恒 < 60 且顺延后落在 7 点；
  旧实现下 60 会被真传进 replace() → ValueError，本测试即失败。
"""
import sys
from datetime import datetime, timedelta, timezone

import pytest

_CN = timezone(timedelta(hours=8))


@pytest.fixture()
def douyin_mod():
    """装载 douyin_mcp 取模块引用（与 test_plugin_tenant_scope_m0 同口径；已装载则复用）"""
    from app.plugins import registry

    mod = sys.modules.get("ai_plugin_douyin_mcp")
    if mod is None:
        assert registry.load_plugin_dir(registry.EXAMPLE_DIR / "douyin_mcp") is not None
        mod = sys.modules["ai_plugin_douyin_mcp"]
    return mod


def _cn_now(hour: int, minute: int = 0) -> datetime:
    """构造「北京时间 hour:minute」对应的 UTC aware 时间（作为被冻结的 now）"""
    return datetime(2026, 9, 20, hour, minute, tzinfo=_CN).astimezone(timezone.utc)


class _Recorder:
    """假 random：按调用顺序返回预设值，同时记录每次请求的闭区间 (a, b)"""

    def __init__(self, values):
        self._values = list(values)
        self.intervals: list[tuple[int, int]] = []

    def randint(self, a: int, b: int) -> int:
        self.intervals.append((a, b))
        v = self._values.pop(0) if self._values else a
        assert a <= v <= b, f"测试桩越界：请求 [{a},{b}] 却要求返回 {v}"
        return v


def _freeze(mod, monkeypatch, now_utc: datetime, values) -> _Recorder:
    """冻结模块内 datetime.now + 替换模块内 random（均为模块属性，不外溢）"""
    fake = _Recorder(values)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now_utc if tz is None else now_utc.astimezone(tz)

    monkeypatch.setattr(mod, "datetime", _FrozenDateTime)
    monkeypatch.setattr(mod, "random", fake)
    return fake


def _as_cn(naive_utc: datetime) -> datetime:
    """插件返回 naive UTC，测试侧显式补 UTC 再换北京时间（不吃本机时区）"""
    return naive_utc.replace(tzinfo=timezone.utc).astimezone(_CN)


# ---------------- 深夜分支：请求的随机区间本身必须合法 ----------------

@pytest.mark.parametrize("cn_hour, delay", [(0, 15), (2, 120), (4, 60), (6, 15)])
def test_顺延分支请求的分钟上界不超过59(douyin_mod, monkeypatch, cn_hour, delay):
    fake = _freeze(douyin_mod, monkeypatch, _cn_now(cn_hour), [delay])
    t = douyin_mod._random_execute_at()

    assert len(fake.intervals) == 2, f"北京时间 {cn_hour} 点未进入顺延分支，用例失去意义"
    assert fake.intervals[0] == (15, 120), "随机延后区间不应被改动"
    lo, hi = fake.intervals[1]
    assert 0 <= lo <= hi <= 59, f"randint 闭区间上界非法（minute={hi}）：{fake.intervals[1]}"

    assert t.tzinfo is None, "返回值仍约定为 naive UTC"
    cn = _as_cn(t)
    assert cn.hour == 7, "顺延后应落在北京时间 7 点"
    assert lo <= cn.minute <= hi, "分钟应取自请求区间"


# ---------------- 深夜分支：穷举 randint 可能返回的每个取值 ----------------

@pytest.mark.parametrize("cn_hour", [0, 2, 4, 6])
def test_任意随机取值算出的分钟恒小于60(douyin_mod, monkeypatch, cn_hour):
    probe = _freeze(douyin_mod, monkeypatch, _cn_now(cn_hour), [15])
    douyin_mod._random_execute_at()
    assert len(probe.intervals) == 2, f"北京时间 {cn_hour} 点未进入顺延分支"
    lo, hi = probe.intervals[1]

    broken = []
    for v in range(lo, hi + 1):
        _freeze(douyin_mod, monkeypatch, _cn_now(cn_hour), [15, v])
        try:
            r = douyin_mod._random_execute_at()
        except ValueError as e:
            broken.append((v, repr(e)))
            continue
        assert 0 <= r.minute <= 59
        assert _as_cn(r).hour == 7 and _as_cn(r).minute == v

    assert not broken, (
        f"randint 可返回取值 [{lo},{hi}] 中存在导致非法分钟/异常的值：{broken}"
    )


# ---------------- 非深夜时段：不顺延、只按随机延后量走 ----------------

def test_非深夜时段不顺延(douyin_mod, monkeypatch):
    now = _cn_now(12)
    fake = _freeze(douyin_mod, monkeypatch, now, [45])
    t = douyin_mod._random_execute_at()

    assert len(fake.intervals) == 1, "非深夜不该再抽分钟"
    assert t == (now + timedelta(minutes=45)).replace(tzinfo=None)


# ---------------- 静默判定边界（同一批冻结口径顺带覆盖） ----------------

@pytest.mark.parametrize("cn_hour, quiet", [(0, True), (6, True), (7, False), (23, False)])
def test_is_quiet_hours_边界(douyin_mod, monkeypatch, cn_hour, quiet):
    _freeze(douyin_mod, monkeypatch, _cn_now(cn_hour), [15])
    assert douyin_mod._is_quiet_hours() is quiet
