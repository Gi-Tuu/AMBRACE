# -*- coding: utf-8 -*-
"""观察入口 API：observe 显式布尔、只多传该字段；原 readonly 兼容。"""
import asyncio
import pathlib
import sys

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from sqlalchemy import text

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "https://garden.example.invalid"


async def _seed(u=98301, c=98303):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        await db.execute(text("INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (:u,:n,'n','x')"), {"u": u, "n": f"u{u}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
            "is_active,cognitive_loop_enabled,memory_v2_enabled) VALUES (:c,:u,'c',0,0,1,0,0)"),
            {"c": c, "u": u})
        await db.commit()


def _client(uid=98301):
    from app.api import plugins as plugins_api
    from app.auth.deps import get_current_user_id
    app = FastAPI()
    app.include_router(plugins_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: uid
    return TestClient(app)


@pytest.fixture()
def wired(monkeypatch):
    from app.plugins import plugin_store, registry
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    mod = registry._loaded["garden_forum"]["module"]
    registry._enabled["garden_forum"] = True
    registry._db_config["garden_forum"] = {"garden_forum_on": True,
                                           "garden_forum_observe": True,
                                           "garden_forum_url": BASE}
    asyncio.run(_seed())
    seen = {}
    async def fake(cid, *, owner_user_id=None, limit=20, observe=False):
        seen.update(cid=cid, owner=owner_user_id, observe=observe)
        return {"ok": True, "mode": "readonly", "character_id": str(cid),
                "remote_id": "sam", "items": [], "count": 0, "observed": observe}
    monkeypatch.setattr(mod, "run_readonly_browse", fake)
    return mod, seen


def test_api_observe_true_passed_explicitly(wired):
    mod, seen = wired
    r = _client().post("/api/v1/plugins/garden_forum/readonly",
                       json={"character_id": 98303, "observe": True})
    assert r.status_code == 200, r.text
    assert seen["observe"] is True and seen["owner"] == 98301


def test_api_observe_default_false_and_type_checked(wired):
    mod, seen = wired
    r = _client().post("/api/v1/plugins/garden_forum/readonly",
                       json={"character_id": 98303})
    assert r.status_code == 200 and seen["observe"] is False
    assert _client().post("/api/v1/plugins/garden_forum/readonly",
                          json={"character_id": 98303, "observe": "true"}).status_code == 400
    assert _client().post("/api/v1/plugins/garden_forum/readonly",
                          json={"character_id": 98303, "observe": 1}).status_code == 400


def test_status_and_clear_routes(wired, monkeypatch):
    mod, seen = wired
    monkeypatch.setattr(mod, "observation_status", lambda cid, owner_user_id=None: {
        "ok": True, "enabled": True, "observed": True, "expires_at": 2000,
        "items": [{"id": 7, "title": "合成"}]})
    r = _client().get("/api/v1/plugins/garden_forum/observation",
                      params={"character_id": 98303})
    assert r.status_code == 200, r.text
    assert r.json()["observed"] is True and r.json()["items"][0]["id"] == 7
    monkeypatch.setattr(mod, "clear_readonly_observation", lambda cid, owner_user_id=None: {
        "ok": True, "cleared": True})
    r = _client().request("DELETE", "/api/v1/plugins/garden_forum/observation",
                          json={"character_id": 98303})
    assert r.status_code == 200 and r.json()["cleared"] is True
    assert _client(99999).get("/api/v1/plugins/garden_forum/observation",
                              params={"character_id": 98303}).status_code == 403
    assert _client().request("DELETE", "/api/v1/plugins/garden_forum/observation",
                             json={"character_id": 98303, "x": 1}).status_code == 400


def test_status_reject_maps_403(wired, monkeypatch):
    mod, seen = wired
    monkeypatch.setattr(mod, "observation_status", lambda cid, owner_user_id=None: {
        "ok": False, "code": "character_not_owned"})
    r = _client().get("/api/v1/plugins/garden_forum/observation",
                      params={"character_id": 98303})
    assert r.status_code == 403
