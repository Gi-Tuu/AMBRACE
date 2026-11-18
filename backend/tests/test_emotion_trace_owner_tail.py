# -*- coding: utf-8 -*-
"""A4 批 7 尾追（2026-10-14）改写（2026-11-14）：**on/off/shadow 三档的 service 结算语义**。

原版钉的是「生效留痕（obs_event）的角色归属」六条硬口径；2026-11-14 B13 判效完成后
``obs_event`` DB 留痕退役（≈1.3 万行/天停止写库，读数已逐字入档）⇒ 归属类合同随之退役，
本文件保留仍然成立的 **service 结算语义** 合同：

1. ``on`` ＝ 真实 service → 真实 ``drives.settle_level``：落库水位与直接调域函数逐字节一致；
2. ``off`` 档 ``settle_level`` 收到的调用**不带任何 keyword**（原样旧调用）；
3. ``shadow`` 档同样无快照旧调用、落库等于不调制；
4. 同一游标重复结算**幂等**：第二起增量为 0；
5. ``character_id`` 是 keyword-only 且缺省 None；带/不带归属的返回值逐字节相同（参数不参与数学）。

纪律：纯合成夹具，零业务库、零迁移、零网络。
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta

import pytest

from app.application import relational_drive_service as rds
from app.domain.relational import drives

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
    """真 ``obs_event`` 一律空操作（留痕写库已退役；即便重接也不许在测试里碰任何库）。"""
    import app.memory.observability as obs
    monkeypatch.setattr(obs, "obs_event", lambda *a, **k: None)


@pytest.fixture
def rows_by_char():
    """按角色路由的合成行集（不同角色同时在场，才能验「不串味」）。"""
    return {
        _GRAY_CHAR: [_Row(drives.DRIVE_LONGING, 10.0), _Row(drives.DRIVE_SHARING, 4.0)],
        _OTHER_CHAR: [_Row(drives.DRIVE_LONGING, 20.0)],
    }


def _wire(monkeypatch, rows_by_char, mode, gray_hit=True, bias=None):
    """把 settle() 的外部依赖接成可控档位（settle_level 走真实现）。"""
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


# ───────────── 1. on 档：落库水位与域函数逐字节一致 ─────────────

def test_on_water_level_identical_to_domain_function(rows_by_char, monkeypatch):
    """归属参数绝不改数学：落库水位 == 直接调真实 settle_level（同快照）的结果，逐字节。"""
    _wire(monkeypatch, rows_by_char, rds.MODULATION_ON)
    rows = rows_by_char[_GRAY_CHAR]
    before = [(row.drive_key, float(row.level)) for row in rows]
    _settle(rows_by_char, _GRAY_CHAR)
    for (key, level), row in zip(before, rows):
        want, want_cursor = drives.settle_level(level, key, _CURSOR, _NOW, _SNAPSHOT)
        assert _bits(row.level) == _bits(want)
        assert row.last_settled_at == want_cursor


def test_on_outside_gray_still_no_modulation(rows_by_char, monkeypatch):
    """on 但桶外 ⇒ 与 off 同：不走调制（灰度白名单口径不动）。"""
    _wire(monkeypatch, rows_by_char, rds.MODULATION_ON, gray_hit=False)
    rows = rows_by_char[_GRAY_CHAR]
    before = [float(row.level) for row in rows]
    _settle(rows_by_char, _GRAY_CHAR)
    plain = [drives.settle_level(level, row.drive_key, _CURSOR, _NOW)[0]
             for level, row in zip(before, rows)]
    assert [_bits(row.level) for row in rows] == [_bits(p) for p in plain], "桶外不得调制"


# ───────────── 2. off / shadow：原行为 ─────────────

def test_off_emits_no_trace_and_passes_no_keyword(rows_by_char, monkeypatch):
    _wire(monkeypatch, rows_by_char, rds.MODULATION_OFF)
    seen: list[dict] = []
    real = drives.settle_level

    def _spy(*args, **kwargs):
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(drives, "settle_level", _spy)
    _settle(rows_by_char, _GRAY_CHAR)
    assert seen and all(not kw for kw in seen)


def test_shadow_trace_count_and_owner_unchanged(rows_by_char, monkeypatch):
    """shadow 档：settle_level 仍是无快照旧调用、落库等于不调制（每行同形）。"""
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

    assert all(not kw for kw in seen), "shadow 档 settle_level 仍是无快照旧调用"
    plain = [drives.settle_level(level, row.drive_key, _CURSOR, _NOW)[0]
             for level, row in zip(before, rows)]
    assert [_bits(row.level) for row in rows] == [_bits(p) for p in plain], "shadow 落库须等于不调制"


# ───────────── 3. 同游标幂等 ─────────────

async def _collect(rows_by_char, character_id):
    result = await rds.settle(_FakeSession(rows_by_char[character_id]), character_id, _USER, _NOW)
    return {k: _bits(v) for k, v in result.items()}


def _fresh_gray_rows():
    return [_Row(drives.DRIVE_LONGING, 10.0), _Row(drives.DRIVE_SHARING, 4.0)]


def test_same_cursor_is_idempotent(rows_by_char, monkeypatch):
    """同 now 重复结算：第二次起增量为 0。"""
    _wire(monkeypatch, rows_by_char, rds.MODULATION_ON)
    rows = rows_by_char[_GRAY_CHAR]
    first = asyncio.run(_collect(rows_by_char, _GRAY_CHAR))
    second = asyncio.run(_collect(rows_by_char, _GRAY_CHAR))

    assert second == first, "同 now 二次结算增量必须为 0"
    assert all(row.last_settled_at == _NOW for row in rows)


# ───────────── 4. 默认旧调用逐字节等价 ─────────────

def test_character_id_is_keyword_only_with_none_default():
    sig = inspect.signature(drives.settle_level)
    assert sig.parameters["character_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["character_id"].default is None
    assert sig.parameters["emotion_snapshot"].default is None


def test_owner_argument_never_moves_water_level():
    """同一入参，带/不带 character_id 的返回值逐字节相同（归属参数不参与数学）。"""
    no_owner = drives.settle_level(7.5, drives.DRIVE_CONCERN, _CURSOR, _NOW, _SNAPSHOT)
    with_owner = drives.settle_level(
        7.5, drives.DRIVE_CONCERN, _CURSOR, _NOW, _SNAPSHOT, character_id=_GRAY_CHAR)
    assert _bits(no_owner[0]) == _bits(with_owner[0]) and no_owner[1] == with_owner[1]
