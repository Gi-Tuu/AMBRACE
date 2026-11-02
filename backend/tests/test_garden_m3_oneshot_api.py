# -*- coding: utf-8 -*-
"""花园 one-shot 受认证 API：服务端角色归属/可见性/启用/参数白名单 + 真实插件入口。"""
import asyncio
import pathlib
import sys

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from sqlalchemy import text
from app.auth.config import create_token

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "http://127.0.0.1:8080"


@pytest.fixture(scope="module")
def mod():
    from app.plugins import plugin_store, registry
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    return registry._loaded["garden_forum"]["module"]


class FakeGF:
    calls = []
    def __init__(self, base, key="", name="", timeout=0):
        self.base = base
    def state(self, limit=10):
        FakeGF.calls.append("state")
        return {"replies_to_me": [{"id": 11, "post_id": 7, "body": "合成留言"}],
                "unread_dm": [], "unanswered": [], "circles": []}
    def say(self, act, **kw):
        FakeGF.calls.append(act)
        return {"ok": True}


def _doc():
    return {"base_url": BASE, "partner_key": "PK_SYNTHETIC_ONLY",
            "bound": {"97003": {"api_key": "AK_SYNTHETIC_ONLY", "name": "合成小叶",
                            "user_id": 9701, "base_url": BASE}},
            "one_shot": {}, "pending": {}, "pending_tombstones": {}, "posts": {}, "usage": {},
            "_credential_meta": {"ok": True, "crypto": True, "decrypt_failed": 0, "plaintext": []}}


def _cfg():
    return {"garden_forum_on": True, "garden_forum_allow_llm": True,
            "garden_forum_allow_write": True, "garden_forum_url": BASE,
            "public_profiles": {"97003": {"name": "合成小叶"}},
            "garden_forum_max_writes_per_beat": 4, "garden_forum_max_writes_per_day": 20,
            "garden_forum_max_generations_per_beat": 1,
            "garden_forum_max_generations_per_day": 6, "garden_forum_daily_post": 0}


async def _seed(u=9701, c=9711, s=9721):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        await db.execute(text("INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (:u,:n,'n','x')"), {"u": u, "n": f"u{u}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
            "is_active,cognitive_loop_enabled,memory_v2_enabled) VALUES (:c,:u,'c',0,0,1,0,0)"),
            {"c": c, "u": u})
        await db.commit()


def _client_token(uid=9701, token=None):
    from app.api import plugins as plugins_api
    app = FastAPI()
    app.include_router(plugins_api.router)
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return TestClient(app), headers



def _client(uid=1):
    from app.api import plugins as plugins_api
    from app.auth.deps import get_current_user_id
    app = FastAPI()
    app.include_router(plugins_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: uid
    return TestClient(app)


@pytest.fixture()
def wired(mod, monkeypatch):
    from app.plugins import registry
    FakeGF.calls = []
    doc = _doc()
    cfg = _cfg()
    registry._enabled["garden_forum"] = True
    registry._db_config["garden_forum"] = cfg
    monkeypatch.setattr(mod, "GardenForum", FakeGF)
    monkeypatch.setattr(mod, "store", mod.store)  # keep module object; patch funcs below
    monkeypatch.setattr(mod.store, "load", lambda: doc)
    monkeypatch.setattr(mod.store, "save", lambda d: True)
    monkeypatch.setattr(mod.sdk, "get_config", lambda: cfg)
    mod._host_guard.clear()
    mod._lock = asyncio.Lock()
    async def fake_chat(**kw):
        return "一句合成回复"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    asyncio.run(_seed(9701, 97003))
    return mod, doc, cfg, registry


def test_api_success_and_requires_server_identity(wired, monkeypatch):
    mod, doc, cfg, registry = wired
    body = {"character_id": 97003, "task_id": "api-task-1"}
    r = _client(9701).post("/api/v1/plugins/garden_forum/oneshot", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    assert FakeGF.calls == ["state", "act.comment"]
    assert doc["one_shot"]["api-task-1"]["state"] == "success"


def test_api_rejects_extra_override_fields(wired):
    mod, doc, cfg, registry = wired
    r = _client(9701).post("/api/v1/plugins/garden_forum/oneshot",
                        json={"character_id": 97003, "task_id": "t", "cfg": {}, "doc": {}})
    assert r.status_code == 400
    assert FakeGF.calls == []


def test_api_wrong_owner_and_disabled(wired):
    mod, doc, cfg, registry = wired
    asyncio.run(_seed(9702, 9712))
    r = _client(9701).post("/api/v1/plugins/garden_forum/oneshot",
                        json={"character_id": 9712, "task_id": "t"})
    assert r.status_code == 403
    registry._enabled["garden_forum"] = False
    try:
        r2 = _client(9701).post("/api/v1/plugins/garden_forum/oneshot",
                             json={"character_id": 97003, "task_id": "t2"})
    finally:
        registry._enabled["garden_forum"] = True
    assert r2.status_code == 409
    assert FakeGF.calls == []


def test_api_state_save_failure_returns_rejected_no_model(wired, monkeypatch):
    mod, doc, cfg, registry = wired
    monkeypatch.setattr(mod.store, "save", lambda d: False)
    r = _client(9701).post("/api/v1/plugins/garden_forum/oneshot",
                        json={"character_id": 97003, "task_id": "api-save-fail"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["code"] == "intent_save_failed"
    assert FakeGF.calls == []




def test_api_real_token_auth_and_duplicate_task(wired):
    mod, doc, cfg, registry = wired
    client, headers = _client_token(9701, create_token(9701))
    r = client.post("/api/v1/plugins/garden_forum/oneshot", headers=headers,
                    json={"character_id": 97003, "task_id": "api-real-token"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    before = list(FakeGF.calls)
    r2 = client.post("/api/v1/plugins/garden_forum/oneshot", headers=headers,
                     json={"character_id": 97003, "task_id": "api-real-token"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "rejected"
    assert r2.json()["code"] == "duplicate_task"
    assert FakeGF.calls == before


def test_api_missing_and_invalid_token(wired):
    mod, doc, cfg, registry = wired
    client, _ = _client_token(9701)
    r = client.post("/api/v1/plugins/garden_forum/oneshot",
                    json={"character_id": 97003, "task_id": "no-token"})
    assert r.status_code == 401
    r2 = client.post("/api/v1/plugins/garden_forum/oneshot",
                     headers={"Authorization": "Bearer bad.token.here"},
                     json={"character_id": 97003, "task_id": "bad-token"})
    assert r2.status_code == 401
    assert FakeGF.calls == []


def test_api_other_account_forbidden(wired):
    mod, doc, cfg, registry = wired
    asyncio.run(_seed(9702, 9712))
    client, headers = _client_token(9702, create_token(9702))
    r = client.post("/api/v1/plugins/garden_forum/oneshot", headers=headers,
                    json={"character_id": 97003, "task_id": "cross-account"})
    assert r.status_code == 403
    assert FakeGF.calls == []


def test_api_concurrent_same_task_sends_once(wired):
    from concurrent.futures import ThreadPoolExecutor
    mod, doc, cfg, registry = wired

    def call():
        return _client(9701).post("/api/v1/plugins/garden_forum/oneshot",
                                  json={"character_id": 97003, "task_id": "api-concurrent"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        rs = [f.result() for f in [pool.submit(call), pool.submit(call)]]
    assert sorted(r.status_code for r in rs) == [200, 200]
    states = sorted(r.json()["status"] for r in rs)
    assert states == ["rejected", "success"], [r.json() for r in rs]
    assert len([x for x in FakeGF.calls if x == "act.comment"]) == 1


def test_api_plugin_invisible_404_no_state(wired, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    from app.plugins import registry
    mod, doc, cfg, registry2 = wired
    monkeypatch.setitem(AGENT_FLAGS, "plugin_user_scope", True)
    old = registry._db_prov.get("garden_forum")
    registry._db_prov["garden_forum"] = {"source": "user", "owner_user_id": 999,
                                         "owner_tenant_id": 999}
    try:
        client, headers = _client_token(9701, create_token(9701))
        r = client.post("/api/v1/plugins/garden_forum/oneshot", headers=headers,
                        json={"character_id": 97003, "task_id": "invisible"})
    finally:
        if old is None:
            registry._db_prov.pop("garden_forum", None)
        else:
            registry._db_prov["garden_forum"] = old
    assert r.status_code == 404
    assert FakeGF.calls == []
