# -*- coding: utf-8 -*-
"""真实 assemble_context 入口：个人 user_binding 独立授权、注入前复核与配置代际。"""
import pathlib
import sys
import time

import pytest

from test_garden_observation_assembly import _assemble

pytest_plugins = ["test_garden_observation_assembly"]

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "https://garden.example.invalid"


class FakeGF:
    mode = "ok"
    calls = 0

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

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
            "user_binding_epoch": 1, "observations": {},
            "user_bindings": {"1:13": {
                "base_url": BASE, "role_id": "role-a", "character_id": "13",
                "remote_character_id": "sam", "agent": "a", "user_key": "UK_ASM",
                "scope": "readonly", "active": True, "bound_at": now,
                "last_verified_at": now, "binding_epoch": 1,
                "observation": {"base_url": BASE, "role_id": "role-a",
                                "read_at": now, "expires_at": now + 600,
                                "items": [{"id": 8, "title": "个人装配标题",
                                           "body": "正文", "author_display": "作者",
                                           "circle": "花园", "created_at": "2026-10-29"}]}}}}


@pytest.fixture()
def wired_user(asm_db, monkeypatch):
    from app.plugins import plugin_store, registry
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
    return asm_db, mod, doc, cfg


def _find_user(msgs):
    return [m for m in msgs if "个人装配标题" in str(m.get("content") or "")]


def test_real_assembly_user_binding_injects(wired_user):
    asm_mod, mod, doc, cfg = wired_user
    msgs = _assemble(asm_mod, doc, cfg)
    got = _find_user(msgs)
    assert len(got) == 1 and got[0]["role"] == "user"
    assert FakeGF.calls == 1


def test_real_assembly_revoked_role_wrong_and_switch_off(wired_user):
    asm_mod, mod, doc, cfg = wired_user
    FakeGF.mode = "revoked"
    assert _find_user(_assemble(asm_mod, doc, cfg)) == []
    FakeGF.mode = "wrong_role"
    assert _find_user(_assemble(asm_mod, doc, cfg)) == []
    FakeGF.mode = "ok"
    cfg["garden_forum_observe"] = False
    assert _find_user(_assemble(asm_mod, doc, cfg)) == []
    cfg["garden_forum_observe"] = True
    cfg["garden_forum_on"] = False
    assert _find_user(_assemble(asm_mod, doc, cfg)) == []


def test_real_assembly_unbind_rebind_expired(wired_user):
    asm_mod, mod, doc, cfg = wired_user
    doc["user_bindings"]["1:13"]["active"] = False
    assert _find_user(_assemble(asm_mod, doc, cfg)) == []
    doc["user_bindings"]["1:13"]["active"] = True
    doc["user_bindings"]["1:13"]["observation"]["expires_at"] = int(time.time()) - 1
    assert _find_user(_assemble(asm_mod, doc, cfg)) == []
