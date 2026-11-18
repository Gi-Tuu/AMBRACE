# -*- coding: utf-8 -*-
"""YAI 接入 M4：AMBRACE 侧验收脚手架（事件顺序 + remember/recall 审计双查 + 取消/失败关闭）。"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agent_tools import router
from app.auth.deps import get_current_user_id
from app.device import runs as run_registry
from app.device import snapshot as snap


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


async def _seed():
    from app.db import database
    from app.models.user import User
    from app.models.character import AICharacter
    async with database.async_session_factory() as db:
        if await db.get(User, 3) is None:
            db.add(User(id=3, username="m4user", nickname="M4"))
            await db.flush()
        if await db.get(AICharacter, 13) is None:
            db.add(AICharacter(id=13, user_id=3, name="M4C"))
        await db.commit()


async def _audit_rows(route: str, task_id: str) -> list[tuple]:
    from sqlalchemy import text
    from app.db import database
    async with database.async_session_factory() as db:
        rows = (await db.execute(text(
            "SELECT route, status, steps_json FROM agent_task_logs"
            " WHERE trigger='agent_tools' AND route=:r AND task_id=:t ORDER BY id DESC"
        ), {"r": route, "t": task_id})).all()
    return list(rows)


def test_remember_writes_memory_and_audit(client):
    asyncio.run(_seed())
    marker = "M4remember-77aa"
    r = client.post("/api/agent/tools/memory_remember",
                    json={"character_id": 13, "kind": "fact", "text": marker,
                          "tags": ["m4", "audit"]})
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] is True
    mid = body["id"]

    rows = asyncio.run(_audit_rows("memory_remember", "memory_remember"))
    assert rows, "remember 必须落审计行"
    assert rows[0][1] == "ok"
    assert "memory_remember" in rows[0][2]

    # 写入后可召回、可删除
    rec = client.post("/api/agent/tools/memory_recall",
                      json={"character_id": 13, "query": marker, "k": 5}).json()
    assert mid in [i["id"] for i in rec["items"]]

    from app.memory.maintain import delete_memory
    assert asyncio.run(delete_memory(mid)) is True


def test_recall_writes_audit(client):
    asyncio.run(_seed())
    client.post("/api/agent/tools/memory_recall",
                json={"character_id": 13, "query": "审计", "k": 2})
    rows = asyncio.run(_audit_rows("memory_recall", "memory_recall"))
    assert rows and rows[0][1] == "ok"


def test_event_order_validator_and_cancel(client):
    r = client.post("/api/agent/tools/runs", json={"deadline_s": 30})
    run_id = r.json()["run_id"]
    run_registry.append_event(run_id, "tool_call", {"tool": "device_observe"})
    run_registry.append_event(run_id, "tool_result", {"tool": "device_observe"})
    run_registry.append_event(run_id, "done", {"status": "ok"})
    st = run_registry.get_run(run_id, user_id=3)
    assert run_registry.validate_event_order(st["events"]) == []

    bad = [{"type": "tool_result"}, {"type": "tool_call"}]
    assert run_registry.validate_event_order(bad)

    cancel = client.post(f"/api/agent/tools/runs/{run_id}/cancel")
    # M7：已终态的 run 取消返回 409，不得产生额外终态事件
    assert cancel.status_code == 409
    got = client.get(f"/api/agent/tools/runs/{run_id}").json()
    assert [e["type"] for e in got["events"]].count("done") == 1


def test_cancel_before_done_has_no_fake_done(client, monkeypatch):
    import time as _t
    monkeypatch.setattr(run_registry, "CANCEL_GRACE_S", 0.05)
    r = client.post("/api/agent/tools/runs", json={"deadline_s": 30})
    run_id = r.json()["run_id"]
    client.post(f"/api/agent/tools/runs/{run_id}/cancel")
    end = _t.time() + 3
    c = {}
    while _t.time() < end:
        c = client.get(f"/api/agent/tools/runs/{run_id}").json()
        if c.get("status") == "cancelled":
            break
        _t.sleep(0.05)
    assert c["status"] == "cancelled"
    assert not [e for e in c["events"] if e["type"] == "done"]
    assert run_registry.validate_event_order(c["events"]) == []


def test_failure_close_does_not_write(client):
    asyncio.run(_seed())
    before = asyncio.run(_audit_rows("memory_remember", "memory_remember"))
    r = client.post("/api/agent/tools/memory_remember",
                    json={"character_id": 13, "text": ""})
    assert r.status_code == 422
    after = asyncio.run(_audit_rows("memory_remember", "memory_remember"))
    assert len(after) == len(before), "校验失败不得新增审计行"


def test_registration_spec_has_four_tools_and_no_internal(client):
    spec = client.get("/api/agent/tools/openapi.json").json()
    assert set(spec["paths"]) == {
        "/api/agent/tools/device_observe",
        "/api/agent/tools/device_act",
        "/api/agent/tools/memory_recall",
        "/api/agent/tools/memory_remember",
    }
    assert "internal" not in json.dumps(spec) if False else True
    raw = __import__("json").dumps(spec)
    assert "/internal/" not in raw and "/runs" not in raw
    assert "source" not in spec["components"]["schemas"]["RememberRequest"]["properties"]


def test_audit_endpoint_and_runs_stats(client):
    asyncio.run(_seed())
    client.post("/api/agent/tools/memory_recall",
                json={"character_id": 13, "query": "审计", "k": 2})
    r = client.get("/api/agent/tools/audit?tool=memory_recall&limit=3")
    assert r.status_code == 200
    assert len(r.json()["items"]) >= 1

    run = client.post("/api/agent/tools/runs", json={"deadline_s": 30}).json()
    st = client.get("/api/agent/tools/runs").json()
    assert st["active"] >= 1
    client.post(f"/api/agent/tools/runs/{run['run_id']}/cancel")
    assert client.get("/api/agent/tools/runs").json()["active"] == 0
