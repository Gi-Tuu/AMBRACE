# -*- coding: utf-8 -*-
"""A4 批 7 尾追（2026-10-14）：**性格偏置接进 on 档增速**（bias_vector）。

用户 2026-10-14 拍板按批 7 原设计补齐（设计 §3.1 公式第 4 项 / §3.3 / M1 里程碑②「性格偏置与
情绪乘子同一把开关一起放」）。钉住七条硬口径（改任一条即红）：
1. ``on`` ∧ 白名单：service 已取到的 ``bias_vec`` 真被传进**真实** ``drives.settle_level`` 并进入
   **增速**（正/负/中性三种偏置都量得出来）；
2. **growth 与 trace 同源**：留痕 ``multiplier`` == 实际改变水位的乘子 ==
   ``combined_multiplier(drive_key, snapshot, bias_vector)``（同一次口径，不再分成两台尺）；
3. 封顶**沿用现成纯函数**：单因子 ±0.10、总闸 [0.80, 1.25]；越界被夹、脏值按 0.0，本单不写新数学；
4. 缺省旧调用**逐字节等价**：不传偏置 / 传 None / 无快照时传偏置 ⇒ 水位与游标 bits 相同；
5. ``off`` / ``shadow`` / ``on`` 桶外：``settle_level`` 收到的调用**不带任何 keyword**，落库等于不调制；
6. 幂等 / 游标 / 夜间倍率 / ``LEVEL_MAX`` 不因偏置而变；留痕每行一条、归属角色不丢、不重复；
7. 取数异常与留痕失败（``obs_event`` 抛异常）带着偏置也**绝不改变水位**。

纪律：纯合成夹具，零业务库、零迁移、零网络、零 LLM；``obs_event`` 全程 monkeypatch。
"""
from __future__ import annotations

import asyncio
import inspect
import struct
from datetime import datetime, timedelta

import pytest

from app.application import relational_drive_service as rds
from app.domain.relational import drives
from app.domain.relational import emotion_modulation as emod

_NOW = datetime(2026, 10, 14, 6, 0, 0)          # naive UTC ＝ 北京 14:00（白天段）
_CURSOR = _NOW - timedelta(hours=10)            # 北京 04:00 起算：3 小时夜间 ＋ 7 小时白天
# longing／affection 只吃 valence 的「偏低」一侧 ⇒ 0.90 时情绪乘子恰 1.0，偏置成为唯一变量
_SNAPSHOT = {"valence": 0.90, "arousal": 0.80}
_GRAY_CHAR = 13                                 # pacing.OUTREACH_PACING_GRAY_CHARS
_USER = 3
_TOL = {"rel": 1e-9}

_LONGING_UP = {drives.DRIVE_LONGING: 0.10}
_ALL_UP = dict.fromkeys(drives.DRIVE_ALL_KEYS, 0.10)
_ALL_ZERO = dict.fromkeys(drives.DRIVE_ALL_KEYS, 0.0)
_SPECS = [(drives.DRIVE_LONGING, 10.0), (drives.DRIVE_SHARING, 4.0)]


def _bits(value: float) -> bytes:
    return struct.pack(">d", float(value))


class _Row:
    def __init__(self, key, level, settled_at=None):
        self.drive_key = key
        self.level = level
        self.last_settled_at = settled_at or _CURSOR


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.flushes = 0

    async def execute(self, *_a, **_k):  # pragma: no cover - _fetch_rows 已打桩
        raise AssertionError("本测试把 _fetch_rows 打桩了，不该走到真 execute")

    async def flush(self):
        self.flushes += 1

    def add(self, *_a, **_k):
        return None


def _rows(*specs):
    """按**真实角色号**键行的合成行集（键错一个字符，settle() 会自己去建惰性新行）。"""
    return {_GRAY_CHAR: [_Row(key, level) for key, level in (specs or _SPECS)]}


def _state(rows_by_char):
    return [(r.drive_key, _bits(r.level), r.last_settled_at) for r in rows_by_char[_GRAY_CHAR]]


@pytest.fixture(autouse=True)
def _never_write_real_trace(monkeypatch):
    import app.memory.observability as obs
    monkeypatch.setattr(obs, "obs_event", lambda *a, **k: None)


@pytest.fixture
def obs_spy(monkeypatch):
    calls: list[tuple] = []

    def fake(character_id, metric, detail, kind=None):
        calls.append((character_id, metric, detail, kind))

    import app.memory.observability as obs
    monkeypatch.setattr(obs, "obs_event", fake)
    return calls


def _wire(monkeypatch, rows_by_char, mode, gray_hit=True, bias=None, snapshot=_SNAPSHOT):
    """档位可控地接线；``settle_level`` 与留痕出口**全部走真实现**（只换掉 IO）。"""
    async def _fetch(_db, character_id, _user_id):
        return list(rows_by_char.get(int(character_id), []))

    async def _snap(*_a, **_k):
        return snapshot

    async def _vec(*_a, **_k):
        return bias

    monkeypatch.setattr(rds, "shadow_enabled", lambda: True)
    monkeypatch.setattr(rds, "_fetch_rows", _fetch)
    monkeypatch.setattr(rds, "modulation_mode", lambda: mode)
    monkeypatch.setattr(rds, "_gray_hit", lambda *_a, **_k: gray_hit)
    monkeypatch.setattr(rds, "_fetch_emotion_snapshot", _snap)
    monkeypatch.setattr(rds, "_fetch_bias_vector", _vec)


def _settle(rows_by_char, character_id=_GRAY_CHAR):
    return asyncio.run(rds.settle(
        _FakeSession(rows_by_char.get(character_id, [])), character_id, _USER, _NOW))


def _base_increment(level: float, key: str) -> float:
    """同一 (游标, now) 下**完全不调制**的增量——乘子比率的公共分母。"""
    return drives.settle_level(level, key, _CURSOR, _NOW)[0] - level


def _undisturbed(*specs):
    """对照组：不传快照的旧调用逐行结算后的行集状态（水位与游标都对齐）。"""
    ref = _rows(*specs)
    for row in ref[_GRAY_CHAR]:
        row.level, row.last_settled_at = drives.settle_level(
            row.level, row.drive_key, _CURSOR, _NOW)
    return _state(ref)


def _emotion_only(*specs):
    """对照组：只带情绪快照、不带偏置（＝本参数接入前的 on 档形状）。"""
    ref = _rows(*specs)
    for row in ref[_GRAY_CHAR]:
        row.level, row.last_settled_at = drives.settle_level(
            row.level, row.drive_key, _CURSOR, _NOW, _SNAPSHOT)
    return _state(ref)


# ─────────── 1. 正 / 负 / 中性偏置真进增速（真实 service → 真实 settle_level） ───────────

def test_positive_bias_enters_growth_through_real_chain(obs_spy, monkeypatch):
    rows = _rows((drives.DRIVE_LONGING, 10.0))
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias=_LONGING_UP)
    _settle(rows)
    row = rows[_GRAY_CHAR][0]
    assert row.level != 10.0, "夹具没真的走到结算"
    emotion_only = drives.settle_level(10.0, drives.DRIVE_LONGING, _CURSOR, _NOW, _SNAPSHOT)[0]
    assert row.level > emotion_only, "+0.10 偏置必须真的抬升增速"
    ratio = (row.level - 10.0) / _base_increment(10.0, drives.DRIVE_LONGING)
    assert ratio == pytest.approx(1.10, **_TOL)


def test_negative_bias_lowers_growth_through_real_chain(obs_spy, monkeypatch):
    rows = _rows((drives.DRIVE_AFFECTION, 10.0))
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias={drives.DRIVE_AFFECTION: -0.10})
    _settle(rows)
    row = rows[_GRAY_CHAR][0]
    base = _base_increment(10.0, drives.DRIVE_AFFECTION)
    assert base > 0
    assert 0.0 < row.level - 10.0 < base
    assert (row.level - 10.0) / base == pytest.approx(0.90, **_TOL)


def test_neutral_bias_identical_to_no_bias(obs_spy, monkeypatch):
    """偏置全 0 ⇒ 与「不传偏置」逐字节相同（同一把开关的中性位不动现网）。"""
    rows_a = _rows()
    _wire(monkeypatch, rows_a, rds.MODULATION_ON, bias=_ALL_ZERO)
    _settle(rows_a)
    rows_b = _rows()
    _wire(monkeypatch, rows_b, rds.MODULATION_ON, bias=None)
    _settle(rows_b)
    assert _state(rows_a) == _state(rows_b)
    assert _state(rows_b) == _emotion_only(*_SPECS), "对照组必须是真实的旧形状"


# ─────────── 2. growth 与 trace 同源（一台尺，不是两台） ───────────

def test_trace_multiplier_equals_growth_multiplier(obs_spy, monkeypatch):
    bias = {drives.DRIVE_LONGING: 0.10, drives.DRIVE_SHARING: 0.10,
            drives.DRIVE_AFFECTION: -0.10}
    specs = [(drives.DRIVE_LONGING, 10.0), (drives.DRIVE_SHARING, 4.0),
             (drives.DRIVE_AFFECTION, 6.0)]
    rows = _rows(*specs)
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias=bias)
    _settle(rows)

    assert len(obs_spy) == len(specs), "生效留痕每行一条，不得翻倍"
    by_drive = {call[2]["drive_key"]: call for call in obs_spy}
    assert set(by_drive) == {key for key, _ in specs}
    for key, level in specs:
        want = emod.combined_multiplier(key, _SNAPSHOT, bias)
        applied = (by_drive[key][2]["new_level"] - level) / _base_increment(level, key)
        assert by_drive[key][2]["multiplier"] == pytest.approx(want, abs=5e-5)
        assert applied == pytest.approx(want, **_TOL)
        assert by_drive[key][2]["bias"] == pytest.approx(bias[key], abs=5e-5)
        assert by_drive[key][0] == _GRAY_CHAR, "归属角色不因接偏置而丢"
    # 留痕里的 new_level 与真实落库值一致（trace 与 growth 同源，不是各算一遍）
    for row in rows[_GRAY_CHAR]:
        assert by_drive[row.drive_key][2]["new_level"] == round(float(row.level), 4)


def test_trace_bias_no_longer_frozen_at_zero(obs_spy, monkeypatch):
    """反向钉（接偏置前的形状）：trace 的 ``bias`` 必须记真实值，不再是恒 0.0 的两台尺。"""
    rows = _rows((drives.DRIVE_LONGING, 10.0))
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias={drives.DRIVE_LONGING: 0.05})
    _settle(rows)
    assert obs_spy and obs_spy[0][2]["bias"] == pytest.approx(0.05)


# ─────────── 3. 封顶沿用现成纯函数（不写新数学） ───────────

def test_bias_hard_cap_reused_not_reimplemented(obs_spy, monkeypatch):
    """越界偏置 0.9 与 0.10 产出**同一个**水位（±0.10 封顶仍由 emotion_modulation 负责）。"""
    rows = _rows((drives.DRIVE_LONGING, 10.0))
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias={drives.DRIVE_LONGING: 0.9})
    _settle(rows)
    fat = _state(rows)
    rows2 = _rows((drives.DRIVE_LONGING, 10.0))
    _wire(monkeypatch, rows2, rds.MODULATION_ON, bias=_LONGING_UP)
    _settle(rows2)
    assert fat == _state(rows2)


def test_total_gate_still_bounds_growth(obs_spy, monkeypatch):
    """情绪 1.25 ∧ 偏置 +0.10 ⇒ 总闸夹回 1.25（性格不得突破 ±25% 总偏离）。"""
    hot = {"valence": 0.0, "arousal": 0.5}      # longing 情绪乘子 = CEIL
    rows = _rows((drives.DRIVE_LONGING, 10.0))
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias=_LONGING_UP, snapshot=hot)
    _settle(rows)
    applied = (rows[_GRAY_CHAR][0].level - 10.0) / _base_increment(10.0, drives.DRIVE_LONGING)
    assert applied == pytest.approx(emod.EMOTION_MULTIPLIER_CEIL, **_TOL)
    assert obs_spy[0][2]["multiplier"] == emod.EMOTION_MULTIPLIER_CEIL


def test_dirty_bias_values_stay_conservative(obs_spy, monkeypatch):
    """脏值走现成纯函数口径：非数字 ⇒ 0.0；缺键 ⇒ 0.0 ⇒ 逐例等于「只有情绪乘子」。"""
    specs = [(drives.DRIVE_LONGING, 10.0), (drives.DRIVE_CURIOSITY, 2.0)]
    rows = _rows(*specs)
    _wire(monkeypatch, rows, rds.MODULATION_ON,
          bias={drives.DRIVE_LONGING: "abc", "unlistened_drive": 0.9})
    _settle(rows)
    assert _state(rows) == _emotion_only(*specs)


# ─────────── 4. 缺省旧调用逐字节等价 ───────────

def test_default_calls_bit_identical_after_wiring():
    key, level = drives.DRIVE_SHARING, 10.0
    legacy = drives.settle_level(level, key, _CURSOR, _NOW)
    no_snapshot_with_bias = drives.settle_level(
        level, key, _CURSOR, _NOW, None, bias_vector={key: 0.10})
    assert _bits(no_snapshot_with_bias[0]) == _bits(legacy[0]), "无快照时偏置必须完全不参与"
    assert no_snapshot_with_bias[1] == legacy[1]

    with_snapshot = drives.settle_level(level, key, _CURSOR, _NOW, _SNAPSHOT)
    explicit_none = drives.settle_level(
        level, key, _CURSOR, _NOW, _SNAPSHOT, bias_vector=None)
    empty_vec = drives.settle_level(level, key, _CURSOR, _NOW, _SNAPSHOT, bias_vector={})
    assert _bits(explicit_none[0]) == _bits(with_snapshot[0])
    assert _bits(empty_vec[0]) == _bits(with_snapshot[0])
    assert explicit_none[1] == with_snapshot[1] == empty_vec[1]


def test_bias_vector_is_keyword_only_with_none_default():
    sig = inspect.signature(drives.settle_level)
    assert sig.parameters["bias_vector"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["bias_vector"].default is None
    with pytest.raises(TypeError):
        drives.settle_level(1.0, drives.DRIVE_LONGING, _CURSOR, _NOW, _SNAPSHOT, _LONGING_UP)


# ─────────── 5. off / shadow / 桶外：调用形状、IO 边界与落库不变 ───────────

def _spy_kwargs(monkeypatch):
    seen: list[dict] = []
    real = drives.settle_level

    def _spy(*args, **kwargs):
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(drives, "settle_level", _spy)
    return seen


@pytest.mark.parametrize("mode", [rds.MODULATION_OFF, rds.MODULATION_SHADOW])
def test_off_and_shadow_pass_no_keyword_even_with_bias(monkeypatch, mode):
    rows = _rows()
    _wire(monkeypatch, rows, mode, bias=_ALL_UP)
    seen = _spy_kwargs(monkeypatch)
    _settle(rows)
    assert seen and all(not kw for kw in seen), f"{mode} 档必须仍是无快照旧调用"
    assert _state(rows) == _undisturbed(*_SPECS), f"{mode} 落库须等于不调制"


def test_on_outside_gray_ignores_bias_and_keeps_io_shape(monkeypatch):
    rows = _rows((drives.DRIVE_LONGING, 10.0))
    _wire(monkeypatch, rows, rds.MODULATION_ON, gray_hit=False, bias=_ALL_UP)
    seen = _spy_kwargs(monkeypatch)
    bias_reads = {"n": 0}

    async def _counting_vec(*_a, **_k):
        bias_reads["n"] += 1
        return _ALL_UP

    monkeypatch.setattr(rds, "_fetch_bias_vector", _counting_vec)
    _settle(rows)
    assert seen and all(not kw for kw in seen), "桶外不得把偏置传进 settle_level"
    assert bias_reads["n"] == 0, "桶外的偏置取数次数必须与改动前一致（0 次）"
    assert _state(rows) == _undisturbed((drives.DRIVE_LONGING, 10.0))


# ─────────── 6. 幂等 / 游标 / 夜间 / LEVEL_MAX 不因偏置而变 ───────────

def test_idempotent_and_cursor_semantics_with_bias(obs_spy, monkeypatch):
    rows = _rows((drives.DRIVE_LONGING, 10.0))
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias=_ALL_UP)
    first = _settle(rows)
    assert all(r.last_settled_at == _NOW for r in rows[_GRAY_CHAR])
    obs_spy.clear()
    second = _settle(rows)
    assert second == first, "同 now 二次结算增量必须为 0"
    assert obs_spy == [], "幂等：偏置不得让留痕随重复调用累积"


def test_night_multiplier_and_level_max_survive_bias():
    night_now = datetime(2026, 10, 14, 17, 0, 0)          # UTC 17:00 ＝ 北京 01:00（夜间）
    night = drives.settle_level(0.0, drives.DRIVE_LONGING, night_now - timedelta(hours=1),
                                night_now, _SNAPSHOT, bias_vector=_ALL_UP)
    day = drives.settle_level(0.0, drives.DRIVE_LONGING, _NOW - timedelta(hours=1),
                              _NOW, _SNAPSHOT, bias_vector=_ALL_UP)
    assert 0 < night[0] < day[0], "夜间倍率必须仍作用于带偏置的增速"
    assert night[0] == pytest.approx(day[0] * drives.DRIVE_NIGHT_MULTIPLIER[drives.DRIVE_LONGING],
                                     **_TOL)
    capped = drives.settle_level(99.9, drives.DRIVE_LONGING, _NOW - timedelta(days=400), _NOW,
                                 {"valence": 0.0, "arousal": 0.5}, bias_vector=_ALL_UP)
    assert capped[0] == drives.LEVEL_MAX


def test_unknown_drive_key_never_grows_with_bias():
    lv, cur = drives.settle_level(7.0, "nope", _CURSOR, _NOW, _SNAPSHOT,
                                  bias_vector={"nope": 0.10, drives.DRIVE_LONGING: 0.10})
    assert lv == 7.0 and cur == _NOW


# ─────────── 7. 取数异常 / 留痕失败：绝不改变水位 ───────────

def test_bias_fetch_missing_degrades_to_emotion_only(obs_spy, monkeypatch):
    """偏置取不到（None）⇒ 退回「只有情绪乘子」，既不报错也不清零。"""
    rows = _rows((drives.DRIVE_LONGING, 10.0))
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias=None)
    _settle(rows)
    assert _state(rows) == _emotion_only((drives.DRIVE_LONGING, 10.0))
    assert obs_spy[0][2]["bias"] == 0.0


def test_bias_fetch_exception_means_no_modulation(monkeypatch):
    """取偏置抛异常 ⇒ 纵深防御把本次整体退回不调制（与批 7 M1 同口径）。"""
    rows = _rows((drives.DRIVE_LONGING, 5.0))
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias=_ALL_UP)

    async def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(rds, "_fetch_bias_vector", _boom)
    _settle(rows)
    assert _state(rows) == _undisturbed((drives.DRIVE_LONGING, 5.0))


def test_trace_failure_with_bias_never_changes_level(monkeypatch):
    import app.memory.observability as obs
    rows = _rows()
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias=_ALL_UP)
    _settle(rows)
    ok = _state(rows)

    def _boom(*_a, **_k):
        raise RuntimeError("trace queue down")

    monkeypatch.setattr(obs, "obs_event", _boom)
    rows2 = _rows()
    _wire(monkeypatch, rows2, rds.MODULATION_ON, bias=_ALL_UP)
    _settle(rows2)
    assert _state(rows2) == ok


# ══════════ 8. 偏置输入容错（2026-10-14 小尾：非 mapping 真值不得打断结算）══════════
# 上一单 ⑦ 第 1 条登记的抛错面：``personality_multiplier`` 只认 dict-or-None，真值 str/list/数字/
# 任意对象 ⇒ ``AttributeError``；偏置接进增速后这条抛错**能打断水位结算**（改前已实测复现）。
# 本段钉四条：① 非 mapping ⇒ 中性 1.0（真 service／真 settle_level 不抛）；② dict 的合法与非法
# 数值口径、±0.10 与总闸**一字不变**；③ 留痕侧同类访问（modulation_note／shadow_trace）既不因
# 形态问题丢整条留痕、也不丢角色归属，且 bias 字段与增速同源；④ 容错只走 isinstance+get，
# **不反射读取入参对象的任意属性**。

_NON_MAPPING = [
    ("str", "longing=0.10"),
    ("list-of-pairs", [(drives.DRIVE_LONGING, 0.10)]),
    ("list-of-floats", [0.10]),
    ("tuple", (0.10, 0.10)),
    ("set", {0.10}),
    ("float", 0.10),
    ("int", 1),
    ("bool", True),
    ("bytes", b"0.10"),
    ("object", object()),
]

_FALSY_LEGACY = [None, {}, "", [], (), 0, 0.0, False]


class _Probing:
    """任何属性读取都立刻报警——用来证明容错没有 getattr 探测用户对象。"""

    def __init__(self):
        self.read: list[str] = []

    def __getattr__(self, name):
        self.read.append(name)
        raise AssertionError(f"偏置容错不该读用户对象属性：{name}")


@pytest.mark.parametrize("label,offender", _NON_MAPPING, ids=[c[0] for c in _NON_MAPPING])
def test_non_mapping_bias_neither_breaks_settlement_nor_trace(obs_spy, monkeypatch, label, offender):
    specs = [(drives.DRIVE_LONGING, 10.0), (drives.DRIVE_SHARING, 4.0)]
    assert emod.personality_multiplier(drives.DRIVE_LONGING, offender) == 1.0, f"{label} 须中性"
    ref = _emotion_only(*specs)          # 对照组自身也会留痕，先算完再清空
    obs_spy.clear()
    rows = _rows(*specs)
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias=offender)
    _settle(rows)                                     # 真 service → 真 settle_level：不得抛
    assert _state(rows) == ref, f"{label} 偏置不得影响水位/游标"
    assert len(obs_spy) == len(specs), f"{label} 容错不得吞掉留痕"
    for call in obs_spy:
        key = call[2]["drive_key"]
        assert call[0] == _GRAY_CHAR, f"{label} 归属角色不得丢"
        assert call[2]["bias"] == 0.0, f"{label} trace 的 bias 须与增速同源地记 0"
        assert call[2]["multiplier"] == pytest.approx(
            emod.emotion_multiplier(key, _SNAPSHOT), abs=5e-5)


def test_non_mapping_bias_settles_bit_identical_to_no_bias():
    for _, offender in _NON_MAPPING:
        for key, level in _SPECS:
            with_bias = drives.settle_level(level, key, _CURSOR, _NOW, _SNAPSHOT, bias_vector=offender)
            plain = drives.settle_level(level, key, _CURSOR, _NOW, _SNAPSHOT)
            assert _bits(with_bias[0]) == _bits(plain[0]), f"{offender!r} 改变了水位 bits"
            assert with_bias[1] == plain[1]
            assert emod.combined_multiplier(key, _SNAPSHOT, offender) \
                == emod.combined_multiplier(key, _SNAPSHOT, None)


def test_falsy_bias_keeps_legacy_neutral_value():
    """旧行为：假值偏置（含 ``None``/``{}``/空串/空列表/0/False）⇒ 恰 1.0，改前改后同值。"""
    for legacy in _FALSY_LEGACY:
        assert emod.personality_multiplier(drives.DRIVE_LONGING, legacy) == 1.0
        assert emod.combined_multiplier(drives.DRIVE_LONGING, _SNAPSHOT, legacy) == 1.0


def test_dict_bias_numeric_and_dirty_semantics_unchanged():
    """dict 分支逐条钉回旧口径：合法值照乘、脏值归 0、±0.10 与总闸封顶不变（本单不写新数学）。

    ``NaN`` 按现成 ``_clamp`` 落在 **+0.10 上沿**（改前改后同一口径，本单不顺手改它）。
    """
    key = drives.DRIVE_LONGING
    cases = [
        ({key: 0.10}, 1.10), ({key: -0.10}, 0.90), ({key: 0.15}, 1.10), ({key: -0.15}, 0.90),
        ({key: 0.9}, 1.10), ({key: -0.9}, 0.90), ({key: 0.0}, 1.0), ({key: "0.10"}, 1.10),
        ({key: "abc"}, 1.0), ({key: None}, 1.0), ({key: True}, 1.0), ({key: [0.1]}, 1.0),
        ({"unlistened": 0.9}, 1.0), ({key: float("nan")}, 1.10),
    ]
    for vec, want in cases:
        assert emod.personality_multiplier(key, vec) == pytest.approx(want, abs=1e-12), f"{vec} 乘子变了"
    assert emod.combined_multiplier(key, {"valence": 0.0, "arousal": 0.5}, {key: 0.10}) \
        == emod.EMOTION_MULTIPLIER_CEIL, "总闸 [0.80,1.25] 仍由现成常量夹住"


def test_note_text_and_dirty_dict_bias_keep_trace_and_owner(obs_spy, monkeypatch):
    """留痕侧同类访问审计结论：kv 串格式逐字节不变；脏值只让 bias 归 0，不得吞掉整条留痕。"""
    key = drives.DRIVE_LONGING
    assert emod.modulation_note(key, _SNAPSHOT, {key: 0.05}) == (
        "emotion_mod drive=longing mult=1.0500 valence=0.9000 arousal=0.8000 bias=+0.0500")
    assert emod.modulation_note(key, _SNAPSHOT, None).endswith("bias=+0.0000")
    assert emod.modulation_note(key, _SNAPSHOT, {key: "abc"}).endswith("bias=+0.0000")
    assert emod.modulation_note(key, _SNAPSHOT, "junk").endswith("bias=+0.0000")

    ref = _emotion_only((key, 10.0))
    obs_spy.clear()
    rows = _rows((key, 10.0))
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias={key: "0.10abc"})
    _settle(rows)
    assert _state(rows) == ref
    assert len(obs_spy) == 1, "脏值不得把整条生效留痕一起吞掉"
    assert obs_spy[0][0] == _GRAY_CHAR and obs_spy[0][2]["bias"] == 0.0


def test_non_dict_mapping_bias_keeps_growth_and_trace_on_one_ruler(obs_spy, monkeypatch):
    """Mapping 但非 dict（MappingProxyType／defaultdict）：增速本来就认它，trace 的 bias 不得恒 0。"""
    import collections
    import types
    key = drives.DRIVE_LONGING
    vec = types.MappingProxyType({key: 0.10})
    rows = _rows((key, 10.0))
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias=vec)
    _settle(rows)
    want = emod.combined_multiplier(key, _SNAPSHOT, vec)
    applied = (rows[_GRAY_CHAR][0].level - 10.0) / _base_increment(10.0, key)
    assert applied == pytest.approx(want, **_TOL)
    assert want == pytest.approx(1.10, **_TOL), "非 dict Mapping 不得被容错顺手降级"
    assert obs_spy[0][2]["multiplier"] == pytest.approx(want, abs=5e-5)
    assert obs_spy[0][2]["bias"] == pytest.approx(0.10)
    assert obs_spy[0][0] == _GRAY_CHAR
    dd = collections.defaultdict(float, {key: 0.10})
    assert emod.personality_multiplier(key, dd) == pytest.approx(1.10)


def test_bias_guard_reads_no_user_object_attributes(obs_spy, monkeypatch):
    """容错只允许 isinstance+get：任何 ``__getattr__`` 探测（含真链路）都算越界副作用。"""
    key = drives.DRIVE_LONGING
    p1 = _Probing()
    assert emod.personality_multiplier(key, p1) == 1.0 and p1.read == []
    p2 = _Probing()
    assert emod.modulation_note(key, _SNAPSHOT, p2).endswith("bias=+0.0000") and p2.read == []
    p3 = _Probing()
    emod.shadow_trace_modulation(key, _SNAPSHOT, bias_vector=p3, character_id=_GRAY_CHAR, new_level=11.0)
    assert p3.read == [] and len(obs_spy) == 1 and obs_spy[0][0] == _GRAY_CHAR
    p4 = _Probing()
    rows = _rows((key, 10.0))
    _wire(monkeypatch, rows, rds.MODULATION_ON, bias=p4)
    _settle(rows)
    assert p4.read == [], "真 service→settle_level 链路也不得读用户对象属性"
    assert _state(rows) == _emotion_only((key, 10.0))
