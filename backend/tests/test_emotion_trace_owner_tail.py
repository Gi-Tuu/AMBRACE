# -*- coding: utf-8 -*-
"""A4 批 7 尾追（2026-10-14）：**生效留痕的角色归属**（emotion_trace_tail）。

钉住六条硬口径（改任一条即红）：
1. ``on`` ＝ 真实 service → **真实** ``drives.settle_level`` → 真实 ``shadow_trace_modulation``
   → ``obs_event`` 收到的 ``character_id`` 就是本次结算的角色（不再是 None）；
2. 不同角色的留痕**不串味**：按 character_id 路由的假行集里，每行留痕只带自己那个角色；
3. ``off`` / ``shadow`` **不多留痕**：off＝0 条、shadow＝每行 1 条（与改动前同一数量），
   且 off/shadow 档下 ``settle_level`` 收到的调用**不带任何 keyword**（原样旧调用）；
4. 留痕失败（``obs_event`` 抛异常）**不改变水位与游标**，也不往上抛；
5. 同一游标重复结算**幂等**：第二起增量为 0，留痕条数不翻倍成「补算历史」；
6. 默认旧调用**逐字节等价**：``character_id`` 是 keyword-only 且缺省 None，
   不传 ⇒ 留痕里的角色仍是 None、水位/游标/乘子与本参数加入前完全相同。

纪律：纯合成夹具，零业务库、零迁移、零网络；``obs_event`` 全程 monkeypatch（连 trace 队列都不进）。
本文件**不碰**偏置数学（on 档 growth 是否含性格偏置属未决口径，只登记在派单报告里）。
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta

import pytest

from app.application import relational_drive_service as rds
from app.domain.relational import drives
from app.domain.relational import emotion_modulation as emod

_NOW = datetime(2026, 10, 14, 6, 0, 0)         # naive UTC ＝ 北京 14:00（白天段）
_CURSOR = _NOW - timedelta(hours=10)
_SNAPSHOT = {"valence": 0.90, "arousal": 0.80}  # 高愉悦/高兴奋 ⇒ longing 单边被压
_GRAY_CHAR = 13                                 # pacing.OUTREACH_PACING_GRAY_CHARS 白名单角色
_OTHER_CHAR = 14
_USER = 3


def _bits(value: float) -> bytes:
    """逐字节比较水位（避免近似断言放过微小改动）。"""
    import struct
    return struct.pack(">d", float(value))


class _Row:
    def __init__(self, key, level, settled_at=None):
        self.drive_key = key
        self.level = level
        self.last_settled_at = settled_at or _CURSOR


class _FakeSession:
    """只够跑完 settle() 的最小假 session（不落任何真实库）。"""

    def __init__(self, rows):
        self.rows = rows
        self.flushes = 0

    async def execute(self, *_a, **_k):  # pragma: no cover - 走 _fetch_rows 打桩
        raise AssertionError("本测试把 _fetch_rows 打桩了，不该走到真 execute")

    async def flush(self):
        self.flushes += 1

    def add(self, *_a, **_k):
        return None


@pytest.fixture(autouse=True)
def _never_write_real_trace(monkeypatch):
    """真 ``obs_event`` 一律先换成空操作（更不碰任何库）。"""
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


@pytest.fixture
def rows_by_char():
    """按角色路由的合成行集（不同角色同时在场，才能验「不串味」）。"""
    return {
        _GRAY_CHAR: [_Row(drives.DRIVE_LONGING, 10.0), _Row(drives.DRIVE_SHARING, 4.0)],
        _OTHER_CHAR: [_Row(drives.DRIVE_LONGING, 20.0)],
    }


def _wire(monkeypatch, rows_by_char, mode, gray_hit=True, bias=None):
    """把 settle() 的外部依赖接成可控档位（**settle_level / 留痕出口都走真实现**）。"""
    async def _fetch(_db, character_id, _user_id):
        return list(rows_by_char.get(int(character_id), []))

    monkeypatch.setattr(rds, "shadow_enabled", lambda: True)
    monkeypatch.setattr(rds, "_fetch_rows", _fetch)
    monkeypatch.setattr(rds, "modulation_mode", lambda: mode)
    monkeypatch.setattr(rds, "_gray_hit", lambda *_a, **_k: gray_hit)

    async def _snap(*_a, **_k):
        return _SNAPSHOT

    async def _vec(*_a, **_k):
        return bias

    monkeypatch.setattr(rds, "_fetch_emotion_snapshot", _snap)
    monkeypatch.setattr(rds, "_fetch_bias_vector", _vec)


def _settle(rows_by_char, character_id):
    return asyncio.run(rds.settle(_FakeSession(rows_by_char.get(character_id, [])),
                                  character_id, _USER, _NOW))


# ───────────── 1. on 档：生效留痕归属到真实角色 ─────────────

def test_on_trace_carries_character_id_through_real_chain(obs_spy, rows_by_char, monkeypatch):
    """on＋白名单：真实 service → 真实 settle_level → 真实留痕出口 ⇒ 角色非 None 且正确。"""
    _wire(monkeypatch, rows_by_char, rds.MODULATION_ON)
    result = _settle(rows_by_char, _GRAY_CHAR)

    rows = rows_by_char[_GRAY_CHAR]
    assert len(obs_spy) == len(rows), "生效留痕每行一条，不得翻倍"
    assert all(call[0] == _GRAY_CHAR for call in obs_spy), [c[0] for c in obs_spy]
    assert all(call[1] == emod.SHADOW_METRIC and call[3] == "shadow" for call in obs_spy)
    # 明细里 drive_key/new_level 与真实落库值一致（归属修复没把明细写歪）
    by_drive = {call[2]["drive_key"]: call for call in obs_spy}
    assert set(by_drive) == {row.drive_key for row in rows}
    for row in rows:
        assert by_drive[row.drive_key][2]["new_level"] == round(float(row.level), 4)
        assert result[row.drive_key] == pytest.approx(row.level)


def test_on_water_level_identical_to_domain_function(obs_spy, rows_by_char, monkeypatch):
    """归属参数绝不改数学：落库水位 == 直接调真实 settle_level（同快照）的结果，逐字节。"""
    _wire(monkeypatch, rows_by_char, rds.MODULATION_ON)
    rows = rows_by_char[_GRAY_CHAR]
    before = [(row.drive_key, float(row.level)) for row in rows]
    _settle(rows_by_char, _GRAY_CHAR)
    for (key, level), row in zip(before, rows):
        want, want_cursor = drives.settle_level(level, key, _CURSOR, _NOW, _SNAPSHOT)
        assert _bits(row.level) == _bits(want)
        assert row.last_settled_at == want_cursor


def test_different_characters_do_not_mix(obs_spy, rows_by_char, monkeypatch):
    """两个角色各自结算 ⇒ 留痕只带自己那个 character_id，不共用、不串味。"""
    _wire(monkeypatch, rows_by_char, rds.MODULATION_ON)
    _settle(rows_by_char, _GRAY_CHAR)
    _settle(rows_by_char, _OTHER_CHAR)

    ids = [call[0] for call in obs_spy]
    assert ids[:2] == [_GRAY_CHAR, _GRAY_CHAR]
    assert ids[2:] == [_OTHER_CHAR]
    assert len(obs_spy) == 3
    assert {call[2]["drive_key"] for call in obs_spy if call[0] == _OTHER_CHAR} == {
        drives.DRIVE_LONGING}


def test_on_outside_gray_still_no_trace(obs_spy, rows_by_char, monkeypatch):
    """on 但桶外 ⇒ 与 off 同：一条留痕都不发（灰度白名单口径不动）。"""
    _wire(monkeypatch, rows_by_char, rds.MODULATION_ON, gray_hit=False)
    _settle(rows_by_char, _GRAY_CHAR)
    assert obs_spy == []


# ───────────── 2. off / shadow：原行为，不多留痕 ─────────────

def test_off_emits_no_trace_and_passes_no_keyword(obs_spy, rows_by_char, monkeypatch):
    _wire(monkeypatch, rows_by_char, rds.MODULATION_OFF)
    seen: list[dict] = []
    real = drives.settle_level

    def _spy(*args, **kwargs):
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(drives, "settle_level", _spy)
    _settle(rows_by_char, _GRAY_CHAR)
    assert obs_spy == [] and seen and all(not kw for kw in seen)


def test_shadow_trace_count_and_owner_unchanged(obs_spy, rows_by_char, monkeypatch):
    """shadow：留痕条数＝行数（每行 1 条，无重复），且归属/水位与改动前同形。"""
    _wire(monkeypatch, rows_by_char, rds.MODULATION_SHADOW)
    seen: list[dict] = []
    real = drives.settle_level

    def _spy(*args, **kwargs):
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(drives, "settle_level", _spy)
    rows = rows_by_char[_GRAY_CHAR]
    before = [float(row.level) for row in rows]
    _settle(rows_by_char, _GRAY_CHAR)

    assert len(obs_spy) == len(rows)
    assert all(call[0] == _GRAY_CHAR for call in obs_spy)
    assert all(not kw for kw in seen), "shadow 档 settle_level 仍是无快照旧调用"
    plain = [drives.settle_level(level, row.drive_key, _CURSOR, _NOW)[0]
             for level, row in zip(before, rows)]
    assert [_bits(row.level) for row in rows] == [_bits(p) for p in plain], "shadow 落库须等于不调制"


# ───────────── 3. 留痕失败不改变水位 ─────────────

async def _collect(rows_by_char, character_id):
    result = await rds.settle(_FakeSession(rows_by_char[character_id]), character_id, _USER, _NOW)
    return {k: _bits(v) for k, v in result.items()}


def _fresh_gray_rows():
    return [_Row(drives.DRIVE_LONGING, 10.0), _Row(drives.DRIVE_SHARING, 4.0)]


def test_trace_failure_never_changes_level(rows_by_char, monkeypatch):
    """留痕出口抛异常 ⇒ 结算照常：返回值/落库水位/游标与留痕成功时逐字节相同，且不上抛。"""
    import app.memory.observability as obs

    _wire(monkeypatch, rows_by_char, rds.MODULATION_ON)
    rows_by_char[_GRAY_CHAR] = _fresh_gray_rows()
    ok = asyncio.run(_collect(rows_by_char, _GRAY_CHAR))
    ok_rows = [(row.drive_key, _bits(row.level), row.last_settled_at)
               for row in rows_by_char[_GRAY_CHAR]]

    def _boom(*_a, **_k):
        raise RuntimeError("trace queue down")

    monkeypatch.setattr(obs, "obs_event", _boom)
    rows_by_char[_GRAY_CHAR] = _fresh_gray_rows()
    broken = asyncio.run(_collect(rows_by_char, _GRAY_CHAR))

    assert broken == ok, "留痕失败不得改变水位"
    assert [(row.drive_key, _bits(row.level), row.last_settled_at)
            for row in rows_by_char[_GRAY_CHAR]] == ok_rows, "留痕失败不得改变落库值与游标"



# ───────────── 4. 同游标幂等 ─────────────

def test_same_cursor_is_idempotent(obs_spy, rows_by_char, monkeypatch):
    """同 now 重复结算：第二次起增量为 0，且**不再多留痕**（零增量路径提前返回）。"""
    _wire(monkeypatch, rows_by_char, rds.MODULATION_ON)
    rows = rows_by_char[_GRAY_CHAR]
    first = asyncio.run(_collect(rows_by_char, _GRAY_CHAR))
    traces_first = len(obs_spy)
    assert traces_first == len(rows)
    obs_spy.clear()
    second = asyncio.run(_collect(rows_by_char, _GRAY_CHAR))

    assert second == first, "同 now 二次结算增量必须为 0"
    assert all(row.last_settled_at == _NOW for row in rows)
    assert obs_spy == [], "幂等：留痕条数不随重复调用累积"


# ───────────── 5. 默认旧调用逐字节等价 ─────────────

def test_character_id_is_keyword_only_with_none_default():
    sig = inspect.signature(drives.settle_level)
    assert sig.parameters["character_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["character_id"].default is None
    assert sig.parameters["emotion_snapshot"].default is None


def test_default_call_unchanged_owner_stays_none(obs_spy):
    """不传 character_id ⇒ 留痕角色仍是 None（M0「没有调用方」口径不被改写）。"""
    traced = drives.settle_level(10.0, drives.DRIVE_LONGING, _CURSOR, _NOW, (0.0, 0.5))
    assert len(obs_spy) == 1
    assert obs_spy[0][0] is None
    legacy = drives.settle_level(10.0, drives.DRIVE_LONGING, _CURSOR, _NOW)
    assert _bits(traced[0]) != _bits(legacy[0])  # 乘子确实生效（(0.0,.5)⇒×1.25）
    assert _bits(legacy[0]) == _bits(drives.settle_level(
        10.0, drives.DRIVE_LONGING, _CURSOR, _NOW, None)[0])


def test_owner_argument_never_moves_water_level(obs_spy):
    """同一入参，带/不带 character_id 的返回值逐字节相同（归属只进 trace）。"""
    no_owner = drives.settle_level(7.5, drives.DRIVE_CONCERN, _CURSOR, _NOW, _SNAPSHOT)
    obs_spy.clear()
    with_owner = drives.settle_level(
        7.5, drives.DRIVE_CONCERN, _CURSOR, _NOW, _SNAPSHOT, character_id=_GRAY_CHAR)
    assert _bits(no_owner[0]) == _bits(with_owner[0]) and no_owner[1] == with_owner[1]
    assert obs_spy[-1][0] == _GRAY_CHAR
