# -*- coding: utf-8 -*-
"""Real isolated DB: caller/family/role ownership, transfer during read, tool entry."""
import asyncio
import pathlib
import sqlite3

import pytest

from _dbclone import clone_engine, make_session_factory
import app.agent.tools as tools_mod

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
BASE = "https://garden.example.invalid"


def _doc():
    return {"base_url": BASE, "partner_key": "PK_SYNTHETIC", "bound": {"13": {
        "api_key": "AK_SYNTHETIC", "name": "Sam", "user_id": 1,
        "base_url": BASE, "remote_id": "sam",
    }}, "digest": {}, "manual_only": True, "observations": {}, "pending": {},
            "_credential_meta": {"ok": True, "crypto": True, "decrypt_failed": 0, "plaintext": []}}


class GF:
    calls = []

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

    def whoami(self, partner_key):
        GF.calls.append("whoami")
        return {"ok": True, "characters": [{
            "character_id": "sam", "agent": "sam-agent",
            "account_exists": True, "retired": False,
        }]}

    def me(self):
        return {"agent": {"name": "sam-agent"}}

    def feed(self, circle=None, limit=20, offset=0):
        GF.calls.append("feed")
        return {"items": [{
            "id": 7, "title": "DB真实标题", "body": "正文",
            "author": "作者", "circle": "花园", "created_at": "2026-10-31",
        }]}


@pytest.fixture()
def real_db(tmp_path, monkeypatch):
    db_file = tmp_path / "garden_role.db"
    engine = clone_engine(db_file.as_posix())
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="u1"))
            db.add(User(id=2, username="u2", nickname="u2"))
            db.add(AICharacter(id=13, user_id=1, name="Sam", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_seed())
    old = tools_mod._garden_session_factory
    tools_mod._garden_session_factory = factory
    from app.plugins import plugin_store, registry
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    mod = registry._loaded["garden_forum"]["module"]
    registry._enabled["garden_forum"] = True
    monkeypatch.setattr(mod.store, "load", lambda: _doc())
    cfg = {"garden_forum_on": True, "garden_forum_inject": True,
           "garden_forum_observe": False, "garden_forum_url": BASE,
           "garden_forum_allowed_urls": [BASE]}
    registry._db_config["garden_forum"] = dict(cfg)
    mod.GardenForum = GF
    GF.calls = []
    yield factory, db_file, mod, registry, cfg
    tools_mod._garden_session_factory = old
    asyncio.run(engine.dispose())


def _hook(registry, uid, cid=13):
    msgs = []
    asyncio.run(registry.run_hook("context_inject", {
        "user_id": uid, "character_id": cid, "context_messages": msgs,
        "session_id": "synthetic", "user_message": "去花园看看",
    }, user_id=uid))
    return msgs


def test_same_owner_can_read_real_db(real_db):
    _factory, _path, _mod, registry, _cfg = real_db
    msgs = _hook(registry, 1)
    text = "\n".join(str(m.get("content") or "") for m in msgs)
    assert "DB真实标题" in text
    assert GF.calls.count("feed") == 1


def test_cross_family_refused_before_network(real_db):
    _factory, _path, _mod, registry, _cfg = real_db
    msgs = _hook(registry, 2)
    text = "\n".join(str(m.get("content") or "") for m in msgs)
    assert "角色不属于" in text
    assert GF.calls == []


def test_real_db_transfer_during_read_discards_result(real_db):
    _factory, db_path, mod, registry, _cfg = real_db

    class Transfer(GF):
        def feed(self, circle=None, limit=20, offset=0):
            con = sqlite3.connect(db_path.as_posix())
            try:
                con.execute("UPDATE ai_characters SET user_id=2 WHERE id=13")
                con.commit()
            finally:
                con.close()
            return super().feed(circle, limit, offset)

    mod.GardenForum = Transfer
    GF.calls = []
    msgs = _hook(registry, 1)
    text = "\n".join(str(m.get("content") or "") for m in msgs)
    assert "角色归属变化" in text
    assert "DB真实标题" not in text
    assert GF.calls.count("feed") == 1


def test_absent_character_fails_closed_without_network(real_db):
    _factory, _path, _mod, registry, _cfg = real_db
    async def _delete():
        from app.models.character import AICharacter
        async with _factory() as db:
            ch = await db.get(AICharacter, 13)
            if ch is not None:
                await db.delete(ch)
                await db.commit()
    asyncio.run(_delete())
    msgs = _hook(registry, 1)
    text = "\n".join(str(m.get("content") or "") for m in msgs)
    assert "角色不属于" in text
    assert GF.calls == []


def test_character_deleted_during_read_discards_result(real_db):
    _factory, db_path, mod, registry, _cfg = real_db
    class Delete(GF):
        def feed(self, circle=None, limit=20, offset=0):
            con = sqlite3.connect(db_path.as_posix())
            try:
                con.execute("DELETE FROM ai_characters WHERE id=13")
                con.commit()
            finally:
                con.close()
            return super().feed(circle, limit, offset)
    mod.GardenForum = Delete
    GF.calls = []
    msgs = _hook(registry, 1)
    text = "\n".join(str(m.get("content") or "") for m in msgs)
    assert "角色归属变化" in text
    assert "DB真实标题" not in text
    assert GF.calls.count("feed") == 1
