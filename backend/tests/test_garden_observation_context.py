# -*- coding: utf-8 -*-
"""context_inject 真实注册分发路径：只读本地观察，不联网/不调模型。"""
import asyncio
import pathlib
import sys
import time

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "https://garden.example.invalid"


def _doc(expires=None):
    now = int(time.time())
    return {"base_url": BASE, "partner_key": "PK_SYNTHETIC_CTX", "manual_only": True,
            "bound": {"13": {"api_key": "AK_SYNTHETIC_CTX", "name": "Sam", "user_id": 7,
                             "base_url": BASE, "remote_id": "sam"}},
            "_credential_meta": {"ok": True, "crypto": True, "decrypt_failed": 0,
                                 "plaintext": []},
            "observations": {"13": {"base_url": BASE, "remote_id": "sam", "user_id": 7,
                                    "read_at": now, "expires_at": expires or now + 600,
                                    "source": "readonly_browse",
                                    "items": [{"id": 7, "title": "合成标题",
                                               "body": "合成正文", "author_display": "作者",
                                               "circle": "花园", "created_at": "2026-10-29"}]}},
            "digest": {}, "pending": {}, "pending_tombstones": {}}


@pytest.fixture()
def wired(monkeypatch):
    from app.plugins import plugin_store, registry
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    mod = registry._loaded["garden_forum"]["module"]
    doc = _doc()
    monkeypatch.setattr(mod.store, "load", lambda: doc)
    cfg = {"garden_forum_on": True, "garden_forum_inject": True,
           "garden_forum_observe": True, "garden_forum_url": BASE}
    monkeypatch.setattr(mod.sdk, "get_config", lambda: dict(cfg))
    calls = []
    class GF:
        def __init__(self, *a, **k): calls.append("init")
    monkeypatch.setattr(mod, "GardenForum", GF)
    return mod, registry, doc, cfg, calls


def _run_hook(registry):
    msgs = []
    asyncio.run(registry.run_hook("context_inject", {
        "user_id": 7, "character_id": 13, "context_messages": msgs,
        "session_id": "synthetic", "user_message": "hi",
    }, user_id=7))
    return msgs


def test_registered_context_inject_adds_external_block(wired):
    mod, registry, doc, cfg, calls = wired
    msgs = _run_hook(registry)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert "花园外部观察" in msgs[0]["content"]
    assert "合成标题" in msgs[0]["content"]
    assert calls == []


def test_disabled_or_expired_does_not_inject(wired):
    mod, registry, doc, cfg, calls = wired
    cfg["garden_forum_observe"] = False
    assert _run_hook(registry) == []
    cfg["garden_forum_observe"] = True
    doc["observations"]["13"]["expires_at"] = int(time.time()) - 1
    assert _run_hook(registry) == []
    assert calls == []
