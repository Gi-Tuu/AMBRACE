# -*- coding: utf-8 -*-
"""YAI 接入 M1：`/api/agent/tools/*` 契约（含 review 修正：无树入参/最小 spec/typed response/202 语义）。"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agent_tools import router
from app.auth.deps import get_current_user_id
from app.device import snapshot as snap


def _tree(*children):
    return {"role": "window", "name": "测试窗口", "children": list(children)}


def _btn(name, nid=None):
    return {"role": "button", "name": name, "native_id": nid,
            "available_actions": ["Click", "SetFocus"]}


def _scroll():
    return {"role": "panel", "name": "列表", "available_actions": ["Scroll"]}


@pytest.fixture(autouse=True)
def _clean():
    snap.clear_store()
    yield


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: 3
    return TestClient(app)


def _internal_observe(client, tree, limit=50, offset=0):
    return client.post(f"/api/agent/tools/internal/observe?limit={limit}&offset={offset}",
                       json={"tree": tree})


def test_public_observe_rejects_tree_and_model_supplied_tree(client):
    # 公开操作不接受树（安全边界）
    r = client.post("/api/agent/tools/device_observe",
                    json={"target_app": "com.android.settings", "tree": _tree(_btn("确定"))})
    assert r.status_code == 422
    r2 = client.post("/api/agent/tools/device_observe",
                     json={"target_app": "com.android.settings"})
    assert r2.status_code == 503
    assert r2.json()["detail"]["code"] == "DEVICE_CAPTURE_UNAVAILABLE"


def test_internal_observe_paginates_and_bounds(client):
    tree = _tree(*[_btn(f"按钮{i}", f"id{i}") for i in range(10)])
    r = _internal_observe(client, tree, limit=3)
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 3 and body["has_more"] is True
    assert body["expires_in_s"] == 120.0
    assert {"ref", "role", "name", "native_id", "path", "actions"} <= set(body["items"][0])
    r2 = _internal_observe(client, tree, limit=200, offset=0)
    assert len(json.dumps(r2.json(), ensure_ascii=False)) <= 16_000


def test_act_scroll_is_pending_not_success(client, monkeypatch):
    from app.api import agent_tools as agent_tools_mod

    async def _granted(_uid):
        return {"action_tap": "once_ever"}

    monkeypatch.setattr(agent_tools_mod, "configured_policies", _granted)
    snap_id = _internal_observe(client, _tree(_scroll())).json()["snapshot_id"]
    ref = next(iter(snap.get_snapshot(snap_id).refs))
    r = client.post("/api/agent/tools/device_act",
                    json={"snapshot_id": snap_id, "ref": ref, "verb": "scroll"})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending_delivery"
    assert body["executed"] is False
    assert body["delivery"] == "not_delivered"


def test_act_click_requires_approval_and_stale_ref(client, monkeypatch):
    from app.api import agent_tools as agent_tools_mod

    async def _empty(_uid):
        return {}

    monkeypatch.setattr(agent_tools_mod, "configured_policies", _empty)
    snap_id = _internal_observe(client, _tree(_btn("确定", "ok"))).json()["snapshot_id"]
    ref = next(iter(snap.get_snapshot(snap_id).refs))
    r = client.post("/api/agent/tools/device_act",
                    json={"snapshot_id": snap_id, "ref": ref, "verb": "click"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "APPROVAL_REQUIRED"

    # 公开操作不再接受 current_tree，stale 由服务端复核路径覆盖（单测见设备族）
    wrong_verb = client.post("/api/agent/tools/device_act",
                             json={"snapshot_id": snap_id, "ref": ref, "verb": "scroll"})
    assert wrong_verb.status_code == 400
    assert wrong_verb.json()["detail"]["code"] == "INVALID_ARGS"


def test_open_app_and_snapshot_fail_closed(client, monkeypatch):
    from app.api import agent_tools as mod

    async def _fail(**kwargs):
        return {"ok": False, "detail": "device_unavailable"}

    monkeypatch.setattr(mod.device_adapter, "open_app", _fail)
    r = client.post("/api/agent/tools/device_open_app", json={"package": "com.android.settings"})
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "ACTION_FAILED"
    snap_r = client.post("/api/agent/tools/device_snapshot", json={})
    assert snap_r.status_code == 503


def test_minimal_openapi_scope_components_and_typed_responses(client):
    spec = client.get("/api/agent/tools/openapi.json").json()
    assert spec["openapi"] == "3.1.0"
    assert set(spec["paths"]) == {
        "/api/agent/tools/device_observe",
        "/api/agent/tools/device_act",
        "/api/agent/tools/memory_recall",
        "/api/agent/tools/memory_remember",
    }
    assert "manifest" not in json.dumps(spec["paths"])
    schemas = spec["components"]["schemas"]
    assert {"ObserveRequest", "ObserveResponse", "ActRequest", "ActResponse",
            "ErrorBody", "RecallRequest", "RecallResponse", "RememberRequest",
            "RememberResponse", "RecallItem"} <= set(schemas)
    # 后台模型/凭据字段不得泄漏
    leaked = {"CharacterResponse", "MemoryResponse", "LlmConfigUpsert", "AuthResponse",
              "MCPServerCreate", "api_key"}
    assert not leaked & {k for k in schemas} and "api_key" not in json.dumps(spec)
    # typed response 必须有字段
    act_200 = spec["paths"]["/api/agent/tools/device_act"]["post"]["responses"]["202"]
    assert "properties" in act_200["content"]["application/json"]["schema"] or \
        "$ref" in act_200["content"]["application/json"]["schema"]
    assert spec["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
    for ops in spec["paths"].values():
        for op in ops.values():
            assert op["security"] == [{"BearerAuth": []}]
    assert "tree" not in json.dumps(schemas["ObserveRequest"])
    assert "path" not in json.dumps(schemas["ActRequest"])  # path 只取服务端快照映射
    assert "current_tree" not in json.dumps(schemas["ActRequest"])


def test_manifest_lists_registered_and_deferred(client):
    m = client.get("/api/agent/tools/manifest").json()
    assert m["registered_tools"] == ["device_observe", "device_act",
                                     "memory_recall", "memory_remember"]
    assert set(m["deferred_tools"]) == {"device_open_app", "device_snapshot"}
    assert m["limits"]["max_result_chars"] == 16000
