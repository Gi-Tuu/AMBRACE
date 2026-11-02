# -*- coding: utf-8 -*-
"""Sam existing partner binding -> ToolRunner garden_read -> bounded result (synthetic)."""
import asyncio

from test_garden_observation_context import wired  # noqa: E402,F401



class LegacySam:
    calls = []

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

    def whoami(self, partner_key):
        LegacySam.calls.append("partner-whoami")
        return {"ok": True, "characters": [{
            "character_id": "sam", "agent": "sam-agent",
            "account_exists": True, "retired": False,
        }]}

    def me(self):
        return {"agent": {"name": "sam-agent"}}

    def feed(self, circle=None, limit=20, offset=0):
        LegacySam.calls.append("feed")
        return {"items": [{
            "id": 8, "title": "旧绑定标题", "body": "旧绑定正文",
            "author": "旧作者", "circle": "花园", "created_at": "2026-10-31",
        }]}


import pytest
from _dbclone import clone_engine, make_session_factory


@pytest.fixture(autouse=True)
def _role_db(tmp_path, monkeypatch):
    import asyncio as _asyncio
    import app.agent.tools as tools_mod
    from app.plugins import registry as _reg
    engine = clone_engine((tmp_path / "sam_role.db").as_posix())
    factory = make_session_factory(engine)
    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=7, username="sam_u7", nickname="u7"))
            db.add(AICharacter(id=13, user_id=7, name="Sam", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()
    _asyncio.run(_seed())
    monkeypatch.setattr(tools_mod, "_garden_session_factory", factory)
    from app.plugins import registry as _reg2
    _reg2._enabled["garden_forum"] = True
    async def _scope(*_a, **_k):
        return (None, None)
    monkeypatch.setattr(_reg, "_resolve_hook_scope", _scope)
    yield
    _asyncio.run(engine.dispose())


def test_sam_legacy_binding_live_read_uses_existing_binding(wired):  # noqa: F811
    mod, registry, doc, cfg, calls = wired
    mod.GardenForum = LegacySam
    LegacySam.calls = []
    cfg["garden_forum_observe"] = False
    registry._db_config["garden_forum"] = dict(cfg)
    state = {"context_messages": []}
    asyncio.run(registry.run_hook("context_inject", {
        "user_id": 7, "character_id": 13, "context_messages": state["context_messages"],
        "session_id": "synthetic", "user_message": "去花园看看",
    }, user_id=7))
    assert len(state["context_messages"]) == 1
    assert "旧绑定标题" in state["context_messages"][0]["content"]
    assert LegacySam.calls == ["partner-whoami", "feed"]
    assert doc.get("bound", {}).get("13") is not None
