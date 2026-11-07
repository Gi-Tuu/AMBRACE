# -*- coding: utf-8 -*-
"""M8b 合同：L4「UNKNOWN」生产者——工具失败/超时不再冒充事实，且注入侧强制可见。"""
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


class _Spec:
    epistemic_status = "FACT"
    max_observation_chars = 120
    provenance = "tool"
    name = "demo_tool"


def test_tool_error_maps_to_unknown_when_flag_on(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.agent import observation as obs_mod
    from app.memory import grading

    monkeypatch.setitem(AGENT_FLAGS, grading.FLAG, False)
    o = obs_mod.from_spec(_Spec(), {"summary": "boom"}, "error")
    assert o.epistemic_status == "FACT", "flag 关必须逐字保持历史口径"

    monkeypatch.setitem(AGENT_FLAGS, grading.FLAG, True)
    o2 = obs_mod.from_spec(_Spec(), {"summary": "boom"}, "error")
    assert o2.epistemic_status == "UNKNOWN", "失败/超时必须落 UNKNOWN"

    o3 = obs_mod.from_spec(_Spec(), {"summary": "fine"}, "ok")
    assert o3.epistemic_status == "FACT", "成功路径不受影响"

    o4 = obs_mod.from_spec(_Spec(), None, "blocked")
    assert o4.epistemic_status == "FACT", "被拒绝是已知结果，不得改成 UNKNOWN"


def test_unknown_label_forced_in_injection(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.agent import tools
    from app.memory import grading

    obs = {"epistemic_status": "UNKNOWN", "provenance": "tool", "summary": "x"}

    # 两个 flag 都关：旧行为＝不标注
    monkeypatch.setitem(AGENT_FLAGS, grading.FLAG, False)
    monkeypatch.setitem(AGENT_FLAGS, "observation_label_v1", False)
    assert tools.observation_tag(obs) == ""

    # 只开分级 flag：UNKNOWN 必须强制可见（不能被静默当成功）
    monkeypatch.setitem(AGENT_FLAGS, grading.FLAG, True)
    assert tools.observation_tag(obs) == "·UNKNOWN·tool"

    # 普通认知态不受强制影响
    normal = {"epistemic_status": "FACT", "provenance": "tool"}
    assert tools.observation_tag(normal) == ""
    monkeypatch.setitem(AGENT_FLAGS, "observation_label_v1", True)
    assert tools.observation_tag(normal) == "·FACT·tool"


def test_wiring_ratchets():
    obs_src = (APP / "agent" / "observation.py").read_text(encoding="utf-8")
    tools_src = (APP / "agent" / "tools.py").read_text(encoding="utf-8")
    assert "UNKNOWN" in obs_src and "_epistemic_for_status" in obs_src
    assert "_unknown_label_forced" in tools_src
    assert 'epi == "UNKNOWN"' in tools_src
