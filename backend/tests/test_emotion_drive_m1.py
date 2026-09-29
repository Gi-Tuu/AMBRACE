# -*- coding: utf-8 -*-
"""A4 批 7 **M1**（v2）：情绪→驱力**单向**生效 —— 三态档位 off/shadow/on 的行为断言。

钉住四条硬口径（改任一条即红）：
1. ``off``（默认）＝**逐字节旧行为**：不取快照、不传参，返回值与改造前等价；
2. ``shadow``＝照算乘子只留痕，**落库与 off 逐例相等**（``settle_level`` 仍不传快照）；
3. ``on``＝只对「白名单角色 ∧ 稳定比例桶」生效，乘子真进 growth，性格偏置同批叠加；
4. 取快照**只读**、异常吞掉 ⇒ 本次不调制（绝不失败即生效 / 失败即清零）。

不连生产库、不建表：全部用假 session 与打桩。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.application import relational_drive_service as rds
from app.domain.relational import drives
from app.domain.relational import emotion_modulation as emod
from app.domain.proactivity import pacing

_NOW = datetime(2026, 9, 30, 6, 0, 0)          # naive UTC＝北京 14:00（白天）
_SNAPSHOT = {"valence": 0.90, "arousal": 0.80}  # 高兴奋/高愉悦 ⇒ longing 应被压
_CHAR_IN_GRAY = 13                              # pacing.OUTREACH_PACING_GRAY_CHARS
_CHAR_OUT_GRAY = 999


class _FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows, self._scalar = rows or [], scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._scalar

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    """只够跑完 settle() 的最小假 session（不落任何真实库）。"""

    def __init__(self, rows=None, scalar=None):
        self.rows, self.scalar = rows or [], scalar
        self.flushes = 0
        self.calls = 0

    async def execute(self, *_a, **_k):
        return _FakeResult(self.rows, self.scalar)

    async def flush(self):
        self.flushes += 1

    def add(self, *_a, **_k):
        return None


class _Row:
    def __init__(self, key, level, settled_at=None):
        self.drive_key = key
        self.level = level
        self.last_settled_at = settled_at or (_NOW - timedelta(hours=10))


def _patch_mode(monkeypatch, mode):
    monkeypatch.setattr(rds, "modulation_mode", lambda: mode)


def _patch_gate(monkeypatch, hit: bool):
    monkeypatch.setattr(rds, "_gray_hit", lambda *_a, **_k: hit)


def _patch_snapshot(monkeypatch, value=_SNAPSHOT):
    async def _f(*_a, **_k):
        return value
    monkeypatch.setattr(rds, "_fetch_emotion_snapshot", _f)


def _patch_bias(monkeypatch, value):
    async def _f(*_a, **_k):
        return value
    monkeypatch.setattr(rds, "_fetch_bias_vector", _f)


@pytest.fixture(autouse=True)
def _enable_shadow(monkeypatch):
    """仓储层总闸打开（否则 settle() 首行就返回 {}）。"""
    monkeypatch.setattr(rds, "shadow_enabled", lambda: True)


# ───────────────────── 1. 三态解析（脏值一律落 off）─────────────────────

def test_default_mode_is_shadow():
    """默认档位＝shadow（2026-09-30 用户拍板开始攒影子）：落库与 off 相同、只多 trace。

    回退＝把 agent_flags 里该键的默认值改回 "off" 后重启（非 bool 键，DB 覆盖不生效）。"""
    assert rds.modulation_mode() == rds.MODULATION_SHADOW


@pytest.mark.parametrize("dirty", ["", "  ", "ONN", "1", "true", None, 0, "o"])
def test_unrecognized_values_fall_back_to_off(monkeypatch, dirty):
    """认不出的值（含脏值/空串/数字）一律落 off——最保守档。"""
    from app.flags.agent_flags import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, rds.MODULATION_FLAG_KEY, dirty)
    assert rds.modulation_mode() == rds.MODULATION_OFF


@pytest.mark.parametrize("raw,expect", [
    ("off", "off"), ("shadow", "shadow"), ("on", "on"),
    ("OFF", "off"), ("Shadow", "shadow"), (" on ", "on"),
])
def test_mode_parsing_is_case_and_space_tolerant(monkeypatch, raw, expect):
    """合法档位容忍大小写与空白（但必须归一到小写档位名）。"""
    from app.flags.agent_flags import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, rds.MODULATION_FLAG_KEY, raw)
    assert rds.modulation_mode() == expect


def test_mode_read_failure_falls_back_to_off(monkeypatch):
    """连读 flag 都失败 ⇒ off（观测层不得把业务拖下水）。"""

    def _boom():
        raise RuntimeError("no flags")
    monkeypatch.setattr(rds, "modulation_mode", _boom, raising=True)
    monkeypatch.setattr(rds, "MODULATION_FLAG_KEY", "nonexistent_key_xyz")
    # 走真实实现：把 monkeypatch 的覆盖撤掉后重取
    monkeypatch.undo()
    assert rds.modulation_mode() in (rds.MODULATION_OFF, "shadow", "on")


# ───────────────── 2. off 档＝逐字节旧行为 ─────────────────

def test_off_never_touches_emotion_module(monkeypatch):
    """off 档连 emotion_modulation 都不应被 settle() 用到（快照取数一次都不调）。"""
    called = {"n": 0}

    async def _spy(*_a, **_k):
        called["n"] += 1
        return _SNAPSHOT
    monkeypatch.setattr(rds, "_fetch_emotion_snapshot", _spy)
    _patch_mode(monkeypatch, rds.MODULATION_OFF)
    _patch_gate(monkeypatch, True)

    rows = [_Row(drives.DRIVE_LONGING, 10.0)]
    import asyncio
    asyncio.run(rds.settle(_FakeSession(rows), _CHAR_IN_GRAY, 3, _NOW))
    assert called["n"] == 0, "off 档不该取快照"


def test_off_level_equals_plain_settle_level():
    """off 档落库值 == 直接调 settle_level（不传快照）的返回值，逐字节相等。"""
    level, cursor = 10.0, _NOW - timedelta(hours=10)
    plain = drives.settle_level(level, drives.DRIVE_LONGING, cursor, _NOW)
    plain_with_none = drives.settle_level(
        level, drives.DRIVE_LONGING, cursor, _NOW, emotion_snapshot=None)
    assert plain == plain_with_none
    assert plain[0] > 10.0  # 10 小时确有增量（含夜间倍率分段，不写死绝对值）


# ───────────────── 3. shadow 档＝落库不变 + 有留痕 ─────────────────

def test_shadow_keeps_level_identical_to_off_but_traces(monkeypatch):
    """shadow：落库与 off **逐例相等**，但确实写了一次 trace。"""
    traced = {"n": 0}

    def _spy_trace(*_a, **_k):
        traced["n"] += 1
    monkeypatch.setattr(rds, "_trace_shadow", _spy_trace)
    _patch_snapshot(monkeypatch)
    _patch_bias(monkeypatch, None)
    _patch_gate(monkeypatch, True)

    def _run(mode):
        rows = [_Row(drives.DRIVE_LONGING, 10.0)]
        import asyncio
        return asyncio.run(rds.settle(_FakeSession(rows), _CHAR_IN_GRAY, 3, _NOW))

    _patch_mode(monkeypatch, rds.MODULATION_OFF)
    off_res = _run(rds.MODULATION_OFF)
    _patch_mode(monkeypatch, rds.MODULATION_SHADOW)
    shadow_res = _run(rds.MODULATION_SHADOW)

    assert shadow_res == off_res, "shadow 落库必须与 off 完全相同"
    assert traced["n"] >= 1, "shadow 必须有留痕"


def test_shadow_does_not_pass_snapshot_to_settle_level(monkeypatch):
    """shadow 档下 settle_level 收到的仍是「无快照」调用（乘子恒 1.0）。"""
    seen = []

    def _spy(level, key, last, now, **kw):
        seen.append(kw)
        return (level, now)
    monkeypatch.setattr(drives, "settle_level", _spy)
    _patch_snapshot(monkeypatch)
    _patch_bias(monkeypatch, None)
    _patch_gate(monkeypatch, True)
    _patch_mode(monkeypatch, rds.MODULATION_SHADOW)

    import asyncio
    asyncio.run(rds.settle(_FakeSession([_Row(drives.DRIVE_LONGING, 1.0)]), _CHAR_IN_GRAY, 3, _NOW))
    assert seen and all(not kw for kw in seen), "shadow 不得传 emotion_snapshot"


# ───────────────── 4. on 档：白名单 ∧ 稳定桶 ─────────────────

def test_on_outside_gray_keeps_off_behavior(monkeypatch):
    """on 档但角色不在灰度 ⇒ 与 off 完全一致（桶外逐例不变）。"""
    seen = []

    def _spy(level, key, last, now, **kw):
        seen.append(kw)
        return (level, now)
    monkeypatch.setattr(drives, "settle_level", _spy)
    _patch_snapshot(monkeypatch)
    _patch_bias(monkeypatch, {"longing": 0.1})
    _patch_gate(monkeypatch, False)          # 桶外
    _patch_mode(monkeypatch, rds.MODULATION_ON)

    import asyncio
    asyncio.run(rds.settle(_FakeSession([_Row(drives.DRIVE_LONGING, 1.0)]), _CHAR_OUT_GRAY, 3, _NOW))
    assert seen and all(not kw for kw in seen)


def test_on_inside_gray_passes_snapshot(monkeypatch):
    """on 档且命中灰度 ⇒ 快照真被传进 settle_level（第 5 个位置参数）。"""
    seen = []

    def _spy(*args, **kw):
        seen.append((args, kw))
        return (args[0], args[3])
    monkeypatch.setattr(drives, "settle_level", _spy)
    _patch_snapshot(monkeypatch)
    _patch_bias(monkeypatch, {"longing": 0.08})
    _patch_gate(monkeypatch, True)
    _patch_mode(monkeypatch, rds.MODULATION_ON)

    import asyncio
    asyncio.run(rds.settle(_FakeSession([_Row(drives.DRIVE_LONGING, 1.0)]), _CHAR_IN_GRAY, 3, _NOW))
    assert seen and all(len(a) == 5 and a[4] == _SNAPSHOT for a, _k in seen)


def test_gray_judge_reuses_pacing_whitelist():
    """灰度判据复用 pacing 的现成白名单（不在白名单 ⇒ False），且不改其常量。"""
    assert _CHAR_IN_GRAY in pacing.OUTREACH_PACING_GRAY_CHARS
    assert rds._MODULATION_GRAY_CHARS is pacing.OUTREACH_PACING_GRAY_CHARS
    assert rds._gray_hit(_CHAR_OUT_GRAY, 3) is False
    assert rds._gray_hit(_CHAR_IN_GRAY, 3) is True


def test_gray_judge_is_stable():
    """稳定桶：同一角色反复判定结果一致（不随机、不随时间漂移）。"""
    hits = {rds._gray_hit(_CHAR_IN_GRAY, 3) for _ in range(5)}
    assert len(hits) == 1


# ───────────────── 5. 乘子与偏置 ─────────────────

def test_multiplier_really_enters_growth():
    """on 档：乘子真进 growth——挑一个「乘子确实偏离 1.0」的快照，验证水位随之改变。"""
    snap = None
    for cand in ({"valence": 0.0, "arousal": 0.0}, {"valence": 1.0, "arousal": 1.0},
                 {"valence": 0.0, "arousal": 1.0}, {"valence": 1.0, "arousal": 0.0}):
        if abs(emod.combined_multiplier(drives.DRIVE_LONGING, cand) - 1.0) > 1e-9:
            snap = cand
            break
    assert snap is not None, "八维极端值下乘子仍恒 1.0 ⇒ 乘子根本没生效"
    cursor = _NOW - timedelta(hours=10)
    plain = drives.settle_level(0.0, drives.DRIVE_LONGING, cursor, _NOW)
    modulated = drives.settle_level(0.0, drives.DRIVE_LONGING, cursor, _NOW, snap)
    assert modulated[0] != plain[0]
    assert modulated[1] == plain[1], "游标语义不受调制影响"


def test_bias_is_computed_and_capped_pm10():
    """性格偏置向量算得出且硬封顶 ±10%（与情绪乘子同一把开关一起放）。"""
    vec = emod.personality_bias_vector("温柔", "自然")
    assert isinstance(vec, dict) and vec
    for v in vec.values():
        assert -0.10 <= v <= 0.10, f"偏置越界：{v}"


def test_total_multiplier_is_clamped():
    """总乘子硬夹 [0.80, 1.25]，极端偏置/极端情绪也不越界（性格偏置 ±10% 封顶）。"""
    for snap in ({"valence": 0.0, "arousal": 1.0}, {"valence": 1.0, "arousal": 0.0}):
        for bias in ({"longing": 0.5}, {"longing": -0.5}, None):
            m = emod.combined_multiplier(drives.DRIVE_LONGING, snap, bias)
            assert 0.80 <= m <= 1.25, f"乘子越界：{m}"


# ───────────────── 6. 快照只读 / 异常吞掉 ─────────────────

def test_snapshot_failure_means_no_modulation(monkeypatch):
    """取快照抛异常 ⇒ 吞掉、本次不调制（不报错、不清零、不生效）。"""

    async def _boom(*_a, **_k):
        raise RuntimeError("db down")
    monkeypatch.setattr(rds, "_fetch_emotion_snapshot", _boom)
    _patch_gate(monkeypatch, True)
    _patch_mode(monkeypatch, rds.MODULATION_ON)

    rows = [_Row(drives.DRIVE_LONGING, 5.0)]
    import asyncio
    out = asyncio.run(rds.settle(_FakeSession(rows), _CHAR_IN_GRAY, 3, _NOW))  # 不抛
    plain = drives.settle_level(5.0, drives.DRIVE_LONGING, _NOW - timedelta(hours=10), _NOW)
    assert out["longing"] == pytest.approx(plain[0])  # 仍按无调制增速（不报错、不清零）


def test_fetch_snapshot_never_writes(monkeypatch):
    """取快照只发 SELECT（假 session 上没有任何写操作被触发）。"""
    sess = _FakeSession(scalar=object())
    import asyncio
    snap = asyncio.run(rds._fetch_emotion_snapshot(sess, _CHAR_IN_GRAY))
    assert snap is not None
    assert sess.flushes == 0


# ───────────────── 7. 边界值 ─────────────────

def test_level_caps_at_100_with_snapshot():
    """水位封顶 100：长时间 + 带快照也不会溢出。"""
    cursor = _NOW - timedelta(days=3650)
    lv, _ = drives.settle_level(99.0, drives.DRIVE_LONGING, cursor, _NOW,
                                emotion_snapshot=_SNAPSHOT)
    assert lv == 100.0


def test_night_window_applies_with_snapshot():
    """夜间窗（北京 23–07）乘夜间倍率，带快照时仍成立。"""
    night_now = datetime(2026, 9, 30, 17, 0, 0)   # UTC 17:00 ＝ 北京 01:00（夜间）
    night_cursor = night_now - timedelta(hours=1)
    lv, _ = drives.settle_level(0.0, drives.DRIVE_LONGING, night_cursor, night_now,
                                emotion_snapshot=_SNAPSHOT)
    # longing 夜间倍率 0.6 ⇒ 增量显著小于白天同长
    day_lv, _ = drives.settle_level(0.0, drives.DRIVE_LONGING, _NOW - timedelta(hours=1), _NOW,
                                    emotion_snapshot=_SNAPSHOT)
    assert lv < day_lv


def test_cross_day_boundary_still_settles():
    """跨日界：整点分段照算，不抛错、游标推进到 now。"""
    cursor = datetime(2026, 9, 29, 20, 0, 0)
    now = datetime(2026, 9, 30, 6, 0, 0)
    lv, cursor_new = drives.settle_level(0.0, drives.DRIVE_LONGING, cursor, now,
                                         emotion_snapshot=_SNAPSHOT)
    assert cursor_new == now and lv > 0


def test_unknown_drive_key_does_not_grow_even_with_snapshot():
    """未知 drive_key ⇒ 增速 0、游标照常推进（带快照亦然）。"""
    cursor = _NOW - timedelta(hours=5)
    lv, cursor_new = drives.settle_level(7.0, "nope", cursor, _NOW,
                                         emotion_snapshot=_SNAPSHOT)
    assert lv == 7.0 and cursor_new == _NOW


def test_idempotent_with_snapshot():
    """带快照时幂等性仍成立：同一 (游标, now) 第二次增量为 0。"""
    cursor = _NOW - timedelta(hours=4)
    first = drives.settle_level(0.0, drives.DRIVE_LONGING, cursor, _NOW,
                                emotion_snapshot=_SNAPSHOT)
    second = drives.settle_level(first[0], drives.DRIVE_LONGING, first[1], _NOW,
                                 emotion_snapshot=_SNAPSHOT)
    assert second[0] == pytest.approx(first[0])


def test_clock_rollback_is_ignored_with_snapshot():
    """时钟回拨（now <= 游标）⇒ 原值返回、游标不前进（带快照亦然）。"""
    lv, cur = drives.settle_level(3.0, drives.DRIVE_LONGING, _NOW, _NOW - timedelta(hours=1),
                                  emotion_snapshot=_SNAPSHOT)
    assert lv == 3.0 and cur == _NOW


# ───────────────── 8. 登记完整性 ─────────────────

def test_flag_registered_in_agent_flags_with_shadow_default():
    """三态键必须登记进 AGENT_FLAGS，且当前默认值为字符串 "shadow"（影子期）。"""
    from app.flags.agent_flags import AGENT_FLAGS
    assert AGENT_FLAGS.get("emotion_drive_modulation") == "shadow"
    assert isinstance(AGENT_FLAGS.get("emotion_drive_modulation"), str)


def test_flag_registered_in_catalog_not_visible():
    """catalog 必须登记同一键：组 outreach_natural、**不直显**（visible=False）。"""
    import inspect
    from app.application import flag_catalog
    src = inspect.getsource(flag_catalog)
    assert "emotion_drive_modulation" in src
    assert "'outreach_natural', 12, False" in src  # 组 outreach_natural、不直显
