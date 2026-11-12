# -*- coding: utf-8 -*-
"""M2 合同：状态口径分工（energy=短期体力／fatigue=长期疲劳），flag `life_state_semantics_v1` 默认关。

口径（2026-11-05 用户拍板 C）：
- 体力见底 energy<20 ⇒ 睡觉（原有分支，优先级最高）；
- 长期疲劳 fatigue>65 ⇒ 休息；flag 开时不再叠加 energy<50 条件。
"""
from app.life.decision import decide, StateSnapshot

_NEEDS = {k: 50 for k in (
    "curiosity", "productivity", "relaxation", "social",
    "creativity", "learning", "reflection", "entertainment",
)}


def _snap(**over) -> StateSnapshot:
    base = dict(
        character_id=1, user_id=1, energy=70, focus=50,
        needs=dict(_NEEDS), phase="afternoon", mood=50,
        fatigue=30, anger=10, location="home", current_room="living",
    )
    base.update(over)
    return StateSnapshot(**base)


def _set_flag(monkeypatch, value: bool) -> None:
    from app.flags.agent_flags import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "life_state_semantics_v1", value)


def test_flag_off_keeps_legacy_predicate(monkeypatch):
    _set_flag(monkeypatch, False)
    # 疲劳高但体力不算低（>50）⇒ 旧判据不休息
    assert decide(_snap(energy=60, fatigue=70)).reason != "fatigue_rest"
    # 疲劳高且体力低 ⇒ 旧判据休息
    assert decide(_snap(energy=40, fatigue=70)).reason == "fatigue_rest"


def test_flag_on_two_level_predicate(monkeypatch):
    _set_flag(monkeypatch, True)
    # 长期疲劳单列：体力 60 也休息（旧口径不发）
    assert decide(_snap(energy=60, fatigue=70)).reason == "fatigue_rest"
    # 疲劳未过线：不因 fatigue 休息
    assert decide(_snap(energy=60, fatigue=60)).reason != "fatigue_rest"


def test_energy_critical_takes_precedence(monkeypatch):
    _set_flag(monkeypatch, True)
    # 体力见底 ⇒ 睡觉优先于「疲劳休息」
    d = decide(_snap(energy=10, fatigue=80, phase="morning"))
    assert d.action == "sleep" and d.reason.startswith("energy_critical")


def test_flag_registered_and_documented():
    from app.flags.agent_flags import AGENT_FLAGS

    assert AGENT_FLAGS.get("life_state_semantics_v1") is False, "默认必须关，现网零变化"
    from app.application import flag_catalog

    keys = set(flag_catalog.FLAG_CATALOG)
    if keys:
        assert "life_state_semantics_v1" in keys


def test_decision_source_has_two_branch_ratchet():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "life" / "decision.py").read_text(encoding="utf-8")
    assert "_state_semantics_v1" in src
    assert "def _fatigue_decision(" in src, "两级判据必须抽成独立函数（热点棘轮：decide 不许变长）"
    assert "if snap.fatigue > 65:" in src, "flag 开分支要单列长期疲劳"
    assert "if snap.fatigue > 65 and snap.energy < 50:" in src, "flag 关分支要逐字保留旧判据"
    assert "_fatigue_decision(snap)" in src
