# -*- coding: utf-8 -*-
"""YAI 接入 M2：run registry / cancel / deadline / once_ever 审批门。"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import agent_tools as agent_tools_mod
from app.api.agent_tools import router
from app.auth.deps import get_current_user_id
from app.device import runs as run_registry
from app.device import snapshot as snap


def _tree(*children):
    return {"role": "window", "name": "窗口", "children": list(children)}


def _btn(name, nid=None):
    return {"role": "button", "name": name, "native_id": nid,
            "available_actions": ["Click"]}


@pytest.fixture(autouse=True)
def _clean():
    snap.clear_store()
    run_registry.clear()
    yield


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: 3
    return TestClient(app)


def _wait_cancelled(client, run_id, timeout=3.0):
    import time as _t
    end = _t.time() + timeout
    while _t.time() < end:
        g = client.get(f"/api/agent/tools/runs/{run_id}").json()
        if g["status"] == "cancelled":
            return g
        _t.sleep(0.05)
    return g


def test_run_create_cancel_and_no_fake_done(client, monkeypatch):
    monkeypatch.setattr(run_registry, "CANCEL_GRACE_S", 0.05)
    r = client.post("/api/agent/tools/runs",
                    json={"session_id": "character:42:user:7", "task": "打开设置",
                          "tools": ["device_observe"], "approval_mode": "ask", "deadline_s": 30})
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"].startswith("run-")
    assert body["status"] == "running"
    run_id = body["run_id"]

    c = client.post(f"/api/agent/tools/runs/{run_id}/cancel")
    assert c.status_code == 200
    g = _wait_cancelled(client, run_id)
    assert g["status"] == "cancelled"
    assert not [e for e in g["events"] if e["type"] == "done"]
    term = [e for e in g["events"] if e["type"] == "run_terminal"]
    assert term and term[0]["data"]["status"] == "cancelled"


def test_run_deadline_expires_without_fake_done():
    import asyncio

    async def _case():
        st = run_registry.create_run(user_id=3, deadline_s=0.05)
        await asyncio.sleep(0.2)
        return run_registry.get_run(st.run_id, user_id=3)

    g = asyncio.run(_case())
    assert g["status"] == "expired"
    assert not [e for e in g["events"] if e["type"] == "done"]
    term = [e for e in g["events"] if e["type"] == "run_terminal"]
    assert term and term[0]["data"]["synthetic"] is True
    assert term[0]["data"]["source"] == "registry"


def test_run_id_passthrough(client):
    given = "run-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    r = client.post("/api/agent/tools/runs", json={"run_id": given, "deadline_s": 30})
    assert r.json()["run_id"] == given
    assert client.get(f"/api/agent/tools/runs/{given}").json()["run_id"] == given


def test_run_scoped_to_user(client):
    r = client.post("/api/agent/tools/runs", json={"deadline_s": 30})
    run_id = r.json()["run_id"]
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: 99
    other = TestClient(app)
    assert other.get(f"/api/agent/tools/runs/{run_id}").status_code == 404


def _observe_snapshot(client):
    return client.post("/api/agent/tools/internal/observe",
                       json={"tree": _tree(_btn("确定", "ok")),
                             "target_app": "com.android.settings"}).json()["snapshot_id"]


def test_act_click_blocked_without_once_ever(client, monkeypatch):
    async def _empty(_uid):
        return {}

    monkeypatch.setattr(agent_tools_mod, "configured_policies", _empty)
    snap_id = _observe_snapshot(client)
    ref = next(iter(snap.get_snapshot(snap_id).refs))
    r = client.post("/api/agent/tools/device_act",
                    json={"snapshot_id": snap_id, "ref": ref, "verb": "click"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "APPROVAL_REQUIRED"


def test_act_click_allowed_after_once_ever(client, monkeypatch):
    async def _granted(_uid):
        return {"action_tap": "once_ever"}

    monkeypatch.setattr(agent_tools_mod, "configured_policies", _granted)
    async def _exec(**kwargs):
        return {"ok": True, "detail": "已点击“确定”"}

    monkeypatch.setattr(agent_tools_mod.device_adapter, "execute_action", _exec)
    snap_id = _observe_snapshot(client)
    ref = next(iter(snap.get_snapshot(snap_id).refs))
    r = client.post("/api/agent/tools/device_act",
                    json={"snapshot_id": snap_id, "ref": ref, "verb": "click"})
    assert r.status_code == 200
    assert r.json()["status"] == "executed"
    assert r.json()["delivery"] == "delivered"


def test_expired_snapshot_maps_to_stale_ref(client, monkeypatch):
    async def _granted(_uid):
        return {"action_tap": "once_ever"}

    monkeypatch.setattr(agent_tools_mod, "configured_policies", _granted)
    snap_id = _observe_snapshot(client)
    ref = next(iter(snap.get_snapshot(snap_id).refs))
    snap.clear_store()
    r = client.post("/api/agent/tools/device_act",
                    json={"snapshot_id": snap_id, "ref": ref, "verb": "scroll"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "STALE_REF"


def test_runs_not_in_registration_spec(client):
    spec = client.get("/api/agent/tools/openapi.json").json()
    assert not any("/runs" in p for p in spec["paths"])


def test_event_offsets_and_bounded_buffer(monkeypatch):
    st = run_registry.create_run(user_id=3, deadline_s=30)
    run_registry.append_event(st.run_id, "tool_call", {"tool": "a"})
    run_registry.append_event(st.run_id, "tool_result", {"tool": "a"})
    run_registry.append_event(st.run_id, "done", {"status": "ok"})
    got = run_registry.get_run(st.run_id, user_id=3)
    assert got["last_offset"] == 3
    assert [e["offset"] for e in got["events"]] == [1, 2, 3]
    assert got["status"] == "done"

    monkeypatch.setattr(run_registry, "MAX_EVENTS", 2)
    st2 = run_registry.create_run(user_id=3, deadline_s=30)
    for i in range(5):
        run_registry.append_event(st2.run_id, "model_message", {"i": i})
    got2 = run_registry.get_run(st2.run_id, user_id=3)
    assert got2["last_offset"] == 5
    assert len(got2["events"]) == 2
    assert [e["offset"] for e in got2["events"]] == [4, 5]


def test_terminal_gets_offset(client, monkeypatch):
    monkeypatch.setattr(run_registry, "CANCEL_GRACE_S", 0.05)
    r = client.post("/api/agent/tools/runs", json={"deadline_s": 30})
    rid = r.json()["run_id"]
    client.post(f"/api/agent/tools/runs/{rid}/cancel")
    g = _wait_cancelled(client, rid)
    assert g["last_offset"] == 1
    assert g["events"][0]["type"] == "run_terminal"
    assert g["events"][0]["offset"] == 1
