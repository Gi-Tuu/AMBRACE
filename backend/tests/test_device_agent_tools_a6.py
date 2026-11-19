import pytest

from app.agent import actions as act_mod
from app.device import agent_tools


def test_parse_device_observe_marker():
    acts = act_mod.parse_actions("[DEVICE_OBSERVE]com.android.settings[/DEVICE_OBSERVE]")
    assert len(acts) == 1
    assert acts[0].action_type == act_mod.DEVICE_OBSERVE
    assert acts[0].payload["target_app"] == "com.android.settings"


def test_register_device_tools_observe_enabled_act_disabled():
    specs = {s.name: s for s in agent_tools.register_device_tools(enabled=True)}
    assert specs[agent_tools.OBSERVE_TOOL].enabled is True
    assert specs[agent_tools.OBSERVE_TOOL].execute is not None
    assert specs[agent_tools.ACT_TOOL].enabled is False


@pytest.mark.asyncio
async def test_observe_handler_returns_refs(monkeypatch):
    from app.agent import loop as loop_mod

    monkeypatch.setattr(loop_mod, "AGENT_FLAGS", {"device_actions_enabled": True})

    async def _enabled(_uid):
        return True

    monkeypatch.setattr("app.device.actions._account_actions_enabled", _enabled)

    async def _tree(**_kw):
        return {"role": "window", "name": "设置", "children": [
            {"role": "button", "name": "确定", "clickable": True, "path": "0/0/1"}]}

    monkeypatch.setattr("app.device.agent_tools.device_adapter.observe_tree", _tree)
    out = await agent_tools._execute_observe({"user_id": 3, "target_app": "com.android.settings"})
    assert out["ok"] is True
    assert out["refs"]
    assert "snapshot_id" in out


@pytest.mark.asyncio
async def test_observe_handler_respects_kill_switch(monkeypatch):
    from app.agent import loop as loop_mod

    monkeypatch.setattr(loop_mod, "AGENT_FLAGS", {"device_actions_enabled": False})
    out = await agent_tools._execute_observe({"user_id": 3, "target_app": "com.android.settings"})
    assert out["ok"] is False
    assert "总开关" in out["text"]
