# -*- coding: utf-8 -*-
"""只读浏览：身份/租户/manual/站点全通过才 GET feed；零写动作、零模型、零状态保存。"""
import asyncio
import pathlib
import sys

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "https://garden.example.invalid"


def _doc(**over):
    d = {"base_url": BASE, "partner_key": "PK_SYNTHETIC_ONLY", "manual_only": True,
         "bound": {"13": {"api_key": "AK_SYNTHETIC_SAM", "name": "Sam", "user_id": 7,
                          "base_url": BASE, "remote_id": "sam"}},
         "_credential_meta": {"ok": True, "crypto": True, "decrypt_failed": 0,
                              "plaintext": []}}
    d.update(over)
    return d


class FakeGF:
    calls = []
    feed_items = [{"id": 1, "title": "合成标题", "body": "合成正文",
                   "author_display": "合成作者", "circle": "花园", "created_at": "2026-10-29"}]
    whoami_chars = [{"character_id": "sam", "agent": "sam-agent",
                     "account_exists": True, "retired": False}]
    me_agent = "sam-agent"

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base
        self.key = key

    def whoami(self, pk):
        FakeGF.calls.append("whoami")
        return {"characters": list(FakeGF.whoami_chars)}

    def me(self):
        FakeGF.calls.append("me")
        return {"agent": {"name": FakeGF.me_agent}}

    def feed(self, circle=None, limit=10, offset=0):
        FakeGF.calls.append("feed")
        return {"items": list(FakeGF.feed_items), "total": len(FakeGF.feed_items)}

    def post_detail(self, *a, **k):
        FakeGF.calls.append("post_detail")
        raise AssertionError("只读入口不得读详情（会写 views）")

    def say(self, *a, **k):
        FakeGF.calls.append("say")
        raise AssertionError("只读入口不得写动作")


@pytest.fixture()
def mod(monkeypatch):
    from app.plugins import plugin_store, registry
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    m = registry._loaded["garden_forum"]["module"]
    FakeGF.calls = []
    FakeGF.feed_items = [{"id": 1, "title": "合成标题", "body": "合成正文",
                          "author_display": "合成作者", "circle": "花园",
                          "created_at": "2026-10-29"}]
    FakeGF.whoami_chars = [{"character_id": "sam", "agent": "sam-agent",
                            "account_exists": True, "retired": False}]
    FakeGF.me_agent = "sam-agent"
    doc = _doc()
    saved = []
    monkeypatch.setattr(m, "GardenForum", FakeGF)
    monkeypatch.setattr(m.store, "load", lambda: doc)
    monkeypatch.setattr(m.store, "save", lambda d, *a, **k: saved.append(d) or True)
    monkeypatch.setattr(m.sdk, "get_config", lambda: {"garden_forum_url": BASE})
    m._saved = saved
    return m, doc


def _run(m, owner=7):
    return asyncio.run(m.run_readonly_browse(13, owner_user_id=owner))


def test_success_only_get_feed_and_no_write(mod):
    m, doc = mod
    r = _run(m)
    assert r["ok"] is True and r["mode"] == "readonly"
    assert r["remote_id"] == "sam"
    assert r["items"][0]["title"] == "合成标题"
    assert FakeGF.calls == ["whoami", "me", "feed"]
    assert m._saved == []
    assert doc["bound"]["13"]["api_key"] == "AK_SYNTHETIC_SAM"


@pytest.mark.parametrize("value", [False, "true", None, 1, [], {}])
def test_invalid_or_off_manual_is_rejected(mod, value):
    m, doc = mod
    doc["manual_only"] = value
    assert _run(m)["code"] == "not_manual_pilot"
    assert FakeGF.calls == [] and m._saved == []


def test_no_remote_id_legacy_or_unbound_rejected(mod):
    m, doc = mod
    doc["bound"]["13"].pop("remote_id")
    assert _run(m)["code"] == "missing_remote_id"
    doc["bound"] = {}
    assert _run(m)["code"] == "character_not_bound"


def test_other_tenant_or_site_rejected(mod):
    m, doc = mod
    assert _run(m, owner=8)["code"] == "character_not_owned_or_site_mismatch"
    doc["bound"]["13"]["base_url"] = "https://other.invalid"
    assert _run(m)["code"] == "character_not_owned_or_site_mismatch"


def test_credential_unusable_rejected_before_network(mod):
    m, doc = mod
    doc["_credential_meta"]["ok"] = False
    assert _run(m)["code"] == "credential_unusable"
    assert FakeGF.calls == []


def test_wrong_remote_agent_key_rejected(mod):
    m, doc = mod
    FakeGF.me_agent = "other-agent"
    assert _run(m)["code"] == "agent_key_mismatch"
    assert FakeGF.calls == ["whoami", "me"]
    assert m._saved == []


def test_remote_missing_or_retired_rejected(mod):
    m, doc = mod
    FakeGF.whoami_chars = [{"character_id": "sam", "agent": "sam-agent",
                            "account_exists": False, "retired": False}]
    assert _run(m)["code"] == "remote_identity_missing"
    FakeGF.whoami_chars = []
    assert _run(m)["code"] == "remote_identity_missing"


def test_remote_error_is_code_not_write(mod):
    m, doc = mod
    class Bad(FakeGF):
        def feed(self, *a, **k):
            from garden_forum_sdk import GardenError
            raise GardenError("timeout", "synthetic timeout")
    m.GardenForum = Bad
    r = _run(m)
    assert r["ok"] is False and r["code"] == "timeout"
    assert m._saved == []


def test_feed_sanitized_and_truncated(mod):
    m, doc = mod
    FakeGF.feed_items = [
        {"id": 1, "title": "T" * 500, "body": "B" * 2000,
         "author_display": "A" * 200, "circle": "C" * 200,
         "created_at": "D" * 100, "views": 999, "author": "internal-name"},
        "not-a-dict",
    ]
    r = _run(m)
    assert r["ok"] is True
    item = r["items"][0]
    assert len(item["title"]) == 200 and len(item["body"]) == 400
    assert "views" not in item and "author" not in item
    assert len(r["items"]) == 1


def test_duplicate_and_invalid_identity_rejected_before_me_feed(mod):
    m, doc = mod
    cases = [
        [{"character_id": "sam", "agent": "a"}, {"character_id": "sam", "agent": "a"}],
        [{"character_id": "sam", "agent": "a"}, {"character_id": "sam", "agent": "b"}],
        [{"character_id": "sam", "agent": "a"}, "bad-entry"],
        [{"character_id": "sam", "agent": "a"}, {"agent": "missing-id"}],
    ]
    for chars in cases:
        FakeGF.calls = []
        FakeGF.whoami_chars = chars
        r = _run(m)
        assert r["ok"] is False
        assert r["code"] in ("remote_identity_ambiguous", "remote_identity_invalid")
        assert FakeGF.calls == ["whoami"]


def test_config_status_site_mismatch_or_missing(mod, monkeypatch):
    m, doc = mod
    monkeypatch.setattr(m.sdk, "get_config", lambda: {"garden_forum_url": "https://other.invalid"})
    assert _run(m)["code"] == "site_mismatch"
    monkeypatch.setattr(m.sdk, "get_config", lambda: {})
    assert _run(m)["code"] == "site_config_missing"


def test_feed_invalid_ids_and_nested_objects_are_dropped(mod):
    m, doc = mod
    FakeGF.feed_items = [
        {"id": {"secret": "SYNTHETIC_SECRET"}, "title": "x"},
        {"id": True, "title": "bool"},
        {"id": 0, "title": "zero"},
        {"id": -1, "title": "neg"},
        {"id": 2 ** 63, "title": "huge"},
        {"id": 7, "title": {"nested": "SYNTHETIC_SECRET"},
         "body": ["array"], "author_display": 123, "circle": {"x": 1}},
    ]
    r = _run(m)
    assert r["ok"] is True
    assert r["count"] == 1 and r["skipped_invalid"] == 5
    item = r["items"][0]
    assert item["id"] == 7
    assert item["title"] == "" and item["body"] == "" and item["author_display"] == "" and item["circle"] == ""
    assert "SYNTHETIC_SECRET" not in str(r)


def test_feed_entry_cap_and_bad_response(mod):
    m, doc = mod
    FakeGF.feed_items = [{"id": i, "title": "t"} for i in range(1, 26)]
    r = _run(m)
    assert r["ok"] is True and len(r["items"]) == 20
    class Bad(FakeGF):
        def feed(self, *a, **k):
            return {"items": {"not": "list"}}
    m.GardenForum = Bad
    assert _run(m)["code"] == "bad_feed_response"


def test_network_runs_off_event_loop(mod):
    import asyncio, time
    m, doc = mod
    class Slow(FakeGF):
        def feed(self, *a, **k):
            time.sleep(0.2)
            return {"items": [{"id": 1, "title": "slow"}]}
    m.GardenForum = Slow
    async def scenario():
        ticks = 0
        async def ticker():
            nonlocal ticks
            for _ in range(10):
                await asyncio.sleep(0.01)
                ticks += 1
        task = asyncio.create_task(ticker())
        r = await m.run_readonly_browse(13, owner_user_id=7)
        await task
        return r, ticks
    r, ticks = asyncio.run(scenario())
    assert r["ok"] is True and ticks >= 5


def test_remote_error_code_is_whitelisted(mod):
    m, doc = mod
    class Bad(FakeGF):
        def feed(self, *a, **k):
            from garden_forum_sdk import GardenError
            raise GardenError("SYNTHETIC_REMOTE_TEXT", "remote private text")
    m.GardenForum = Bad
    r = _run(m)
    assert r["code"] == "read_failed"
    assert "SYNTHETIC_REMOTE_TEXT" not in str(r) and "private" not in str(r)


def test_account_exists_must_be_exact_true(mod):
    m, doc = mod
    for bad in (None, False, 0, 1, "true", [], {}):
        FakeGF.calls = []
        FakeGF.whoami_chars = [{"character_id": "sam", "agent": "sam-agent",
                                "retired": False, "account_exists": bad}]
        r = _run(m)
        assert r["ok"] is False
        assert FakeGF.calls == ["whoami"], bad
    # missing field also fail closed
    FakeGF.calls = []
    FakeGF.whoami_chars = [{"character_id": "sam", "agent": "sam-agent", "retired": False}]
    assert _run(m)["ok"] is False and FakeGF.calls == ["whoami"]


def test_retired_missing_is_compatible_but_bad_values_reject(mod):
    m, doc = mod
    FakeGF.whoami_chars = [{"character_id": "sam", "agent": "sam-agent",
                            "account_exists": True}]
    assert _run(m)["ok"] is True  # 正常省略 retired 不误拦
    for bad in ("false", 2, -1, 0.0, 1.0, None, [], {}):
        FakeGF.calls = []
        FakeGF.whoami_chars = [{"character_id": "sam", "agent": "sam-agent",
                                "account_exists": True, "retired": bad}]
        r = _run(m)
        assert r["ok"] is False and r["code"] == "remote_identity_invalid"
        assert FakeGF.calls == ["whoami"]
    for retired in (False, 0):
        FakeGF.whoami_chars = [{"character_id": "sam", "agent": "sam-agent",
                                "account_exists": True, "retired": retired}]
        assert _run(m)["ok"] is True
    for retired in (True, 1):
        FakeGF.whoami_chars = [{"character_id": "sam", "agent": "sam-agent",
                                "account_exists": True, "retired": retired}]
        assert _run(m)["code"] == "remote_identity_missing"


def test_identity_agents_must_be_nonempty_strings(mod):
    m, doc = mod
    for bad in (None, {}, [], 1, "", " has-space"):
        FakeGF.calls = []
        FakeGF.whoami_chars = [{"character_id": "sam", "agent": bad,
                                "account_exists": True}]
        r = _run(m)
        assert r["ok"] is False
        assert r["code"] == "remote_agent_unverifiable"
        assert FakeGF.calls == ["whoami"]
    # /api/me 的 agent.name 错型：先通过 whoami，再拒绝，不 feed
    FakeGF.whoami_chars = [{"character_id": "sam", "agent": "sam-agent",
                            "account_exists": True}]
    for bad in (None, {}, [], 1, ""):
        FakeGF.calls = []
        FakeGF.me_agent = bad
        r = _run(m)
        assert r["ok"] is False
        assert FakeGF.calls == ["whoami", "me"]


def test_identity_response_shapes_and_character_id_types(mod):
    m, doc = mod
    for bad_me in (None, [], "agent", {"agent": "sam-agent"}):
        m.GardenForum = lambda *a, _b=bad_me, **k: type("GF", (), {
            "whoami": lambda self, pk: {"characters": [{"character_id": "sam", "agent": "sam-agent",
                                                         "account_exists": True}]},
            "me": lambda self: _b,
        })()
        assert _run(m)["ok"] is False
    assert m._identity_id(13) == "13"
    for bad in (True, False, 0, -1, 2 ** 63, {}, [], 1.5, "", " a", "a "):
        assert m._identity_id(bad) is None


@pytest.mark.parametrize("payload", [["bad"], "bad", None, 7])
def test_feed_non_object_root_returns_stable_error(mod, monkeypatch, payload):
    m, doc = mod
    monkeypatch.setattr(FakeGF, "feed", lambda self, *args: payload)
    result = _run(m)
    assert result == {"ok": False, "code": "bad_feed_response"}
    assert m._saved == []
