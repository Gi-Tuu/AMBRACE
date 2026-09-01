# -*- coding: utf-8 -*-
"""M2-S2 分层衰减：effective_strength_days / 冷归档判定（纯函数）+ decay 集成（flag 门控）。"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.memory import tiering


def _mem(**kw):
    base = dict(strength_days=7.0, epistemic_status=None, reliability_score=None,
                is_core=False, confirmation_count=0, why_it_matters=None,
                delete_at=None, memory_type="event", character_id=1)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture()
def tier_on(monkeypatch):
    monkeypatch.setattr(tiering, "tiered_decay_on", lambda: True)


def test_flag_default_off():
    assert tiering.tiered_decay_on() is False  # 灰度开关默认关：关=逐字节现状


def test_low_confidence_accelerates(tier_on):
    m = _mem(epistemic_status="INFERRED", reliability_score=0.3)
    assert tiering.effective_strength_days(m) == pytest.approx(7.0 * 0.8)
    m2 = _mem(epistemic_status="UNVERIFIED", reliability_score=0.2, strength_days=2.0)
    assert tiering.effective_strength_days(m2) == pytest.approx(3.0)  # max(S_MIN, 1.6)


def test_high_value_raises_floor(tier_on):
    assert tiering.effective_strength_days(_mem(is_core=True)) == pytest.approx(14.0)
    assert tiering.effective_strength_days(_mem(confirmation_count=2)) == pytest.approx(14.0)
    assert tiering.effective_strength_days(_mem(why_it_matters="很重要")) == pytest.approx(14.0)
    # 已有 S 更高则不降
    assert tiering.effective_strength_days(_mem(is_core=True, strength_days=30.0)) == pytest.approx(30.0)


def test_fact_high_reliability_raises_cap(tier_on):
    out = tiering.effective_strength_days(_mem(epistemic_status="FACT", reliability_score=0.9))
    assert out == pytest.approx(60.0)  # max(7, S_MAX=60) → 60（min 180 不触顶）
    out2 = tiering.effective_strength_days(_mem(epistemic_status="FACT", reliability_score=0.9, strength_days=120.0))
    assert out2 == pytest.approx(120.0)  # 已有 120 ≤ 180 保持


def test_default_passthrough(tier_on):
    """普通记忆（epistemic=NULL→FACT、reliability=NULL→1.0，与原行为一致）→ FACT+高可靠分支抬至 60"""
    out = tiering.effective_strength_days(_mem())
    assert out == pytest.approx(60.0)


def test_cold_archive_gate(tier_on):
    """跌破阈值时：高置信 → 冷归档；低置信 → 走删除倒计时；已在倒计时维持现路径"""
    assert tiering.should_cold_archive(_mem(is_core=True)) is True
    assert tiering.should_cold_archive(_mem(epistemic_status="INFERRED", reliability_score=0.2)) is False
    assert tiering.should_cold_archive(_mem(is_core=True, delete_at="already")) is False


def test_cold_archive_gate_flag_off(monkeypatch):
    monkeypatch.setattr(tiering, "tiered_decay_on", lambda: False)
    assert tiering.should_cold_archive(_mem(is_core=True)) is False


def test_decay_integration_cold_archives_high_confidence(tier_on, monkeypatch):
    """decay 集成：flag 开 + 高置信 + 保留率跌破阈值 → is_archived=True 且不设 delete_at"""
    from app.memory import decay as d

    monkeypatch.setattr("app.memory.observability.obs_event", lambda *a, **k: None)  # 隔离真实 trace 写库

    mem = SimpleNamespace(
        id=1, character_id=1, memory_type="event", strength_days=14.0, importance=80.0,
        is_core=True, confirmation_count=0, why_it_matters=None,
        epistemic_status="FACT", reliability_score=0.95,
        is_pinned=False, is_locked=False, delete_at=None,
        last_reinforce_at=datetime.now(timezone.utc) - timedelta(days=60),
        created_at=None, user_id=1,
    )

    commits = []

    class _FakeDB:
        async def commit(self):
            commits.append(1)

    now = datetime.now(timezone.utc).replace(tzinfo=None)  # 60 天未强化，S_eff=14 → pct≈极低 <20%
    removed = asyncio.run(d._apply_decay(_FakeDB(), mem, now=now))
    assert mem.is_archived is True           # 冷归档（可逆）
    assert mem.delete_at is None             # 未进删除倒计时
    assert removed is True
    assert len(commits) >= 1


def test_decay_integration_flag_off_keeps_countdown(monkeypatch):
    """flag 关（现状）：同样条件走 3 天删除倒计时而非冷归档"""
    from app.memory import decay as d

    monkeypatch.setattr("app.memory.observability.obs_event", lambda *a, **k: None)

    mem = SimpleNamespace(
        id=2, character_id=1, memory_type="event", strength_days=14.0, importance=80.0,
        is_core=True, confirmation_count=0, why_it_matters=None,
        epistemic_status="FACT", reliability_score=0.95,
        is_pinned=False, is_locked=False, delete_at=None, is_archived=False,
        last_reinforce_at=datetime.now(timezone.utc) - timedelta(days=60),
        created_at=None, user_id=1,
    )

    class _FakeDB:
        async def commit(self):
            pass

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    removed = asyncio.run(d._apply_decay(_FakeDB(), mem, now=now))
    assert removed is False
    assert mem.is_archived is False
    assert mem.delete_at is not None         # 现状：3 天删除倒计时
