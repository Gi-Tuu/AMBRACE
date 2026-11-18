# -*- coding: utf-8 -*-
"""M7：execute gating、原子 close_run、finalize 幂等/白名单、cancel 终态。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import agent_tools
from app.api.agent_tools import router
from app.auth.deps import get_current_user_id, require_server_admin
from app.device import runs as run_registry


@pytest.fixture(autouse=True)
def _clean():
    run_registry.clear()
    agent_tools._CORE_CALLS.clear()
    yield


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: 3
    return TestClient(app)


def _run(client, **body):
    return client.post("/api/agent/tools/runs", json=body)


def test_execute_disabled_returns_409(client):
    r = _run(client, task="x", execute=True)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "M7_DISABLED"


def test_close_run_is_idempotent():
    st = run_registry.create_run(user_id=3, deadline_s=30)
    assert run_registry.close_run(st.run_id, "error", {"message": "boom"}) is True
    assert run_registry.close_run(st.run_id, "done", {"status": "ok"}) is False
    got = run_registry.get_run(st.run_id, user_id=3)
    assert got["status"] == "error"
    assert got["closed"] is True
    assert [e["type"] for e in got["events"]][-1] == "run_terminal"


def test_finalize_whitelist_and_once(client, monkeypatch):
    monkeypatch.setattr(agent_tools, "_M7_FINALIZE_ENABLED", True)
    client.app.dependency_overrides[require_server_admin] = lambda: 3
    rid = _run(client, deadline_s=30).json()["run_id"]
    r = client.post(f"/api/agent/tools/internal/runs/{rid}/finalize",
                    json={"status": "done",
                          "done": {"status": "ok", "final_text": "hi",
                                   "verification": {"ok": True},
                                   "junk": "drop-me"}})
    assert r.status_code == 200
    got = run_registry.get_run(rid, user_id=3)
    done = [e for e in got["events"] if e["type"] == "done"][0]
    assert done["data"] == {"status": "ok", "final_text": "hi",
                            "verification": {"ok": True}}
    again = client.post(f"/api/agent/tools/internal/runs/{rid}/finalize",
                        json={"status": "done", "done": {"status": "ok"}})
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "ALREADY_FINALIZED"


def test_cancel_after_terminal_conflict(client):
    rid = _run(client, deadline_s=30).json()["run_id"]
    assert run_registry.close_run(rid, "expired") is True
    r = client.post(f"/api/agent/tools/runs/{rid}/cancel")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ALREADY_TERMINAL"


def test_execute_immediate_cancel(client, monkeypatch):
    monkeypatch.setattr(agent_tools, "_M7_FINALIZE_ENABLED", True)
    monkeypatch.setattr(agent_tools, "CORE_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(agent_tools, "CORE_SERVICE_TOKEN", "t")
    async def fake(run_id, task, deadline):
        agent_tools._SET_RUNNING(run_id)
        await __import__("asyncio").sleep(5)
    monkeypatch.setattr(agent_tools, "_core_execute", fake)
    r = _run(client, task="x", execute=True)
    assert r.status_code == 202
    rid = r.json()["run_id"]
    c = client.post(f"/api/agent/tools/runs/{rid}/cancel")
    assert c.status_code == 200
    assert c.json()["status"] == "cancelling"


def test_starting_run_deadline_expires():
    import asyncio
    async def main():
        st = run_registry.create_run(user_id=3, deadline_s=0.05)
        st.status = "starting"
        await asyncio.sleep(0.3)
        return run_registry.get_run(st.run_id, user_id=3)
    got = asyncio.run(main())
    assert got["status"] == "expired"


def test_terminal_run_with_header_rejects_device_act(client):
    rid = _run(client, deadline_s=30).json()["run_id"]
    run_registry._RUNS[rid].source = "core"
    run_registry.close_run(rid, "cancelled")
    r = client.post("/api/agent/tools/device_act",
                    headers={"X-Agent-Run-Id": rid, "X-Agent-Call-Id": "c1"},
                    json={"snapshot_id": "x", "ref": "@x:e1", "verb": "click"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "RUN_TERMINAL"


def test_failure_appends_tool_result(client):
    rid = _run(client, deadline_s=30).json()["run_id"]
    run_registry._RUNS[rid].source = "core"
    r = client.post("/api/agent/tools/device_act",
                    headers={"X-Agent-Run-Id": rid, "X-Agent-Call-Id": "c2"},
                    json={"snapshot_id": "missing", "ref": "@x:e1", "verb": "click"})
    assert r.status_code == 409
    got = run_registry.get_run(rid, user_id=3)
    types = [e["type"] for e in got["events"]]
    assert "tool_call" in types and "tool_result" in types
    res = [e for e in got["events"] if e["type"] == "tool_result"][0]
    assert res["data"]["ok"] is False
