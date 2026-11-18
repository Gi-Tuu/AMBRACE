# -*- coding: utf-8 -*-
"""M8：工具端点的长期 service token（Core sidecar 鉴权）。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import agent_tools
from app.api.agent_tools import router
from app.auth.deps import get_current_user_id


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_service_token_accepted(client, monkeypatch):
    monkeypatch.setattr(agent_tools, "_AMBRACE_SERVICE_TOKEN", "svc-token-m8")
    monkeypatch.setattr(agent_tools, "_AMBRACE_SERVICE_USER_ID", 3)
    r = client.post("/api/agent/tools/device_act",
                    headers={"X-Agent-Service-Key": "svc-token-m8"},
                    json={"snapshot_id": "missing", "ref": "@x:e1", "verb": "click"})
    assert r.status_code == 409  # 鉴权通过，走到快照校验
    assert r.json()["detail"]["code"] == "STALE_REF"


def test_service_token_wrong_is_401(client, monkeypatch):
    monkeypatch.setattr(agent_tools, "_AMBRACE_SERVICE_TOKEN", "svc-token-m8")
    r = client.post("/api/agent/tools/device_act",
                    headers={"X-Agent-Service-Key": "wrong"},
                    json={"snapshot_id": "missing", "ref": "@x:e1", "verb": "click"})
    assert r.status_code == 401


def test_no_token_is_401(client, monkeypatch):
    monkeypatch.setattr(agent_tools, "_AMBRACE_SERVICE_TOKEN", "svc-token-m8")
    r = client.post("/api/agent/tools/device_act",
                    json={"snapshot_id": "missing", "ref": "@x:e1", "verb": "click"})
    assert r.status_code == 401


def test_core_status_unconfigured(client, monkeypatch):
    client.app.dependency_overrides[get_current_user_id] = lambda: 3
    monkeypatch.setattr(agent_tools, "CORE_BASE_URL", "")
    r = client.get("/api/agent/tools/core-status")
    assert r.status_code == 200
    assert r.json()["configured"] is False


def test_core_status_online_with_tools(client, monkeypatch):
    client.app.dependency_overrides[get_current_user_id] = lambda: 3
    monkeypatch.setattr(agent_tools, "CORE_BASE_URL", "http://core.test")
    monkeypatch.setenv("CORE_EXPECTED_TOOLS", "4")

    def fake_get(path, timeout=3.0):
        if path == "/health":
            return 200, {"status": "ok"}
        return 200, [{"name": "t"} for _ in range(4)]

    monkeypatch.setattr(agent_tools, "_get_core", fake_get)
    r = client.get("/api/agent/tools/core-status")
    assert r.status_code == 200
    body = r.json()
    assert body["online"] is True and body["tools"] == 4 and body["ok"] is True
