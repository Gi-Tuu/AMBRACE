"""Event Schema 扩展测试（World & Cognition P0，2026-08-15）"""
import pytest

from app.events.schema import (
    make_event, require_speaker, speaker_of,
    EPISTEMIC_FACT, EPISTEMIC_INFERRED, EPISTEMIC_FICTIONAL,
)


def test_make_event_defaults():
    e = make_event("life.activity_completed", speaker={"type": "character", "id": 1},
                   provenance={"origin": "life_event"}, data={"a": 1})
    assert e["type"] == "life.activity_completed"
    assert e["speaker"]["id"] == 1
    assert e["epistemic_status"] == EPISTEMIC_FACT  # life_event → FACT
    assert e["provenance"]["confidence"] == 0.9
    assert e["event_id"].startswith("evt_")
    assert "timestamp" in e


def test_origin_maps_status():
    assert make_event("x", provenance={"origin": "user_message"})["epistemic_status"] == EPISTEMIC_FACT
    assert make_event("x", provenance={"origin": "inference"})["epistemic_status"] == EPISTEMIC_INFERRED
    assert make_event("x", provenance={"origin": "story_event"})["epistemic_status"] == EPISTEMIC_FICTIONAL


def test_unknown_origin_rejected():
    with pytest.raises(ValueError):
        make_event("x", provenance={"origin": "nope"})


def test_require_speaker():
    with pytest.raises(ValueError):
        require_speaker(make_event("x", provenance={"origin": "life_event"}))
    ok = make_event("x", speaker={"type": "user", "id": 3}, provenance={"origin": "life_event"})
    require_speaker(ok)  # 不抛


def test_speaker_of():
    assert speaker_of("user", 5) == {"type": "user", "id": 5}
    assert speaker_of("ai", 7) == {"type": "character", "id": 7}
    assert speaker_of("user", None) is None
