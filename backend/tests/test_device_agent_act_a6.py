import asyncio
from types import SimpleNamespace

from app.agent import actions as act_mod
from app.device import agent_tools


def test_parse_device_act_marker():
    acts = act_mod.parse_actions(
        '[DEVICE_ACT]{"snapshot_id":"s1","ref":"@s1:e1","verb":"click"}[/DEVICE_ACT]'
    )
    assert len(acts) == 1
    assert acts[0].action_type == act_mod.DEVICE_ACT
    assert acts[0].payload["ref"] == "@s1:e1"


def test_register_device_tools_can_enable_act():
    specs = {s.name: s for s in agent_tools.register_device_tools(enabled=True, act_enabled=True)}
    assert specs[agent_tools.OBSERVE_TOOL].enabled is True
    assert specs[agent_tools.ACT_TOOL].enabled is True
    assert specs[agent_tools.ACT_TOOL].execute is not None


def test_act_handler_click(monkeypatch):
    from app.agent import loop as loop_mod

    monkeypatch.setattr(loop_mod, "AGENT_FLAGS", {"device_actions_enabled": True})

    async def _enabled(_uid):
        return True

    async def _policies(_uid):
        return {"action_tap": "once_ever"}

    async def _exec(**_kw):
        return {"ok": True, "detail": "ok"}

    monkeypatch.setattr("app.device.actions._account_actions_enabled", _enabled)
    monkeypatch.setattr("app.device.actions.configured_policies", _policies)
    monkeypatch.setattr("app.device.agent_tools.snap.plan_action",
                        lambda sid, ref, verb, **kw: {"path": "0/0/1", "expected_identity": {"name": "确定"}})
    monkeypatch.setattr("app.device.agent_tools.snap.get_snapshot",
                        lambda sid: SimpleNamespace(target_app="com.test.app", paths={}))
    monkeypatch.setattr("app.device.agent_tools.device_adapter.execute_action", _exec)
    out = asyncio.run(agent_tools._execute_act(
        {"user_id": 3, "snapshot_id": "s1", "ref": "@s1:e1", "verb": "click", "target_app": "com.test.app"}
    ))
    assert out["ok"] is True
    assert "确定" in out["text"]


def test_act_handler_requires_approval(monkeypatch):
    from app.agent import loop as loop_mod

    monkeypatch.setattr(loop_mod, "AGENT_FLAGS", {"device_actions_enabled": True})

    async def _enabled(_uid):
        return True

    async def _policies(_uid):
        return {}

    monkeypatch.setattr("app.device.actions._account_actions_enabled", _enabled)
    monkeypatch.setattr("app.device.actions.configured_policies", _policies)
    monkeypatch.setattr("app.device.agent_tools.snap.plan_action",
                        lambda sid, ref, verb, **kw: {"path": "0/0/1", "expected_identity": {"name": "确定"}})
    monkeypatch.setattr("app.device.agent_tools.snap.get_snapshot",
                        lambda sid: SimpleNamespace(target_app="com.test.app", paths={}))
    out = asyncio.run(agent_tools._execute_act(
        {"user_id": 3, "snapshot_id": "s1", "ref": "@s1:e1", "verb": "click", "target_app": "com.test.app"}
    ))
    assert out["ok"] is False
    assert "确认" in out["text"]


def test_parse_device_swipe_marker():
    acts = act_mod.parse_actions('[DEVICE_ACT]{"verb":"swipe","direction":"up","target_app":"com.test"}[/DEVICE_ACT]')
    assert acts and acts[0].action_type == act_mod.DEVICE_ACT
    assert acts[0].payload["direction"] == "up"


def test_swipe_requires_kill_switch(monkeypatch):
    from app.agent import loop as loop_mod
    monkeypatch.setattr(loop_mod, "AGENT_FLAGS", {"device_actions_enabled": False})
    out = asyncio.run(agent_tools._execute_act({"user_id": 3, "verb": "swipe", "direction": "up", "target_app": "com.test"}))
    assert out["ok"] is False
    assert "总开关" in out["text"]


def test_adapter_uses_central_decision_for_actions(monkeypatch):
    from app.device import actions as dev_actions
    from app.device import adapter

    async def _deny(**_kw):
        return dev_actions.Decision(allowed=False, reason="global_kill_switch_off")

    monkeypatch.setattr(dev_actions, "decide_action", _deny)
    out = asyncio.run(adapter.execute_action(
        user_id=3, target_app="com.test.app", plan={"path": "0/0/1"},
    ))
    assert out == {"ok": False, "detail": "global_kill_switch_off"}


def test_swipe_requires_explicit_target(monkeypatch):
    from app.agent import loop as loop_mod
    from app.device import actions as dev_actions

    monkeypatch.setattr(loop_mod, "AGENT_FLAGS", {"device_actions_enabled": True})

    async def _enabled(_uid):
        return True

    async def _policies(_uid):
        return {"action_swipe": "once_ever"}

    monkeypatch.setattr(dev_actions, "_account_actions_enabled", _enabled)
    monkeypatch.setattr(dev_actions, "configured_policies", _policies)
    out = asyncio.run(agent_tools._execute_swipe({"verb": "swipe", "direction": "up"}, 3))
    assert out["ok"] is False
    assert "显式 target_app" in out["text"]


def test_natural_swipe_without_package_does_not_synthesize_action():
    from app.application import chat_service

    state = {"context_messages": [{"role": "user", "content": "往上滑一下"}]}
    _, text = asyncio.run(chat_service._run_device_act_stage(
        final_state=state, full_text="往上滑一下", user_id=3,
        character_id=13, session_id=1,
    ))
    assert text == "往上滑一下"
