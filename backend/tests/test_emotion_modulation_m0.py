# -*- coding: utf-8 -*-
"""A4 批 7 / T7 **M0**（2026-09-29）：情绪→驱力调制纯函数 + ``settle_level`` 可选参 + 影子留痕。

被测三件（派单 M0＝零行为）：
1. 新纯函数模块 ``app/domain/relational/emotion_modulation.py``（valence/arousal 派生读数、
   情绪乘子表、性格偏置表、总闸 [0.80,1.25]、偏置封顶 ±0.10）；
2. ``drives.settle_level`` 新增**可选参数** ``emotion_snapshot=None``——缺省必须与 HEAD
   （改造前实现）**逐字节一致**，故本文件内置一份 HEAD 版算法的**逐字副本** ``_legacy_settle_level``
   做对照，水位用 float 位模式比较（``struct.pack``），不接受 ``approx`` 糊过去；
3. 影子档 ``shadow_trace_modulation``：只留痕（INFO／obs_event），不改返回值、不写库、异常自吞。

纪律：全程零 DB、零迁移、零网络、不调模型；``obs_event`` 一律 monkeypatch 掉（连 trace 队列都不进），
不碰 backend/data 生产库。M0 阶段**没有任何生产调用方传新参数**（本文件是唯一传快照的地方）。
"""
from __future__ import annotations

import inspect
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domain.relational import drives as d
from app.domain.relational import emotion_modulation as em
from app.utils.timeutil import shift_utc_naive, to_naive_utc

ALL_DRIVES = d.DRIVE_ALL_KEYS


def _bj(hour: int, minute: int = 0, *, day: int = 2) -> datetime:
    """北京时间 hour:minute → naive UTC（项目口径：北京 = UTC+8，库内一律 naive UTC）。"""
    return shift_utc_naive(datetime(2026, 9, day, hour, minute), -8)


def _bits(value: float) -> bytes:
    """float 的位模式——「逐字节一致」的判据（比 == 更严：精度漂移与 -0.0 都逃不掉）。"""
    return struct.pack(">d", float(value))


# ══════════════════════════════ 0. HEAD 版算法逐字副本（对照组）
# 逐字抄自 HEAD ``app/domain/relational/drives.py``（批 3 M1a，commit 71725012，此后未再改动）的
# ``settle_level`` 函数体，只把模块私有常量换成同名值。日后改 drives.py **不要**同步这里——
# 本函数的存在意义就是「改造前的行为」，任何同步都会让对照断言失去证明力。
_LEGACY_GROWTH = {
    "longing": 0.090, "concern": 0.075, "affection": 0.060,
    "sharing": 0.050, "curiosity": 0.035, "intimacy": 0.020,
}
_LEGACY_NIGHT = {
    "longing": 0.6, "concern": 1.0, "affection": 1.0,
    "sharing": 1.0, "curiosity": 1.0, "intimacy": 0.4,
}
_LEGACY_RELEASE = {
    "longing": 0.35, "concern": 0.70, "affection": 0.40,
    "sharing": 0.45, "curiosity": 0.50, "intimacy": 0.18,
}


def _legacy_to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _legacy_settle_level(level, drive_key, last_settled_at, now):
    cursor = to_naive_utc(last_settled_at)
    moment = to_naive_utc(now)
    if cursor is None:
        return (_legacy_to_float(level), moment)
    if moment <= cursor:
        return (_legacy_to_float(level), cursor)

    growth = _LEGACY_GROWTH.get(drive_key, 0.0)
    night_mult = _LEGACY_NIGHT.get(drive_key, 1.0)
    one_hour = timedelta(hours=1)

    increment = 0.0
    cur = cursor
    while cur < moment:
        cur_bj = shift_utc_naive(cur, 8)
        boundary_utc = shift_utc_naive(
            cur_bj.replace(minute=0, second=0, microsecond=0) + one_hour, -8
        )
        seg_end = moment if moment < boundary_utc else boundary_utc
        hours = (seg_end - cur).total_seconds() / 3600.0
        factor = night_mult if d.is_night_hour(cur_bj.hour) else 1.0
        increment += hours * growth * factor
        cur = seg_end

    return (min(100.0, _legacy_to_float(level) + increment), moment)


# 对照用例矩阵：白天 / 跨入夜间 / 整段夜间 / 分钟级碎段 / 24h / 封顶 / 同刻 / 回拨 / 首建行 / 未知键 / 脏水位 / 长跨度 / 带 tzinfo
_CASES: list[tuple] = [
    (0.0, "longing", _bj(9, 0), _bj(11, 0)),
    (10.0, "longing", _bj(22, 0), _bj(23, 30)),
    (10.0, "longing", _bj(23, 0), _bj(3, 0, day=3)),
    (10.0, "intimacy", _bj(23, 0), _bj(3, 0, day=3)),
    (5.0, "concern", _bj(3, 17), _bj(6, 43)),
    (12.345, "sharing", _bj(0, 0), _bj(23, 59, day=3)),
    (98.0, "longing", _bj(9, 0), _bj(20, 0)),
    (30.0, "curiosity", _bj(9, 0), _bj(9, 0)),
    (30.0, "curiosity", _bj(10, 0), _bj(9, 0)),
    (30.0, "affection", None, _bj(9, 0)),
    (7.5, "nonexistent_drive", _bj(9, 0), _bj(12, 0)),
    ("bad", "longing", _bj(9, 0), _bj(12, 0)),
    (None, "longing", _bj(9, 0), _bj(12, 0)),
    (40.0, "longing", datetime(2026, 9, 2, 1, 0), datetime(2026, 9, 20, 5, 30)),
    (1.0, "affection", _bj(9, 0).replace(tzinfo=timezone.utc), _bj(15, 0)),
]
_CASE_IDS = [f"case{i}" for i in range(len(_CASES))]


# ══════════════════════ 1. 派生读数：只由八维现算（不落列不落表）

def test_readings_all_50_is_neutral():
    snap = {k: 50 for k in ("mood", "comfort", "sensitivity", "body_temp", "anger")}
    assert em.derive_readings(snap) == {"valence": 0.5, "arousal": 0.5}


def test_readings_formula_and_extremes():
    # valence=(mood+comfort)/2/100；arousal=(sensitivity+body_temp+anger)/3/100
    low = {"mood": 0, "comfort": 0, "sensitivity": 0, "body_temp": 0, "anger": 0}
    high = {"mood": 100, "comfort": 100, "sensitivity": 100, "body_temp": 100, "anger": 100}
    assert em.derive_readings(low) == {"valence": 0.0, "arousal": 0.0}
    assert em.derive_readings(high) == {"valence": 1.0, "arousal": 1.0}
    mixed = {"mood": 80, "comfort": 20, "sensitivity": 30, "body_temp": 60, "anger": 90}
    got = em.derive_readings(mixed)
    assert got["valence"] == pytest.approx(0.5)
    assert got["arousal"] == pytest.approx(0.6)


@pytest.mark.parametrize("dirty", [
    {"mood": None, "comfort": "abc"},
    {"mood": True, "comfort": [], "sensitivity": object()},
    {},
    {"anger": "   "},
])
def test_readings_dirty_values_stay_in_range(dirty):
    got = em.derive_readings(dirty)
    assert all(0.0 <= v <= 1.0 for v in got.values())
    assert em.derive_readings({}) == {"valence": 0.5, "arousal": 0.5}  # 一维都读不出 ⇒ 中性（不放大调制）


def test_readings_none_snapshot_is_neutral():
    assert em.derive_readings(None) == {"valence": 0.5, "arousal": 0.5}
    assert em.read_axes(None) == (0.5, 0.5)


def test_readings_out_of_range_clamped():
    got = em.derive_readings(
        {"mood": 900, "comfort": -500, "sensitivity": 1e9, "body_temp": 50, "anger": 50}
    )
    assert got["valence"] == pytest.approx(0.5)                      # (100+0)/2/100
    assert got["arousal"] == pytest.approx((100 + 50 + 50) / 3 / 100)  # 越界维夹到 100


def test_readings_ignore_non_eight_dim_scalars():
    """desire/possessiveness/fatigue 与关系标量**不得**进读数（设计 §3.1 + §④ #5）。"""
    base = {"mood": 20, "comfort": 40, "sensitivity": 70, "body_temp": 10, "anger": 90}
    loaded = dict(base, desire=0, possessiveness=0, fatigue=0, trust=100, attachment=100, curiosity=100)
    assert em.derive_readings(loaded) == em.derive_readings(base)


def test_readings_from_row_object_and_purity():
    class Row:
        mood, comfort, sensitivity, body_temp, anger = 100, 100, 0, 0, 0

    snap = {"mood": 100, "comfort": 100, "sensitivity": 0, "body_temp": 0, "anger": 0}
    before = dict(snap)
    assert em.derive_readings(Row()) == em.derive_readings(snap)
    assert em.derive_readings(snap) == em.derive_readings(snap)  # 确定性
    assert snap == before                                      # 不修改入参


@pytest.mark.parametrize("form", [(0.2, 0.8), [0.2, 0.8], {"valence": 0.2, "arousal": 0.8}])
def test_read_axes_accepts_snapshot_forms(form):
    assert em.read_axes(form) == (0.2, 0.8)


@pytest.mark.parametrize("junk", ["nonsense", 42, {}, (1, 2, 3), {"arousal": "x"}, object()])
def test_read_axes_junk_falls_back_to_neutral(junk):
    assert em.read_axes(junk) == (0.5, 0.5)


# ══════════════════════ 2. 情绪乘子表（上下限）

@pytest.mark.parametrize("drive", ALL_DRIVES)
def test_emotion_multiplier_default_is_exactly_one(drive):
    assert em.emotion_multiplier(drive) == 1.0
    assert em.emotion_multiplier(drive, None) == 1.0
    assert em.emotion_multiplier(drive, {"valence": 0.5, "arousal": 0.5}) == 1.0


@pytest.mark.parametrize("drive", ALL_DRIVES)
def test_emotion_multiplier_single_sided_shape(drive):
    """三条规则的形状（§3.1）：低愉悦上浮 longing/concern、高唤醒上浮 sharing/curiosity、
    低愉悦下压 affection（全表唯一负向）、intimacy 不参与；未指定的一侧恒 1.0。"""
    expect = {
        "longing":   (1.25, 1.0, 1.0, 1.0),
        "concern":   (1.25, 1.0, 1.0, 1.0),
        "sharing":   (1.0, 1.0, 1.0, 1.20),
        "curiosity": (1.0, 1.0, 1.0, 1.20),
        "affection": (0.80, 1.0, 1.0, 1.0),
        "intimacy":  (1.0, 1.0, 1.0, 1.0),
    }[drive]
    val_lo, val_hi, aro_lo, aro_hi = expect
    assert em.emotion_multiplier(drive, {"valence": 0.0, "arousal": 0.5}) == val_lo
    assert em.emotion_multiplier(drive, {"valence": 1.0, "arousal": 0.5}) == val_hi
    assert em.emotion_multiplier(drive, {"valence": 0.5, "arousal": 0.0}) == aro_lo
    assert em.emotion_multiplier(drive, {"valence": 0.5, "arousal": 1.0}) == aro_hi


def test_emotion_multiplier_grid_never_leaves_bounds():
    """穷举 41×41 读数格 × 六驱力 ⇒ 恒 ∈ [0.80, 1.25]，且上下限都被真实触达。"""
    grid = [i / 40 for i in range(41)]
    seen = set()
    for drive in ALL_DRIVES:
        for v in grid:
            for a in grid:
                m = em.emotion_multiplier(drive, (v, a))
                assert em.EMOTION_MULTIPLIER_FLOOR <= m <= em.EMOTION_MULTIPLIER_CEIL
                seen.add(round(m, 6))
    assert round(em.EMOTION_MULTIPLIER_FLOOR, 6) in seen
    assert round(em.EMOTION_MULTIPLIER_CEIL, 6) in seen


def test_emotion_multiplier_table_declares_shape_and_bounds():
    assert em.EMOTION_MULTIPLIER_BOUNDS == (0.80, 1.25)
    assert em.EMOTION_MULTIPLIER_RULES[d.DRIVE_INTIMACY]["axis"] == em.AXIS_NONE
    assert len(em.EMOTION_MULTIPLIER_RULES) == 6
    # 只有 5 条参与调制（不做全 6×2 矩阵，防调参面爆炸）
    assert sum(1 for r in em.EMOTION_MULTIPLIER_RULES.values() if r["axis"] != em.AXIS_NONE) == 5
    assert sum(1 for r in em.EMOTION_MULTIPLIER_RULES.values() if float(r["span"]) < 0) == 1


@pytest.mark.parametrize("junk", ["nonsense", 42, {}, {"valence": None}, (1, 2, 3), {"arousal": "x"}])
@pytest.mark.parametrize("drive", ["longing", "affection"])
def test_emotion_multiplier_never_raises_on_junk_snapshot(drive, junk):
    assert em.emotion_multiplier(drive, junk) == 1.0


def test_emotion_multiplier_unknown_drive_is_one():
    assert em.emotion_multiplier("no_such_drive", (0.0, 1.0)) == 1.0


def test_snapshot_forms_agree():
    """同一份情绪，无论传 ``(v,a)`` 还是八维快照，乘子必须相同（快照形态不改变结果）。"""
    eight = {"mood": 0, "comfort": 0, "sensitivity": 100, "body_temp": 100, "anger": 100}
    valence, arousal = em.derive_readings(eight).values()
    assert (valence, arousal) == (0.0, 1.0)
    for drive in ALL_DRIVES:
        assert em.emotion_multiplier(drive, eight) == em.emotion_multiplier(drive, (valence, arousal))


# ══════════════════════ 3. 性格偏置表（±10% 硬封顶）

@pytest.mark.parametrize("drive", ALL_DRIVES)
def test_bias_zero_without_any_keyword(drive):
    assert em.personality_bias(None, None, drive) == 0.0
    assert em.personality_bias("", "", drive) == 0.0
    assert em.personality_bias("   ", "\t", drive) == 0.0


def test_bias_zero_for_unknown_drive_and_intimacy():
    text = "黏人话痨好奇细心撒娇"
    assert em.personality_bias(text, "", "no_such_drive") == 0.0
    assert em.personality_bias(text, "", d.DRIVE_INTIMACY) == 0.0  # intimacy 永不参与


@pytest.mark.parametrize(
    "drive,text,expect",
    [
        (d.DRIVE_LONGING, "她很黏人", +0.05),
        (d.DRIVE_LONGING, "她很独立", -0.05),
        (d.DRIVE_CONCERN, "做事细心", +0.05),
        (d.DRIVE_CONCERN, "有点粗心", -0.05),
        (d.DRIVE_AFFECTION, "爱撒娇", +0.05),
        (d.DRIVE_AFFECTION, "一副高冷", -0.05),
        (d.DRIVE_SHARING, "性格外向", +0.05),
        (d.DRIVE_SHARING, "为人沉默", -0.05),
        (d.DRIVE_CURIOSITY, "好奇心重", +0.05),
        (d.DRIVE_CURIOSITY, "思想守旧", -0.05),
    ],
)
def test_bias_direction_from_keyword(drive, text, expect):
    assert em.personality_bias(text, "", drive) == pytest.approx(expect)
    vec = em.personality_bias_vector(text, "")
    mult = em.personality_multiplier(drive, vec)
    assert mult == pytest.approx(1.0 + expect)
    assert 0.90 <= mult <= 1.10


def test_bias_hard_cap_pm_10_percent():
    """堆满同向关键词 ⇒ 恰夹在 ±0.10；乘子恒 ∈ [0.90, 1.10]；反向词组互相抵消。"""
    up = "黏人 念旧 长情 多情 依恋 舍不得 牵挂 重感情"
    down = "独立 自律 慢热 疏离 边界感 淡漠 心大 没心没肺"
    assert em.personality_bias(up, "", d.DRIVE_LONGING) == pytest.approx(em.BIAS_HARD_CAP)
    assert em.personality_bias(down, "", d.DRIVE_LONGING) == pytest.approx(-em.BIAS_HARD_CAP)
    assert em.personality_bias(up, down, d.DRIVE_LONGING) == 0.0
    assert em.BIAS_HARD_CAP == 0.10
    for text in (up, down, "，。！？", "混合黏人与独立"):
        vec = em.personality_bias_vector(text, "")
        assert tuple(vec) == d.DRIVE_ALL_KEYS
        assert all(-em.BIAS_HARD_CAP <= b <= em.BIAS_HARD_CAP for b in vec.values())
        for drive in ALL_DRIVES:
            m = em.personality_multiplier(drive, vec)
            assert em.BIAS_MULTIPLIER_BOUNDS[0] <= m <= em.BIAS_MULTIPLIER_BOUNDS[1]


def test_bias_zero_for_non_chinese_text():
    """非中文性格文本 ⇒ 恒 0（不猜，设计 §3.3「认不出的默认」）。"""
    vec = em.personality_bias_vector("clingy, talkative, curious and caring", "warm and chatty")
    assert vec == dict.fromkeys(ALL_DRIVES, 0.0)


def test_bias_vector_is_pure_and_default_free():
    vec = em.personality_bias_vector("黏人又好奇", "")
    assert vec == em.personality_bias_vector("黏人又好奇", "")  # 确定性
    assert vec[d.DRIVE_INTIMACY] == 0.0
    assert vec != em.personality_bias_vector("", "")           # 只由文本决定，非恒零表
    assert em.personality_multiplier(d.DRIVE_LONGING, None) == 1.0  # 缺省向量 ⇒ 恰 1.0
    assert em.personality_multiplier(d.DRIVE_LONGING, {}) == 1.0
    assert em.personality_multiplier("no_such_drive", vec) == 1.0


def test_combined_multiplier_total_gate_pm_25_percent():
    """情绪 × 性格后再夹总闸 ⇒ 总偏离 ≤ ±25%（§3.1 末行「最终再夹一道总闸」）。"""
    bias_up = em.personality_bias_vector("黏人 念旧 长情 多情 依恋", "")
    assert em.personality_multiplier(d.DRIVE_LONGING, bias_up) == pytest.approx(1.10)
    assert em.combined_multiplier(d.DRIVE_LONGING, (0.0, 0.5), bias_up) == em.EMOTION_MULTIPLIER_CEIL

    bias_down = em.personality_bias_vector("高冷 冷淡 矜持 冰山 克制 端着", "")
    assert em.personality_multiplier(d.DRIVE_AFFECTION, bias_down) == pytest.approx(0.90)
    assert em.combined_multiplier(d.DRIVE_AFFECTION, (0.0, 0.5), bias_down) == em.EMOTION_MULTIPLIER_FLOOR

    for drive in ALL_DRIVES:  # 两参都缺省 ⇒ 恰 1.0（M0「零行为」的唯一支点）
        assert em.combined_multiplier(drive) == 1.0
        assert em.combined_multiplier(drive, None, None) == 1.0


def test_combined_grid_within_total_gate():
    grid = [i / 10 for i in range(11)]
    for drive in ALL_DRIVES:
        for v in grid:
            for a in grid:
                for bias in (-0.10, -0.05, 0.0, 0.05, 0.10):
                    m = em.combined_multiplier(drive, (v, a), {drive: bias})
                    assert em.EMOTION_MULTIPLIER_FLOOR <= m <= em.EMOTION_MULTIPLIER_CEIL


# ══════════════════════ 4. settle_level 缺省参 ⇒ 与 HEAD 逐字节一致

@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_default_args_match_head_byte_for_byte(case):
    level, drive_key, cursor, now = case
    want_level, want_cursor = _legacy_settle_level(level, drive_key, cursor, now)
    got_level, got_cursor = d.settle_level(level, drive_key, cursor, now)
    assert _bits(got_level) == _bits(want_level), (got_level, want_level)
    assert got_cursor == want_cursor


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_explicit_none_snapshot_matches_head_byte_for_byte(case):
    level, drive_key, cursor, now = case
    want_level, want_cursor = _legacy_settle_level(level, drive_key, cursor, now)
    got_level, got_cursor = d.settle_level(level, drive_key, cursor, now, None)
    assert _bits(got_level) == _bits(want_level)
    assert got_cursor == want_cursor


def test_neutral_snapshot_matches_head_byte_for_byte():
    """显式传中性快照（乘子恰 1.0）⇒ 也必须与 HEAD 逐字节相同（含留痕路径）。"""
    neutral = {"valence": 0.5, "arousal": 0.5}
    for level, drive_key, cursor, now in _CASES:
        want_level, want_cursor = _legacy_settle_level(level, drive_key, cursor, now)
        got_level, got_cursor = d.settle_level(level, drive_key, cursor, now, neutral)
        assert _bits(got_level) == _bits(want_level), (drive_key, got_level, want_level)
        assert got_cursor == want_cursor


def test_snapshot_signature_is_optional_and_back_compatible():
    sig = inspect.signature(d.settle_level)
    assert sig.parameters["emotion_snapshot"].default is None
    assert list(sig.parameters) == ["level", "drive_key", "last_settled_at", "now", "emotion_snapshot"]
    # 旧的 4 位置参数调用照旧可用
    assert d.settle_level(1.0, "longing", _bj(9), _bj(10)) == _legacy_settle_level(
        1.0, "longing", _bj(9), _bj(10)
    )


def test_drives_constant_literals_unchanged():
    """三张参数表 + 封顶 + 夜间窗口 + 时区偏移的字面值逐个钉死（改一个都算越界）。"""
    assert d.DRIVE_GROWTH_PER_HOUR == _LEGACY_GROWTH
    assert d.DRIVE_NIGHT_MULTIPLIER == _LEGACY_NIGHT
    assert d.DRIVE_OPEN_RELEASE_RATIO == _LEGACY_RELEASE
    assert d.LEVEL_MAX == 100.0
    assert (d.NIGHT_START_HOUR, d.NIGHT_END_HOUR) == (23, 7)
    assert d._BJ_TZ_OFFSET_HOURS == 8
    assert d.DRIVE_CANDIDATE_KEYS == d.DRIVE_ALL_KEYS[:5]
    assert d.DRIVE_TO_INTENT == {
        "longing": "check_in", "concern": "follow_up", "sharing": "share_self",
        "curiosity": "interest_hook", "affection": "recall_shared",
    }
    # 调制层的封顶值不得反过来写进 drives 的表（drives 的表仍是批 3 原值）
    assert all(0.020 <= v <= 0.090 for v in d.DRIVE_GROWTH_PER_HOUR.values())


def test_other_pure_functions_untouched():
    assert d.release_open(50.0, "longing") == pytest.approx(50.0 * (1.0 - 0.35))
    assert d.release_full(88.0) == 0.0
    assert d.top_candidate_drive({"longing": 10.0, "intimacy": 99.0}) == "longing"
    assert d.is_night_hour(23) and d.is_night_hour(6) and not d.is_night_hour(12)


def test_snapshot_scales_growth_only_and_keeps_cursor():
    """带快照 ⇒ 只有 level 被乘子缩放；游标、封顶、返回形状一律不变。"""
    cursor, now = _bj(9, 0), _bj(15, 0)
    base_level, base_cursor = d.settle_level(10.0, d.DRIVE_LONGING, cursor, now)
    up_level, up_cursor = d.settle_level(10.0, d.DRIVE_LONGING, cursor, now, (0.0, 0.5))     # ×1.25
    flat_level, flat_cursor = d.settle_level(10.0, d.DRIVE_LONGING, cursor, now, (1.0, 0.5))  # ×1.00
    assert up_cursor == flat_cursor == base_cursor == now
    assert _bits(flat_level) == _bits(base_level)
    assert up_level > base_level
    assert (up_level - 10.0) / (base_level - 10.0) == pytest.approx(1.25, rel=1e-9)
    capped, _ = d.settle_level(99.0, d.DRIVE_LONGING, _bj(9, 0), _bj(20, 0), (0.0, 0.5))
    assert capped <= d.LEVEL_MAX


def test_snapshot_path_stays_idempotent():
    """同一 (游标, now, 快照) 重复调用必须相等——乘子取自那份快照，不随时间漂移（§3.1 硬约束）。"""
    cursor, now = _bj(22, 0, day=2), _bj(3, 0, day=3)   # 整段夜间
    snap = {"valence": 0.2, "arousal": 0.7}
    first, c1 = d.settle_level(5.0, d.DRIVE_SHARING, cursor, now, snap)
    second, c2 = d.settle_level(5.0, d.DRIVE_SHARING, cursor, now, snap)
    third, _ = d.settle_level(first, d.DRIVE_SHARING, c2, now, snap)
    assert _bits(first) == _bits(second)
    assert c1 == c2 == now
    assert third == first  # 游标已＝now ⇒ 增量 0


def test_early_return_paths_ignore_snapshot():
    """游标为 None / 时钟回拨两条早退路径不经过乘子 ⇒ 与 HEAD 完全一致且不留痕。"""
    assert d.settle_level(12.5, "longing", None, _bj(9, 0), (0.0, 0.0)) == (12.5, _bj(9, 0))
    assert d.settle_level(12.5, "longing", _bj(10, 0), _bj(9, 0), (0.0, 0.0)) == (12.5, _bj(10, 0))


def test_unknown_drive_with_snapshot_still_zero_growth():
    level, cursor = d.settle_level(20.0, "no_such_drive", _bj(9, 0), _bj(12, 0), (0.0, 1.0))
    assert level == 20.0 and cursor == _bj(12, 0)


# ══════════════════════ 5. 影子档：只留痕 / 只读 / 异常隔离

@pytest.fixture(autouse=True)
def _never_write_real_trace(monkeypatch):
    """全文件级保险：真 ``obs_event`` 一律先换成空操作（连 trace 队列都不进，更不碰任何库）。"""
    import app.memory.observability as obs

    monkeypatch.setattr(obs, "obs_event", lambda *a, **k: None)


@pytest.fixture
def obs_spy(monkeypatch):
    """在空操作之上再换成计数器——M0 的留痕必须经过它，但只计数、不落任何真实写口。"""
    calls: list[tuple] = []

    def fake(character_id, metric, detail, kind=None):
        calls.append((character_id, metric, detail, kind))

    import app.memory.observability as obs

    monkeypatch.setattr(obs, "obs_event", fake)
    return calls


@pytest.fixture
def info_spy(monkeypatch):
    msgs: list[str] = []

    def record(*args, **kwargs):  # 按 logging 的调用约定取格式化后的正文
        msg = str(args[0]) if args else ""
        if len(args) > 1:
            msg = msg % args[1:]
        msgs.append(msg)

    monkeypatch.setattr(em._logger, "info", record)
    return msgs


def test_default_path_emits_nothing(obs_spy, info_spy):
    d.settle_level(10.0, d.DRIVE_LONGING, _bj(9, 0), _bj(15, 0))
    assert obs_spy == [] and info_spy == []
    d.settle_level(10.0, d.DRIVE_LONGING, _bj(9, 0), _bj(15, 0), None)
    assert obs_spy == [] and info_spy == []


def test_snapshot_path_traces_without_changing_return(obs_spy, info_spy, monkeypatch):
    cursor, now = _bj(9, 0), _bj(15, 0)
    traced = d.settle_level(10.0, d.DRIVE_LONGING, cursor, now, (0.0, 0.5))
    assert len(obs_spy) == 1 and len(info_spy) == 1
    character_id, metric, detail, kind = obs_spy[0]
    assert metric == em.SHADOW_METRIC and kind == "shadow"
    assert detail["drive_key"] == d.DRIVE_LONGING and detail["multiplier"] == 1.25
    assert detail["valence"] == 0.0 and detail["new_level"] == round(traced[0], 4)
    assert character_id is None and detail["bias"] == 0.0  # M0 没有调用方 ⇒ 不传角色、不传偏置
    assert "emotion_mod" in info_spy[0]
    # 关掉留痕出口后返回值必须**逐字节**相同（留痕不参与计算）
    monkeypatch.setattr(em, "shadow_trace_modulation", lambda *a, **k: None)
    silent = d.settle_level(10.0, d.DRIVE_LONGING, cursor, now, (0.0, 0.5))
    assert _bits(silent[0]) == _bits(traced[0]) and silent[1] == traced[1]


def test_trace_reads_only_never_mutates(obs_spy):
    snap = {"valence": 0.1, "arousal": 0.9}
    vec = {d.DRIVE_LONGING: 0.10}
    snap_before, vec_before = dict(snap), dict(vec)
    assert em.shadow_trace_modulation(d.DRIVE_LONGING, snap, vec, character_id=7) is None
    assert snap == snap_before and vec == vec_before
    assert obs_spy[0][0] == 7 and obs_spy[0][2]["bias"] == 0.1


@pytest.mark.parametrize("mode", ["obs_raises", "log_raises", "import_fails", "note_raises"])
def test_trace_exceptions_are_swallowed(obs_spy, monkeypatch, mode):
    import app.memory.observability as obs

    if mode == "obs_raises":
        monkeypatch.setattr(
            obs, "obs_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
    elif mode == "log_raises":
        def boom(*a, **k):
            raise RuntimeError("log boom")

        monkeypatch.setattr(em._logger, "info", boom)
    elif mode == "import_fails":
        monkeypatch.setitem(sys.modules, "app.memory.observability", None)
    else:
        monkeypatch.setattr(
            em, "modulation_note", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("note boom"))
        )

    assert em.shadow_trace_modulation(d.DRIVE_LONGING, (0.0, 0.0)) is None
    # 结算路径同样不得被留痕失败拖下水（水位与游标照常）
    level, cursor = d.settle_level(10.0, d.DRIVE_LONGING, _bj(9, 0), _bj(15, 0), (0.0, 0.5))
    assert cursor == _bj(15, 0)
    assert 10.0 < level <= d.LEVEL_MAX


def test_modulation_note_is_deterministic_and_bounded():
    note = em.modulation_note(d.DRIVE_LONGING, (0.0, 0.5))
    assert note == em.modulation_note(d.DRIVE_LONGING, (0.0, 0.5))
    assert note == em.modulation_note(d.DRIVE_LONGING, {"valence": 0.0, "arousal": 0.5})
    assert len(note) <= 160
    assert "emotion_mod" in note and "mult=1.2500" in note and "valence=0.0000" in note
    assert any(ch.isdigit() for ch in note)


def test_module_has_no_io_surface():
    """「派生读数不落列不落表」的结构性证明：本模块源码里没有任何 ORM/DB/HTTP 入口。"""
    src = inspect.getsource(em)
    for banned in (
        "sqlalchemy", "AsyncSession", "db.add", "commit", "create_all", "select(", "INSERT",
        "requests", "httpx", "relational_drives", "character_states", "Column", "Mapped[",
    ):
        assert banned not in src, banned
    assert em._EIGHT_DIMS == ("mood", "comfort", "sensitivity", "body_temp", "anger")


def test_module_does_not_touch_flag_layer():
    """M0 禁止新增 flag：本模块不 import flag 目录、不读 AGENT_FLAGS。"""
    src = inspect.getsource(em)
    assert "AGENT_FLAGS" not in src and "agent_flags" not in src and "flag_catalog" not in src


def test_production_callers_still_pass_no_snapshot():
    """M0 的「调用方一处都不传」：全仓（app/ 与 scripts/）除本次三个文件外无人提及新参数。"""
    backend = Path(d.__file__).resolve().parents[3]
    hits = []
    for path in list(backend.glob("app/**/*.py")) + list(backend.glob("scripts/**/*.py")):
        if path.name in {"drives.py", "emotion_modulation.py"}:
            continue
        if "emotion_snapshot" in path.read_text(encoding="utf-8", errors="replace"):
            hits.append(path.name)
    assert hits == []
