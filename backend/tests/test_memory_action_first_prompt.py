# -*- coding: utf-8 -*-
"""A 方案合同：动作优先提示（flag memory_action_first_v1 默认关）。"""
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def test_flag_registered_default_off():
    from app.flags.agent_flags import AGENT_FLAGS

    assert AGENT_FLAGS.get("memory_action_first_v1") is False, "默认关＝现网零行为"


def test_hint_empty_when_flag_off():
    from app.agent.context.assembly import _action_first_hint

    assert _action_first_hint() == "", "关档必须返回空串（不插入任何文字）"


def test_hint_contains_action_rules_when_on(monkeypatch):
    from app.agent.context.assembly import _action_first_hint
    from app.flags.agent_flags import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, "memory_action_first_v1", True)
    hint = _action_first_hint()
    assert hint.startswith("## 行动优先级")
    for token in ("[MEMO]", "[CAL_NOTE]", "【记忆：", "不要用反问代替记录"):
        assert token in hint, token


def test_assembly_wiring_ratchet():
    src = (APP / "agent/context/assembly.py").read_text(encoding="utf-8")
    assert "def _action_first_hint(" in src
    assert 'memory_action_first_v1' in src
    assert '"\\n## 当前世界状态"' in src, "提示必须插在「当前世界状态」段之前"
    assert "_inject_action_first_hint(" in src, "关档不得插入（helper 内判空）"


def test_catalog_entry_exists():
    from app.application.flag_catalog import FLAG_CATALOG

    row = FLAG_CATALOG["memory_action_first_v1"]
    assert row["group"] == "memory" and row["visible"] is False
    assert "先" in row["desc_zh"]
