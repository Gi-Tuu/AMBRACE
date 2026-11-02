# -*- coding: utf-8 -*-
"""只读浏览 API：认证、角色归属、插件可见/启用、参数白名单；不触发写路径。"""
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


async def _seed(u=98101, c=98103):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        await db.execute(text("INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (:u,:n,'n','x')"), {"u": u, "n": f"u{u}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
            "is_active,cognitive_loop_enabled,memory_v2_enabled) VALUES (:c,:u,'c',0,0,1,0,0)"),
            {"c": c, "u": u})
        await db.commit()


def _client(uid=98101):
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
    registry._db_config["garden_forum"] = {"garden_forum_on": True}
    asyncio.run(_seed())

    async def fake_readonly(cid, *, owner_user_id=None, limit=20):
        if owner_user_id != 98101:
            return {"ok": False, "code": "character_not_owned_or_site_mismatch"}
        return {"ok": True, "mode": "readonly", "character_id": str(cid),
                "remote_id": "sam", "items": [{"id": 1, "title": "合成标题"}], "count": 1}
    monkeypatch.setattr(mod, "run_readonly_browse", fake_readonly)
    return mod, registry


def test_api_success_owner_only(wired):
    mod, registry = wired
    r = _client(98101).post("/api/v1/plugins/garden_forum/readonly",
                            json={"character_id": 98103})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "readonly"
    assert r.json()["remote_id"] == "sam"


def test_api_rejects_extra_fields_and_bad_id(wired):
    mod, registry = wired
    c = _client(98101)
    assert c.post("/api/v1/plugins/garden_forum/readonly",
                  json={"character_id": 98103, "url": "https://evil"}).status_code == 400
    assert c.post("/api/v1/plugins/garden_forum/readonly",
                  json={"character_id": True}).status_code == 400
    assert c.post("/api/v1/plugins/garden_forum/readonly", json={}).status_code == 400


def test_api_rejects_other_tenant_and_disabled(wired):
    mod, registry = wired
    assert _client(99999).post("/api/v1/plugins/garden_forum/readonly",
                               json={"character_id": 98103}).status_code == 403
    registry._enabled["garden_forum"] = False
    assert _client(98101).post("/api/v1/plugins/garden_forum/readonly",
                               json={"character_id": 98103}).status_code == 409


def test_api_maps_readonly_reject_codes(wired):
    mod, registry = wired
    async def reject(cid, *, owner_user_id=None, limit=20):
        return {"ok": False, "code": "not_manual_pilot"}
    mod.run_readonly_browse = reject
    assert _client(98101).post("/api/v1/plugins/garden_forum/readonly",
                               json={"character_id": 98103}).status_code == 409
    async def remote(cid, *, owner_user_id=None, limit=20):
        return {"ok": False, "code": "read_failed"}
    mod.run_readonly_browse = remote
    assert _client(98101).post("/api/v1/plugins/garden_forum/readonly",
                               json={"character_id": 98103}).status_code == 502


def test_api_family_root_uses_character_owner_for_binding(wired, monkeypatch):
    import asyncio
    from app.db.database import async_session_factory
    from sqlalchemy import text
    async def seed_family():
        async with async_session_factory() as db:
            await db.execute(text("INSERT OR IGNORE INTO users(id,username,nickname,password_hash,parent_id) VALUES (98201,'famroot','root','x',NULL)"))
            await db.execute(text("INSERT OR IGNORE INTO users(id,username,nickname,password_hash,parent_id) VALUES (98202,'sub','sub','x',98201)"))
            await db.execute(text(
                "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
                "is_active,cognitive_loop_enabled,memory_v2_enabled) VALUES (98203,98202,'c',0,0,1,0,0)"))
            await db.commit()
    asyncio.run(seed_family())
    seen = {}
    async def fake(cid, *, owner_user_id=None, limit=20):
        seen["owner"] = owner_user_id
        return {"ok": True, "mode": "readonly", "character_id": str(cid),
                "remote_id": "sam", "items": [], "count": 0}
    mod, registry = wired
    monkeypatch.setattr(mod, "run_readonly_browse", fake)
    r = _client(98201).post("/api/v1/plugins/garden_forum/readonly",
                            json={"character_id": 98203})
    assert r.status_code == 200, r.text
    assert seen["owner"] == 98202
