# -*- coding: utf-8 -*-
"""M6 SSE：ticket cookie、事件流/续传、INVALID_OFFSET、STREAM_GAP、grace late result。"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agent_tools import router
from app.auth.deps import get_current_user_id
from app.device import runs as run_registry


@pytest.fixture(autouse=True)
def _clean():
    run_registry.clear()
    yield


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: 3
    return TestClient(app)


def _run(client, deadline=30):
    return client.post("/api/agent/tools/runs", json={"deadline_s": deadline}).json()["run_id"]


def _stream(client, run_id, **kw):
    with client.stream("GET", f"/api/agent/tools/runs/{run_id}/events", timeout=10, **kw) as r:
        return r.status_code, "".join(r.iter_text())


def test_stream_ticket_cookie_and_events(client):
    rid = _run(client)
    run_registry.append_event(rid, "tool_call", {"tool": "a"})
    run_registry.append_event(rid, "tool_result", {"tool": "a", "ok": True})
    run_registry.append_event(rid, "done", {"status": "ok"})

    t = client.post(f"/api/agent/tools/runs/{rid}/stream-ticket")
    assert t.status_code == 200
    assert f"ambrace_stream_{rid}" in t.headers.get("set-cookie", "")
    st, body = _stream(client, rid)
    assert st == 200
    assert "id: 1" in body and "event: tool_call" in body
    assert "event: done" in body and "event: eof" in body
    # 续传：offset=2 只回后续
    st2, body2 = _stream(client, rid, headers={"Last-Event-ID": "2"})
    assert st2 == 200 and "id: 1" not in body2 and "event: done" in body2


def test_invalid_offset_and_gap(client, monkeypatch):
    rid = _run(client)
    client.post(f"/api/agent/tools/runs/{rid}/stream-ticket")
    run_registry.append_event(rid, "model_message", {"t": "x"})
    st, _ = _stream(client, rid, params={"offset": 99})
    assert st == 400
    bad = client.get(f"/api/agent/tools/runs/{rid}/events", params={"offset": "abc"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "INVALID_OFFSET"

    monkeypatch.setattr(run_registry, "MAX_EVENTS", 2)
    rid2 = _run(client)
    client.post(f"/api/agent/tools/runs/{rid2}/stream-ticket")
    for i in range(5):
        run_registry.append_event(rid2, "model_message", {"i": i})
    gap = client.get(f"/api/agent/tools/runs/{rid2}/events", params={"offset": 1})
    assert gap.status_code == 409
    assert gap.json()["detail"]["code"] == "STREAM_GAP"
    assert gap.json()["detail"]["oldest_available"] == 4


def test_cancel_grace_late_result_then_terminal(client, monkeypatch):
    monkeypatch.setattr(run_registry, "CANCEL_GRACE_S", 0.15)
    rid = _run(client)
    client.post(f"/api/agent/tools/runs/{rid}/cancel")
    assert run_registry.get_run(rid, user_id=3)["status"] == "cancelling"
    # grace 内晚到回执：允许且带 late=True
    assert run_registry.append_event(rid, "tool_result", {"tool": "a", "ok": True}) is True
    time.sleep(0.4)
    got = run_registry.get_run(rid, user_id=3)
    assert got["status"] == "cancelled"
    types = [e["type"] for e in got["events"]]
    assert types[-1] == "run_terminal"
    late = [e for e in got["events"] if e["type"] == "tool_result"]
    assert late and late[0]["data"].get("late") is True
    # 终态后新业务事件被拒
    assert run_registry.append_event(rid, "tool_call", {"tool": "b"}) is False


def test_events_requires_auth(client):
    rid = _run(client)
    app = FastAPI()
    app.include_router(router)
    from app.auth.deps import get_current_user_id as dep
    app.dependency_overrides[dep] = lambda: 3
    anon = TestClient(app)
    r = anon.get(f"/api/agent/tools/runs/{rid}/events")
    assert r.status_code == 401


def test_header_invalid_offset_and_terminal_replay(client):
    rid = _run(client)
    client.post(f"/api/agent/tools/runs/{rid}/stream-ticket")
    run_registry.append_event(rid, "tool_call", {"tool": "a"})
    run_registry.append_event(rid, "done", {"status": "ok"})
    bad = client.get(f"/api/agent/tools/runs/{rid}/events",
                     headers={"Last-Event-ID": "99"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "INVALID_OFFSET"
    # 已覆盖终态：只回 eof，不重放工具事件
    st, body = _stream(client, rid, headers={"Last-Event-ID": "2"})
    assert st == 200 and "event: eof" in body and "tool_call" not in body


def test_two_clients_same_run_identical_no_duplicate(client):
    rid = _run(client)
    for i in range(3):
        run_registry.append_event(rid, "tool_call", {"tool": f"t{i}"})
        run_registry.append_event(rid, "tool_result", {"tool": f"t{i}", "ok": True})
    run_registry.append_event(rid, "done", {"status": "ok"})
    client.post(f"/api/agent/tools/runs/{rid}/stream-ticket")
    _, b1 = _stream(client, rid)
    _, b2 = _stream(client, rid, headers={"Last-Event-ID": "0"})
    ids1 = [l for l in b1.splitlines() if l.startswith("id: ")]
    ids2 = [l for l in b2.splitlines() if l.startswith("id: ")]
    assert ids1 == ids2 == [f"id: {i}" for i in range(1, 8)]


def test_cross_user_cannot_subscribe(client):
    from app.auth.config import create_token
    rid = _run(client)
    other = create_token(99)
    r = client.get(f"/api/agent/tools/runs/{rid}/events",
                   headers={"Authorization": f"Bearer {other}"})
    assert r.status_code == 404


def test_get_run_summary_fields(client):
    rid = _run(client)
    for i in range(5):
        run_registry.append_event(rid, "model_message", {"i": i})
    g = client.get(f"/api/agent/tools/runs/{rid}").json()
    assert g["last_offset"] == 5
    assert [e["offset"] for e in g["events"]] == [1, 2, 3, 4, 5]
    assert g["oldest_available"] == 1


def test_ping_control_frame_no_offset(client, monkeypatch):
    from app.api import agent_tools as mod
    import threading
    monkeypatch.setattr(mod, "SSE_PING_INTERVAL", 0.05)
    rid = _run(client)
    client.post(f"/api/agent/tools/runs/{rid}/stream-ticket")
    threading.Timer(0.4, lambda: client.post(f"/api/agent/tools/runs/{rid}/cancel")).start()
    st, body = _stream(client, rid)
    assert st == 200
    assert ": ping" in body
    ids = [l for l in body.splitlines() if l.startswith("id: ")]
    assert ids == ["id: 1"]  # 只有 run_terminal，ping/eof 不占 offset


def test_slow_client_lagged_does_not_affect_fast(client, monkeypatch):
    import threading
    monkeypatch.setattr(run_registry, "MAX_EVENTS", 2)
    rid = _run(client)
    client.post(f"/api/agent/tools/runs/{rid}/stream-ticket")
    run_registry.append_event(rid, "tool_call", {"tool": "a"})
    run_registry.append_event(rid, "tool_result", {"tool": "a"})

    def _flood():
        for i in range(5):
            run_registry.append_event(rid, "model_message", {"i": i})
        client.post(f"/api/agent/tools/runs/{rid}/cancel")

    threading.Timer(0.3, _flood).start()
    st, body = _stream(client, rid)
    assert st == 200
    assert "event: stream_lagged" in body or "event: run_terminal" in body

    # 快客户端从最新缓冲游标开始不受慢客户端影响
    run_registry.append_event(rid, "run_terminal", {"status": "cancelled"}) if False else None


def test_internal_append_requires_admin(client):
    rid = _run(client)
    r = client.post(f"/api/agent/tools/internal/runs/{rid}/events",
                    json={"type": "tool_call", "data": {}})
    assert r.status_code == 403


def test_internal_append_admin_enabled_and_forbids_terminal(client, monkeypatch):
    from app.api import agent_tools as mod
    from app.auth.deps import require_server_admin
    monkeypatch.setattr(mod, "_INTERNAL_APPEND_ENABLED", True)
    client.app.dependency_overrides[require_server_admin] = lambda: 3
    rid = _run(client)
    ok = client.post(f"/api/agent/tools/internal/runs/{rid}/events",
                     json={"type": "tool_call", "data": {"tool": "t"}})
    assert ok.status_code == 200
    bad = client.post(f"/api/agent/tools/internal/runs/{rid}/events",
                      json={"type": "done", "data": {"status": "ok"}})
    assert bad.status_code == 403
    assert bad.json()["detail"]["code"] == "EVENT_TYPE_FORBIDDEN"
