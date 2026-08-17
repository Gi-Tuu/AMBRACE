"""记忆可靠度评分 + 信号采集测试（World & Cognition P5，2026-08-15）"""
from datetime import datetime, timedelta, timezone

from app.memory.reliability import (
    source_weight, reliability_score, detect_confirmation, detect_correction,
)
from app.models.memory import Memory
from app.events.schema import EPISTEMIC_FACT, EPISTEMIC_INFERRED


def _mem(**kw):
    base = dict(
        user_id=4, character_id=11, memory_type="event", content="测试记忆",
        speaker_type="user", speaker_id=4, source="chat",
        epistemic_status=EPISTEMIC_FACT,
        importance=60.0,
    )
    base.update(kw)
    return Memory(**base)


def test_source_weight():
    assert source_weight(_mem(speaker_type="user", source="chat")) == 1.0
    assert source_weight(_mem(speaker_type="character", source="chat")) == 0.6
    assert source_weight(_mem(speaker_type="character", source="chat", epistemic_status=EPISTEMIC_INFERRED)) == 0.4
    assert source_weight(_mem(speaker_type="system", source="life")) == 0.9
    assert source_weight(_mem(speaker_type="system", source="status")) == 1.0
    assert source_weight(_mem(speaker_type="character", source="moment")) == 0.85


def test_reliability_no_correction_no_decay():
    m = _mem()
    m.contradiction_count = 0
    m.confirmation_count = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    m.updated_at = now
    # 用户说的：1.0 * 1.0 * 0.7 * 1.0
    assert reliability_score(m, now) == 0.7


def test_reliability_contradiction_penalty():
    m = _mem()
    m.contradiction_count = 1
    m.confirmation_count = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    m.updated_at = now
    # 1.0 * (1/2) * 0.7 = 0.35
    assert reliability_score(m, now) == 0.35


def test_reliability_confirmation_boost():
    m = _mem()
    m.confirmation_count = 2
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    m.updated_at = now
    # 1.0 * 1.0 * 0.9 = 0.9
    assert reliability_score(m, now) == 0.9


def test_reliability_recency_decay():
    m = _mem()
    m.contradiction_count = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    m.updated_at = now - timedelta(days=30)
    # 1.0 * 1.0 * 0.7 * (1.0 - 0.3) = 0.49
    assert reliability_score(m, now) == 0.49
    m.updated_at = now - timedelta(days=200)
    assert reliability_score(m, now) == 0.35  # 衰减下限 0.5：1.0*0.7*0.5


def test_signals():
    assert detect_confirmation("没错，就是这样")
    assert detect_confirmation("对对对，你记得真准")
    assert not detect_confirmation("对，我今天去了公司")
    assert detect_correction("你记错了，不是那样的")
    assert detect_correction("我没说过这件事")
    assert not detect_correction("不是今天，是明天")  # 弱信号不误伤
