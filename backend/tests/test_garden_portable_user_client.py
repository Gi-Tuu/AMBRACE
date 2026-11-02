# -*- coding: utf-8 -*-
"""受限 user key 绑定：两用户/跨宿主同名/唯一/旧 Sam 保全/加密/撤销/观察。"""
import asyncio
import pathlib
import sys

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "http://127.0.0.1:8080"
BASE2 = "http://localhost:8082"


class FakeGF:
    calls = []
    keys = {}

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base
        self.key = key

    def user_whoami(self, user_key):
        FakeGF.calls.append(("whoami", self.base))
        if user_key == "bad":
            from garden_forum_sdk import GardenError
            raise GardenError("invalid_user_key", "invalid")
        if user_key == "revoked":
            from garden_forum_sdk import GardenError
            raise GardenError("user_key_revoked", "revoked")
        row = FakeGF.keys.get((self.base, user_key))
        if not row:
            from garden_forum_sdk import GardenError
            raise GardenError("invalid_user_key", "invalid")
        return {"ok": True, "active": True, "scope": "readonly",
                **row}

    feed_hook = None

    def user_feed(self, user_key, circle=None, limit=20, offset=0):
        FakeGF.calls.append(("feed", self.base))
        if FakeGF.feed_hook is not None:
            FakeGF.feed_hook()
        return {"ok": True, "items": [{"id": 7, "title": "合成标题",
                                       "body": "合成正文", "author_display": "作者",
                                       "circle": "花园", "created_at": "2026-10-29"}],
                "total": 1, "scope": "readonly"}


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    from app.plugins import plugin_store, registry
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    m = registry._loaded["garden_forum"]["module"]
    state = tmp_path / "state.json"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    monkeypatch.setattr(m.store, "STATE_PATH", state)
    legacy = {"base_url": BASE,
              "bound": {"13": {"api_key": "AK_LEGACY_SAM", "name": "Sam", "user_id": 1,
                               "base_url": BASE}},
              "user_bindings": {}, "manual_only": True}
    assert m.store.save(legacy)
    FakeGF.calls = []
    FakeGF.keys = {
        (BASE, "UK_A"): {"role_id": "role-a", "character_id": "sam", "agent": "a-agent"},
        (BASE, "UK_B"): {"role_id": "role-b", "character_id": "sam", "agent": "b-agent"},
        (BASE2, "UK_C"): {"role_id": "role-a", "character_id": "sam", "agent": "c-agent"},
        (BASE, "UK_E"): {"role_id": "role-c", "character_id": "other", "agent": "e-agent"},
    }
    monkeypatch.setattr(m, "GardenForum", FakeGF)
    monkeypatch.setattr(m.sdk, "get_config", lambda: {"garden_forum_url": BASE,
                                                      "garden_forum_allowed_urls": [BASE, BASE2],
                                                      "garden_forum_on": True,
                                                      "garden_forum_inject": True,
                                                      "garden_forum_observe": True})
    return m, state


def _bind(m, uid=1, cid=13, key="UK_A", base=BASE):
    return asyncio.run(m.bind_user_account(cid, uid, key, base))


def test_bind_encrypts_key_and_preserves_legacy_sam(mod):
    m, state = mod
    r = _bind(m)
    assert r["ok"] is True and r["role_id"] == "role-a"
    raw = state.read_text(encoding="utf-8")
    assert "UK_A" not in raw and "AK_LEGACY_SAM" not in raw
    doc = m.store.load()
    assert doc["user_bindings"]["1:13"]["user_key"] == "UK_A"
    assert doc["bound"]["13"]["name"] == "Sam"  # 旧 Sam 原样保留
    assert m.user_account_status(1, 13)["key_present"] is True
    assert "UK_A" not in repr(m.user_account_status(1, 13))


def test_two_users_and_cross_host_same_character(mod):
    m, state = mod
    assert _bind(m, 1, 13, "UK_A", BASE)["ok"] is True
    assert _bind(m, 2, 14, "UK_B", BASE)["ok"] is True
    # 不同宿主、同一 remote character/role 不冲突
    assert _bind(m, 3, 15, "UK_C", BASE2)["ok"] is True
    doc = m.store.load()
    assert doc["user_bindings"]["1:13"]["role_id"] == "role-a"
    assert doc["user_bindings"]["2:14"]["role_id"] == "role-b"
    assert doc["user_bindings"]["3:15"]["role_id"] == "role-a"


def test_same_role_same_host_other_user_conflicts(mod):
    m, state = mod
    assert _bind(m, 1, 13, "UK_A", BASE)["ok"] is True
    # 两个用户都用 role-a（伪造另一个 key 映射）→ 拒绝
    FakeGF.keys[(BASE, "UK_D")] = {"role_id": "role-a", "character_id": "other", "agent": "d"}
    r = _bind(m, 2, 14, "UK_D", BASE)
    assert r["ok"] is False and r["code"] == "binding_conflict"


def test_bad_revoked_missing_key_rejected(mod):
    m, state = mod
    assert _bind(m, 1, 13, "bad")["code"] == "user_key_invalid"
    assert _bind(m, 1, 13, "revoked")["code"] == "user_key_revoked"
    assert _bind(m, 1, 13, "missing")["code"] == "user_key_invalid"


def test_user_readonly_observe_and_isolation(mod):
    m, state = mod
    assert _bind(m, 1, 13, "UK_A")["ok"] is True
    assert _bind(m, 2, 14, "UK_B")["ok"] is True
    r = asyncio.run(m.run_user_readonly(1, 13, observe=True))
    assert r["ok"] is True and r["observed"] is True
    doc = m.store.load()
    assert doc["user_bindings"]["1:13"]["observation"]["items"][0]["id"] == 7
    assert doc["user_bindings"]["2:14"].get("observation") is None
    assert m.user_observation_status(1, 13)["observed"] is True
    assert m.user_observation_status(2, 14)["observed"] is False
    assert m.clear_user_observation(1, 13)["ok"] is True
    assert m.user_observation_status(1, 13)["observed"] is False


def test_unbind_keeps_other_binding_and_legacy(mod):
    m, state = mod
    assert _bind(m, 1, 13, "UK_A")["ok"] is True
    assert _bind(m, 2, 14, "UK_B")["ok"] is True
    assert m.unbind_user_account(1)["ok"] is True
    doc = m.store.load()
    assert "1:13" not in doc["user_bindings"] and "2:14" in doc["user_bindings"]
    assert "13" in doc["bound"]


def test_crypto_unavailable_and_decrypt_failure_fail_closed(mod, monkeypatch):
    m, state = mod
    before = state.read_bytes()
    monkeypatch.setattr(m.store, "_crypto", lambda: None)
    assert _bind(m)["code"] == "crypto_unavailable"
    assert state.read_bytes() == before
    # 恢复加密，但换主密钥文件：旧密文不可解 -> 拒绝，不覆盖
    import app.utils.credential_crypto as cc
    monkeypatch.setattr(m.store, "_crypto", lambda: cc)
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(state.parent / "other.key"))
    assert _bind(m)["code"] == "credential_unusable"
    assert state.read_bytes() == before


def test_same_user_multiple_characters_are_preserved(mod):
    m, state = mod
    assert _bind(m, 1, 13, "UK_A")["ok"] is True
    assert _bind(m, 1, 14, "UK_E")["ok"] is True
    doc = m.store.load()
    assert "1:13" in doc["user_bindings"] and "1:14" in doc["user_bindings"]
    assert m.user_account_status(1, 13)["role_id"] == "role-a"
    assert m.user_account_status(1, 14)["role_id"] == "role-c"
    assert m.unbind_user_account(1, 13)["ok"] is True
    doc = m.store.load()
    assert "1:13" not in doc["user_bindings"] and "1:14" in doc["user_bindings"]


def test_readonly_race_rebind_same_role_different_key_rejects(mod):
    m, state = mod
    assert _bind(m, 1, 13, "UK_A")["ok"] is True

    def race():
        def apply(doc):
            m.store.put_user_binding(doc, 1, base_url=BASE, role_id="role-a",
                                     character_id=13, remote_character_id="sam",
                                     agent="new", user_key="UK_E")
            return doc
        m.store.update_state(apply)
    FakeGF.feed_hook = race
    try:
        r = asyncio.run(m.run_user_readonly(1, 13, observe=True))
    finally:
        FakeGF.feed_hook = None
    assert r["ok"] is True and r["observed"] is False
    assert r["observation_code"] == "observation_conflict"
    doc = m.store.load()
    assert doc["user_bindings"]["1:13"]["user_key"] == "UK_E"


def test_cross_origin_redirect_does_not_leak_user_key(tmp_path):
    import http.server
    import threading
    from garden_forum_sdk import GardenError, GardenForum

    seen = []
    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("X-User-Key"))
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a): pass
    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:%d/api/user/whoami" % target_port)
            self.end_headers()
        def log_message(self, *a): pass
    ts = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Target)
    target_port = ts.server_address[1]
    rs = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    redirect_port = rs.server_address[1]
    threading.Thread(target=ts.serve_forever, daemon=True).start()
    threading.Thread(target=rs.serve_forever, daemon=True).start()
    try:
        gf = GardenForum("http://127.0.0.1:%d" % redirect_port, "")
        try:
            gf.user_whoami("UK_REDIRECT_SECRET")
        except GardenError:
            pass
        assert seen == []
    finally:
        rs.shutdown(); ts.shutdown()


def test_observe_disabled_does_not_create_observation(mod):
    m, state = mod
    assert _bind(m, 1, 13, "UK_A")["ok"] is True
    m.sdk.get_config = lambda: {"garden_forum_url": BASE, "garden_forum_allowed_urls": [BASE],
                                "garden_forum_on": True, "garden_forum_inject": True,
                                "garden_forum_observe": False}
    r = asyncio.run(m.run_user_readonly(1, 13, observe=True))
    assert r["ok"] is True and r["items"]
    assert r["observed"] is False and r["observation_code"] == "observation_disabled"
    assert m.store.load()["user_bindings"]["1:13"]["observation"] is None


def test_observe_turned_off_during_feed_does_not_save(mod):
    m, state = mod
    assert _bind(m, 1, 13, "UK_A")["ok"] is True
    cfg = {"garden_forum_url": BASE, "garden_forum_allowed_urls": [BASE],
           "garden_forum_on": True, "garden_forum_inject": True,
           "garden_forum_observe": True}
    m.sdk.get_config = lambda: dict(cfg)
    def off():
        cfg["garden_forum_observe"] = False
        m.bump_observe_epoch()
    FakeGF.feed_hook = off
    try:
        r = asyncio.run(m.run_user_readonly(1, 13, observe=True))
    finally:
        FakeGF.feed_hook = None
    assert r["ok"] is True and r["observed"] is False
    assert r["observation_code"] == "observation_conflict"
    assert m.store.load()["user_bindings"]["1:13"]["observation"] is None


def test_bump_persists_observe_epoch(mod):
    m, state = mod
    assert _bind(m, 1, 13, "UK_A")["ok"] is True
    before = int(m.store.load().get("observe_epoch") or 0)
    m.bump_observe_epoch()
    assert int(m.store.load().get("observe_epoch") or 0) == before + 1


def test_bump_persist_failure_disables_observe(mod, monkeypatch):
    m, state = mod
    assert _bind(m, 1, 13, "UK_A")["ok"] is True
    monkeypatch.setattr(m.store, "update_state", lambda fn, path=None: (False, None))
    try:
        assert m.bump_observe_epoch() is False
        r = asyncio.run(m.run_user_readonly(1, 13, observe=True))
        assert r["ok"] is True and r["observed"] is False
        assert r["observation_code"] == "observation_disabled"
        assert m.store.load()["user_bindings"]["1:13"]["observation"] is None
    finally:
        m._OBSERVE_EPOCH_DEGRADED = False
