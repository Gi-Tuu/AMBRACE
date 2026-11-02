# -*- coding: utf-8 -*-
"""真实 context_inject 分发：个人绑定独立授权 + 注入前 whoami 验证/代际。"""
import asyncio
import pathlib
import sys
import time

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "http://127.0.0.1:8080"


class FakeGF:
    mode = "ok"
    calls = 0

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

    @staticmethod
    def _seed_db():
        async def _seed():
            from sqlalchemy import text
            from app.db.database import async_session_factory
            async with async_session_factory() as db:
                await db.execute(text("INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (7,'garden_u7','u7','x')"))
                await db.execute(text("INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_active) VALUES (13,7,'c13',1)"))
                await db.commit()
        import asyncio as _asyncio
        _asyncio.run(_seed())

    def user_whoami(self, user_key):
        FakeGF.calls += 1
        if FakeGF.mode == "revoked":
            from garden_forum_sdk import GardenError
            raise GardenError("user_key_revoked", "revoked")
        if FakeGF.mode == "wrong_role":
            return {"ok": True, "active": True, "scope": "readonly", "role_id": "other"}
        return {"ok": True, "active": True, "scope": "readonly", "role_id": "role-a"}


def _doc():
    now = int(time.time())
    return {"base_url": BASE, "bound": {}, "digest": {}, "manual_only": True,
            "user_binding_epoch": 1,
            "user_bindings": {"7:13": {
                "base_url": BASE, "role_id": "role-a", "character_id": "13",
                "remote_character_id": "sam", "agent": "a", "user_key": "UK_A",
                "scope": "readonly", "active": True, "bound_at": now,
                "last_verified_at": now, "binding_epoch": 1,
                "observation": {"base_url": BASE, "role_id": "role-a",
                                "read_at": now, "expires_at": now + 600,
                                "items": [{"id": 7, "title": "个人标题", "body": "正文",
                                           "author_display": "作者", "circle": "花园",
                                           "created_at": "2026-10-29"}]}}},
            "user_observations": {}}


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    from app.plugins import plugin_store, registry
    from _dbclone import clone_engine, make_session_factory
    import app.agent.tools as tools_mod
    engine = clone_engine((tmp_path / "role.db").as_posix())
    factory = make_session_factory(engine)
    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=7, username="garden_u7", nickname="u7"))
            db.add(AICharacter(id=13, user_id=7, name="Sam", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()
    import asyncio as _asyncio
    _asyncio.run(_seed())
    monkeypatch.setattr(tools_mod, "_garden_session_factory", factory)
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    mod = registry._loaded["garden_forum"]["module"]
    registry._enabled["garden_forum"] = True
    doc = _doc()
    monkeypatch.setattr(mod.store, "load", lambda: doc)
    cfg = {"garden_forum_on": True, "garden_forum_inject": True,
           "garden_forum_observe": True, "garden_forum_url": BASE,
           "garden_forum_allowed_urls": [BASE]}
    monkeypatch.setattr(mod.sdk, "get_config", lambda: dict(cfg))
    monkeypatch.setattr(mod, "GardenForum", FakeGF)
    FakeGF.mode, FakeGF.calls = "ok", 0
    yield mod, registry, doc, cfg
    _asyncio.run(engine.dispose())


def _hook(registry, uid=7, cid=13):
    msgs = []
    asyncio.run(registry.run_hook("context_inject", {
        "user_id": uid, "character_id": cid, "context_messages": msgs,
        "session_id": "synthetic", "user_message": "hi",
    }, user_id=uid))
    return msgs


def test_user_only_binding_injects_after_live_verify(wired):
    mod, registry, doc, cfg = wired
    msgs = _hook(registry)
    assert len(msgs) == 1 and msgs[0]["role"] == "user"
    assert "个人标题" in msgs[0]["content"]
    assert FakeGF.calls == 1


def test_revoked_or_wrong_role_no_inject(wired):
    mod, registry, doc, cfg = wired
    FakeGF.mode = "revoked"
    assert _hook(registry) == []
    FakeGF.mode = "wrong_role"
    assert _hook(registry) == []


def test_non_owner_wrong_character_disabled_expired_no_inject(wired):
    mod, registry, doc, cfg = wired
    assert _hook(registry, uid=8) == []
    assert _hook(registry, cid=14) == []
    cfg["garden_forum_observe"] = False
    assert _hook(registry) == []
    cfg["garden_forum_observe"] = True
    doc["user_bindings"]["7:13"]["observation"]["expires_at"] = int(time.time()) - 1
    assert _hook(registry) == []


def test_rebind_during_verify_rejects_old_observation(wired):
    mod, registry, doc, cfg = wired
    # 验证阶段模拟同用户换 key/代际：用 side effect 改 doc，hook 重新 load 后应拒绝
    old = FakeGF.user_whoami
    def mutate(self, user_key):
        r = old(self, user_key)
        doc["user_bindings"]["7:13"]["binding_epoch"] = 2
        doc["user_bindings"]["7:13"]["user_key"] = "UK_NEW"
        return r
    FakeGF.user_whoami = mutate
    try:
        assert _hook(registry) == []
    finally:
        FakeGF.user_whoami = old


def test_truthy_values_do_not_enable_and_config_epoch_off_on(wired):
    mod, registry, doc, cfg = wired
    cfg["garden_forum_observe"] = "true"
    assert _hook(registry) == []
    cfg["garden_forum_observe"] = 1
    assert _hook(registry) == []
    cfg["garden_forum_observe"] = True
    old = FakeGF.user_whoami
    def bump(self, key):
        r = old(self, key)
        mod._OBSERVE_EPOCH = int(getattr(mod, "_OBSERVE_EPOCH", 0)) + 1
        return r
    FakeGF.user_whoami = bump
    try:
        assert _hook(registry) == []
    finally:
        FakeGF.user_whoami = old


def test_changes_during_whoami_no_inject(wired):
    mod, registry, doc, cfg = wired
    old = FakeGF.user_whoami

    def run_with(mut):
        def wrapped(self, key):
            r = old(self, key)
            mut()
            return r
        FakeGF.user_whoami = wrapped
        try:
            return _hook(registry)
        finally:
            FakeGF.user_whoami = old

    assert run_with(lambda: cfg.__setitem__("garden_forum_on", False)) == []
    cfg["garden_forum_on"] = True
    assert run_with(lambda: cfg.__setitem__("garden_forum_inject", False)) == []
    cfg["garden_forum_inject"] = True
    assert run_with(lambda: doc.__setitem__("manual_only", False)) == []
    doc["manual_only"] = True
    assert run_with(lambda: cfg.__setitem__("garden_forum_allowed_urls", [])) == []
    cfg["garden_forum_allowed_urls"] = [BASE]
    assert run_with(lambda: setattr(mod, "_OBSERVE_EPOCH", int(getattr(mod, "_OBSERVE_EPOCH", 0)) + 1)) == []


def test_inject_compares_persistent_observe_epoch_during_whoami(wired):
    mod, registry, doc, cfg = wired
    old = FakeGF.user_whoami

    def bump_state(self, key):
        r = old(self, key)
        doc["observe_epoch"] = int(doc.get("observe_epoch") or 0) + 1
        return r
    FakeGF.user_whoami = bump_state
    try:
        assert _hook(registry) == []
    finally:
        FakeGF.user_whoami = old
