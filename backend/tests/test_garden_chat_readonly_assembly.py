# -*- coding: utf-8 -*-
"""Actual assembly: explicit garden request gets ToolRunner result before model input."""
import asyncio

from test_garden_portable_user_assembly import wired_user  # noqa: E402,F401

pytest_plugins = ["test_garden_observation_assembly"]

import pytest


@pytest.fixture(autouse=True)
def _role_db(asm_db, monkeypatch):
    import app.agent.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_garden_session_factory", asm_db.async_session_factory)

from app.agent.context_builder import _trim_limits


class LiveAssembly:
    calls = []

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

    def user_whoami(self, user_key):
        LiveAssembly.calls.append("whoami")
        return {"ok": True, "active": True, "scope": "readonly",
                "role_id": "role-a", "character_id": "sam", "agent": "a"}

    def user_feed(self, user_key, circle=None, limit=20, offset=0):
        LiveAssembly.calls.append("feed")
        return {"ok": True, "items": [{
            "id": 9, "title": "装配实时标题", "body": "装配实时正文",
            "author": "装配作者", "circle": "花园", "created_at": "2026-10-31",
        }], "scope": "readonly"}


def _assemble(asm_mod, message, stream=None):
    state = {
        "user_message": message, "character_id": 13, "user_id": 1, "session_id": 1,
        "intent": "", "retrieved_memories": [], "context_messages": [],
        "character_info": {}, "ai_response": "", "should_update_memory": False,
        "new_memories": [], "emotional_state": "", "bio_update": None,
        "status_update": None, "lang": "zh",
    }
    return asyncio.run(asm_mod.assemble_context(
        state, stream=stream, _section_values={"relationship": ""},
        _trim=_trim_limits(True)))["context_messages"]


def test_real_assembly_explicit_request_injects_tool_result(wired_user):  # noqa: F811
    asm_mod, mod, doc, cfg = wired_user
    mod.GardenForum = LiveAssembly
    LiveAssembly.calls = []
    text = "\n".join(str(m.get("content") or "") for m in _assemble(asm_mod, "去花园看看"))
    assert "装配实时标题" in text and "装配作者" in text
    assert LiveAssembly.calls.count("feed") == 1


def test_real_assembly_stream_parameter_injects_tool_result(wired_user):  # noqa: F811
    asm_mod, mod, doc, cfg = wired_user
    mod.GardenForum = LiveAssembly
    LiveAssembly.calls = []
    text = "\n".join(str(m.get("content") or "") for m in _assemble(asm_mod, "去花园看看", stream=True))
    assert "装配实时标题" in text
    assert LiveAssembly.calls.count("feed") == 1


def test_real_assembly_plain_chat_does_not_call(wired_user):  # noqa: F811
    asm_mod, mod, doc, cfg = wired_user
    mod.GardenForum = LiveAssembly
    LiveAssembly.calls = []
    _assemble(asm_mod, "今天有点累")
    assert LiveAssembly.calls.count("feed") == 0


def test_hook_not_dispatched_still_gives_failure_to_model(wired_user, monkeypatch):  # noqa: F811
    asm_mod, mod, doc, cfg = wired_user
    from app.plugins import registry
    monkeypatch.setattr(registry, "get_plugin", lambda _n: {"enabled": False})
    async def _no_hook(*_a, **_k):
        return None
    monkeypatch.setattr(registry, "run_hook", _no_hook)
    text = "\n".join(str(m.get("content") or "") for m in _assemble(asm_mod, "去花园看看"))
    assert "花园只读失败" in text and "未分发" in text


def test_db_family_denies_cross_family_before_network(wired_user):  # noqa: F811
    asm_mod, mod, doc, cfg = wired_user
    mod.GardenForum = LiveAssembly
    LiveAssembly.calls = []
    from app.plugins import registry
    msgs = []
    import asyncio as _asyncio
    _asyncio.run(registry.run_hook("context_inject", {
        "user_id": 2, "character_id": 13, "context_messages": msgs,
        "session_id": "synthetic", "user_message": "去花园看看",
    }, user_id=2))
    text = "\n".join(str(m.get("content") or "") for m in msgs)
    assert "角色不属于" in text or "读取失败" in text
    assert LiveAssembly.calls == []


def test_real_assembly_large_history_keeps_result_or_explicit_failure(wired_user):  # noqa: F811
    asm_mod, mod, doc, cfg = wired_user
    mod.GardenForum = LiveAssembly
    LiveAssembly.calls = []
    state = {
        "user_message": "去花园看看", "character_id": 13, "user_id": 1, "session_id": 1,
        "intent": "", "retrieved_memories": [], "context_messages": [{"role": "user", "content": "x" * 100000}],
        "character_info": {}, "ai_response": "", "should_update_memory": False,
        "new_memories": [], "emotional_state": "", "bio_update": None,
        "status_update": None, "lang": "zh",
    }
    msgs = asyncio.run(asm_mod.assemble_context(
        state, _section_values={"relationship": ""}, _trim=_trim_limits(True)))["context_messages"]
    text = "\n".join(str(m.get("content") or "") for m in msgs)
    assert ("装配实时标题" in text) or ("上下文预算不足" in text)


def test_real_db_role_transfer_during_read_discards_result(wired_user):  # noqa: F811
    import sqlite3
    import app.agent.tools as tools_mod
    asm_mod, mod, doc, cfg = wired_user
    base = cfg.get("garden_forum_url") or "http://127.0.0.1:8080"
    doc.setdefault("bound", {})["13"] = {
        "api_key": "AK_SYNTHETIC", "name": "Sam", "user_id": 1,
        "base_url": base, "remote_id": "sam",
    }
    doc["base_url"] = base
    cfg["garden_forum_on"] = True
    cfg["garden_forum_inject"] = True
    old_sf = tools_mod._garden_session_factory
    bind = getattr(asm_mod.async_session_factory, "kw", {}).get("bind")
    db_path = bind.url.database if bind is not None else None
    assert db_path
    tools_mod._garden_session_factory = asm_mod.async_session_factory
    def _set_owner(owner):
        con = sqlite3.connect(db_path)
        try:
            con.execute("UPDATE ai_characters SET user_id=? WHERE id=13", (owner,))
            con.commit()
        finally:
            con.close()
    _set_owner(1)
    class Transfer(LiveAssembly):
        def feed(self, circle=None, limit=20, offset=0):
            _set_owner(2)
            return super().user_feed("", circle, limit, offset)
        def user_feed(self, user_key, circle=None, limit=20, offset=0):
            return self.feed(circle, limit, offset)
    mod.GardenForum = Transfer
    Transfer.calls = []
    from app.plugins import registry
    msgs = []
    try:
        asyncio.run(registry.run_hook("context_inject", {
            "user_id": 1, "character_id": 13, "context_messages": msgs,
            "session_id": "synthetic", "user_message": "去花园看看",
        }, user_id=1))
    finally:
        tools_mod._garden_session_factory = old_sf
        _set_owner(1)
    text = "\n".join(str(m.get("content") or "") for m in msgs)
    assert "角色归属变化" in text
    assert "装配实时标题" not in text


def test_final_model_request_gets_result_or_explicit_failure(wired_user):  # noqa: F811
    asm_mod, mod, doc, cfg = wired_user
    mod.GardenForum = LiveAssembly
    LiveAssembly.calls = []
    state = {
        "user_message": "去花园看看", "character_id": 13, "user_id": 1, "session_id": 1,
        "intent": "", "retrieved_memories": [], "context_messages": [{"role": "user", "content": "x" * 100000}],
        "character_info": {}, "ai_response": "", "should_update_memory": False,
        "new_memories": [], "emotional_state": "", "bio_update": None,
        "status_update": None, "lang": "zh",
    }
    msgs = asyncio.run(asm_mod.assemble_context(
        state, _section_values={"relationship": ""}, _trim=_trim_limits(True)))["context_messages"]
    # 受控模型只依据实际最终消息回答，不硬编码成功。
    text = "\n".join(str(m.get("content") or "") for m in msgs)
    answer = "我看到了装配实时标题" if "装配实时标题" in text else "我没有看到花园内容"
    if "上下文预算不足" in text:
        assert "没有看到" in answer
    else:
        assert "装配实时标题" in text
