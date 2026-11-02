# -*- coding: utf-8 -*-
"""受限 user key API：绑定/状态/解绑/只读/观察；租户归属。"""
import asyncio
import pathlib
import sys

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from sqlalchemy import text

from app.plugins import registry as _registry_snapshot

_REAL_RUN_HOOK = _registry_snapshot.run_hook

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "http://127.0.0.1:8080"
U, C = 98401, 98403


async def _seed():
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        await db.execute(text("INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (:u,'garden_portable_98401','n','x')"), {"u": U})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
            "is_active,cognitive_loop_enabled,memory_v2_enabled) VALUES (:c,:u,'c',0,0,1,0,0)"),
            {"c": C, "u": U})
        await db.commit()


def _client(uid=U):
    from app.api import plugins as plugins_api
    from app.auth.deps import get_current_user_id
    app = FastAPI(); app.include_router(plugins_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: uid
    return TestClient(app)


def _asm_messages():
    from app.agent.context.assembly import assemble_context
    from app.agent.context_builder import _trim_limits
    state = {"user_message": "hi", "character_id": C, "user_id": U, "session_id": 1,
             "intent": "", "retrieved_memories": [], "context_messages": [],
             "character_info": {}, "ai_response": "", "should_update_memory": False,
             "new_memories": [], "emotional_state": "", "bio_update": None,
             "status_update": None, "lang": "zh"}
    return asyncio.run(assemble_context(state, _section_values={"relationship": ""},
                                        _trim=_trim_limits(True)))["context_messages"]


class FakeGF:
    def __init__(self, base, key="", name="", timeout=0): self.base = base
    def user_whoami(self, user_key):
        return {"ok": True, "active": True, "scope": "readonly", "role_id": "role-api",
                "character_id": "sam", "agent": "sam-agent"}
    def user_feed(self, user_key, circle=None, limit=20, offset=0):
        return {"ok": True, "items": [{"id": 7, "title": "API标题", "body": "正文",
                                       "author_display": "作者", "circle": "花园",
                                       "created_at": "2026-10-29"}]}


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    from app.plugins import plugin_store, registry
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    mod = registry._loaded["garden_forum"]["module"]
    # 可逆隔离：monkeypatch 保存原值/缺失状态并在用例后恢复；不直接清空全局。
    monkeypatch.setattr(mod, "_OBSERVE_EPOCH_DEGRADED", False)
    monkeypatch.setitem(registry._enabled, "garden_forum", True)
    # builtin 正例；普通用户安装的 owner 正反例见 test_runtime_scope_owner_*。
    monkeypatch.setitem(registry._db_prov, "garden_forum", {"source": "builtin"})
    monkeypatch.setitem(registry._db_config, "garden_forum", {
        "garden_forum_url": BASE, "garden_forum_observe": True,
        "garden_forum_on": True, "garden_forum_inject": True})
    # 只替换本次 hook 分发会读的缓存；fixture 退出由 monkeypatch 恢复原对象。
    monkeypatch.setattr(registry, "_visible_names_cache", {})
    monkeypatch.setattr(registry, "_caller_tenant_cache", {})
    monkeypatch.setattr(registry, "_warned_no_caller", set())
    # 有些既有测试会 monkeypatch registry.run_hook；即使恢复泄漏也不让装配链拿到假函数。
    monkeypatch.setattr(registry, "run_hook", _REAL_RUN_HOOK)
    # Align the cached assembly factory with this test session.
    from app.agent.context import assembly as _assembly
    from app.db import database as _database
    monkeypatch.setattr(_assembly, "async_session_factory", _database.async_session_factory)
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    monkeypatch.setattr(mod.store, "STATE_PATH", tmp_path / "state.json")
    assert mod.store.save({"bound": {}, "user_bindings": {}, "manual_only": True})
    monkeypatch.setattr(mod, "GardenForum", FakeGF)
    monkeypatch.setattr(mod.sdk, "get_config", lambda: {"garden_forum_url": BASE,
                                                        "garden_forum_observe": True,
                                                        "garden_forum_on": True,
                                                        "garden_forum_inject": True})
    asyncio.run(_seed())
    yield mod, registry


def test_api_bind_status_unbind(wired):
    mod, registry = wired
    c = _client()
    body = {"character_id": C, "user_key": "UK_API", "base_url": BASE}
    r = c.post("/api/v1/plugins/garden_forum/account", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["role_id"] == "role-api"
    assert "UK_API" not in r.text
    r = c.get("/api/v1/plugins/garden_forum/account", params={"character_id": C})
    assert r.status_code == 200 and r.json()["key_present"] is True
    assert c.request("DELETE", "/api/v1/plugins/garden_forum/account",
                     json={"character_id": C}).status_code == 200
    assert c.get("/api/v1/plugins/garden_forum/account",
                 params={"character_id": C}).json()["bound"] is False


def test_api_readonly_and_observation(wired):
    mod, registry = wired
    c = _client()
    assert c.post("/api/v1/plugins/garden_forum/account",
                  json={"character_id": C, "user_key": "UK_API", "base_url": BASE}).status_code == 200
    r = c.post("/api/v1/plugins/garden_forum/user/readonly",
               json={"character_id": C, "observe": True})
    assert r.status_code == 200, r.text
    assert r.json()["observed"] is True
    r = c.get("/api/v1/plugins/garden_forum/user/observation", params={"character_id": C})
    assert r.status_code == 200 and r.json()["observed"] is True
    assert c.request("DELETE", "/api/v1/plugins/garden_forum/user/observation",
                     json={"character_id": C}).status_code == 200
    assert c.get("/api/v1/plugins/garden_forum/user/observation",
                 params={"character_id": C}).json()["observed"] is False


def test_api_cross_tenant_rejected(wired):
    mod, registry = wired
    c = _client(99999)
    assert c.post("/api/v1/plugins/garden_forum/account",
                  json={"character_id": C, "user_key": "UK_API", "base_url": BASE}).status_code == 403


def test_first_install_missing_state_initializes_encrypted(wired):
    mod, registry = wired
    state = mod.store.STATE_PATH
    state.unlink(missing_ok=True)
    c = _client()
    r = c.post("/api/v1/plugins/garden_forum/account",
               json={"character_id": C, "user_key": "UK_FIRST", "base_url": BASE})
    assert r.status_code == 200, r.text
    raw = state.read_text(encoding="utf-8")
    assert "UK_FIRST" not in raw and "user_key" in raw
    doc = mod.store.load()
    assert doc["user_bindings"][f"{U}:{C}"]["user_key"] == "UK_FIRST"
    assert doc["manual_only"] is True
    r = c.post("/api/v1/plugins/garden_forum/user/readonly",
               json={"character_id": C, "observe": True})
    assert r.status_code == 200 and r.json()["observed"] is True
    assert mod.store.load()["user_bindings"][f"{U}:{C}"]["observation"]["items"]


def test_corrupt_state_not_treated_as_first_install(wired):
    mod, registry = wired
    state = mod.store.STATE_PATH
    state.write_text("{corrupt", encoding="utf-8")
    before = state.read_bytes()
    c = _client()
    r = c.post("/api/v1/plugins/garden_forum/account",
               json={"character_id": C, "user_key": "UK_CORRUPT", "base_url": BASE})
    assert r.status_code == 400 and r.json()["detail"] == "credential_unusable"
    assert state.read_bytes() == before


def test_first_install_end_to_end_assembly(wired):
    mod, registry = wired
    state_path = mod.store.STATE_PATH
    state_path.unlink(missing_ok=True)
    c = _client()
    assert c.post("/api/v1/plugins/garden_forum/account",
                  json={"character_id": C, "user_key": "UK_E2E", "base_url": BASE}).status_code == 200
    assert c.post("/api/v1/plugins/garden_forum/user/readonly",
                  json={"character_id": C, "observe": True}).status_code == 200
    got = [m for m in _asm_messages() if "API标题" in str(m.get("content") or "")]
    assert len(got) == 1 and got[0]["role"] == "user"
    assert c.request("DELETE", "/api/v1/plugins/garden_forum/user/observation",
                     json={"character_id": C}).status_code == 200
    assert [m for m in _asm_messages() if "API标题" in str(m.get("content") or "")] == []


def test_runtime_scope_owner_positive_and_negative(monkeypatch):
    """真实 owner 用户路径：本家庭安装可见；别的租户不可见（不靠强设 builtin）。"""
    from app.agent.loop import AGENT_FLAGS
    from app.plugins import registry
    monkeypatch.setitem(AGENT_FLAGS, "plugin_runtime_scope", True)
    monkeypatch.setitem(registry._db_prov, "garden_forum", {
        "source": "local", "owner_user_id": U, "owner_tenant_id": U})
    monkeypatch.setattr(registry, "_visible_names_cache", {})
    monkeypatch.setattr(registry, "_caller_tenant_cache", {})
    allowed, _ = asyncio.run(registry._resolve_hook_scope(
        "context_inject", user_id=U, tenant_id=U, callsite="test"))
    assert allowed is not None and "garden_forum" in allowed
    allowed2, _ = asyncio.run(registry._resolve_hook_scope(
        "context_inject", user_id=99999, tenant_id=88888, callsite="test"))
    assert allowed2 is not None and "garden_forum" not in allowed2


def test_scope_pollution_negative_then_restored_positive(wired, monkeypatch):
    """最小顺序污染回放：其他测试把 garden_forum 标成 remote/他租户后 hook 不可见；
    恢复 builtin 后同一 API→绑定→观察→装配链路恢复注入。"""
    mod, registry = wired
    state_path = mod.store.STATE_PATH
    state_path.unlink(missing_ok=True)
    c = _client()
    assert c.post("/api/v1/plugins/garden_forum/account",
                  json={"character_id": C, "user_key": "UK_ORDER", "base_url": BASE}).status_code == 200
    assert c.post("/api/v1/plugins/garden_forum/user/readonly",
                  json={"character_id": C, "observe": True}).status_code == 200
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "plugin_runtime_scope", True)
    monkeypatch.setitem(registry._db_prov, "garden_forum", {
        "source": "local", "owner_user_id": 99999, "owner_tenant_id": 88888})
    monkeypatch.setattr(registry, "_visible_names_cache", {})
    monkeypatch.setattr(registry, "_caller_tenant_cache", {})
    polluted = [m for m in _asm_messages() if "API标题" in str(m.get("content") or "")]
    assert polluted == []  # 顺序污染触发的真实负例
    # 恢复（模拟 fixture 的可逆隔离）
    monkeypatch.setitem(registry._db_prov, "garden_forum", {"source": "builtin"})
    monkeypatch.setattr(registry, "_visible_names_cache", {})
    restored = [m for m in _asm_messages() if "API标题" in str(m.get("content") or "")]
    assert len(restored) == 1 and restored[0]["role"] == "user"
