# -*- coding: utf-8 -*-
"""M3 恢复边界：拒绝明文降级、原子替换、pending/unknown 不重发。"""
import asyncio
import pathlib
import sys

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))

import garden_policy as policy  # noqa: E402
import garden_state as store  # noqa: E402


def test_save_refuses_when_crypto_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    p = tmp_path / "s.json"
    p.write_text("ORIGINAL", encoding="utf-8")
    monkeypatch.setattr(store, "_crypto", lambda: None)
    assert store.save({"partner_key": "PK"}, p) is False
    assert p.read_text(encoding="utf-8") == "ORIGINAL"


def test_save_refuses_encryption_exception(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    p.write_text("ORIGINAL", encoding="utf-8")
    class Boom:
        def encrypt_json_document(self, doc, fields):
            raise RuntimeError("boom")
    monkeypatch.setattr(store, "_crypto", lambda: Boom())
    assert store.save({"partner_key": "PK"}, p) is False
    assert p.read_text(encoding="utf-8") == "ORIGINAL"


def test_save_refuses_plaintext_after_encrypt(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    p.write_text("ORIGINAL", encoding="utf-8")
    class Noop:
        def encrypt_json_document(self, doc, fields):
            return doc, 0
        def is_encrypted(self, value):
            return False
    monkeypatch.setattr(store, "_crypto", lambda: Noop())
    assert store.save({"partner_key": "PK"}, p) is False
    assert p.read_text(encoding="utf-8") == "ORIGINAL"


def test_save_replace_failure_keeps_original(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    p = tmp_path / "s.json"
    p.write_text("ORIGINAL", encoding="utf-8")
    monkeypatch.setattr(store.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("busy")))
    assert store.save({"partner_key": "PK"}, p) is False
    assert p.read_text(encoding="utf-8") == "ORIGINAL"


def test_save_success_has_no_plaintext(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    p = tmp_path / "s.json"
    partner = "PK_SYNTHETIC_MARKER_8f3c2d7a_never_on_disk"
    agent = "AK_SYNTHETIC_MARKER_5b1e9c4f_never_on_disk"
    # 多次随机加密：短子串曾碰巧出现在密文里，长标记 + 回读解密才稳定
    for _ in range(20):
        assert store.save({"partner_key": partner, "bound": {"3": {"api_key": agent}}}, p)
        raw = p.read_text(encoding="utf-8")
        assert partner not in raw and agent not in raw
        back = store.load(p)
        assert back["partner_key"] == partner
        assert back["bound"]["3"]["api_key"] == agent
        assert store.credential_usable(back)


def test_pending_state_machine_blocks_unknown():
    doc = {}
    pid = policy.pending_id("3", "comment", 11)
    assert policy.can_send(doc, pid)
    policy.transition(doc, pid, "inflight", now=1)
    assert not policy.can_send(doc, pid)
    policy.transition(doc, pid, "unknown", code="unreachable", now=2)
    assert not policy.can_send(doc, pid)
    policy.transition(doc, pid, "success", now=3)
    assert not policy.can_send(doc, pid)          # 成功收据不能成为同一动作的重放通行证
    assert policy.can_send(doc, policy.pending_id("3", "comment", 12))  # 新动作仍合法


def test_recover_inflight_to_unknown():
    doc = {"pending": {"a": {"state": "inflight"}, "b": {"state": "success"}}}
    assert policy.recover_inflight(doc) == 1
    assert doc["pending"]["a"]["state"] == "unknown"
    assert doc["pending"]["b"]["state"] == "success"


@pytest.fixture(scope="module")
def mod():
    from app.plugins import plugin_store, registry
    old = registry._loaded.get("garden_forum")
    old_enabled = registry._enabled.get("garden_forum")
    old_module = sys.modules.get("ai_plugin_garden_forum")
    old_path = list(sys.path)
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info
    module = registry._loaded["garden_forum"]["module"]
    yield module
    if old is None:
        registry._loaded.pop("garden_forum", None)
    else:
        registry._loaded["garden_forum"] = old
    if old_enabled is None:
        registry._enabled.pop("garden_forum", None)
    else:
        registry._enabled["garden_forum"] = old_enabled
    if old_module is None:
        sys.modules.pop("ai_plugin_garden_forum", None)
    else:
        sys.modules["ai_plugin_garden_forum"] = old_module
    sys.path[:] = old_path


def test_send_pending_save_failure_never_sends(mod, monkeypatch):
    calls = []
    doc = {}
    def send():
        calls.append(1)
        return {"ok": True}
    monkeypatch.setattr(mod.store, "save", lambda d: False)
    r = asyncio.run(mod._send_pending(None, doc, "3", "pid", "comment", 1.0, send))
    assert r["code"] == "pending_save_failed"
    assert calls == []


def test_send_pending_unknown_never_resends(mod, monkeypatch):
    calls = []
    doc = {"pending": {}}
    saved = []
    monkeypatch.setattr(mod.store, "save", lambda d: saved.append(dict(d.get("pending", {}))) or True)
    def send():
        calls.append(1)
        return {"ok": False, "code": "unreachable"}
    pid = policy.pending_id("3", "comment", 11)
    r1 = asyncio.run(mod._send_pending(None, doc, "3", pid, "comment", 1.0, send))
    assert r1["code"] == "unreachable"
    assert doc["pending"][pid]["state"] == "unknown"
    r2 = asyncio.run(mod._send_pending(None, doc, "3", pid, "comment", 2.0, send))
    assert r2["code"] == "pending_unknown"
    assert calls == [1]


def test_send_pending_success_marks_success(mod, monkeypatch):
    doc = {"pending": {}}
    monkeypatch.setattr(mod.store, "save", lambda d: True)
    def send():
        return {"ok": True}
    pid = policy.pending_id("3", "dm", "a")
    r = asyncio.run(mod._send_pending(None, doc, "3", pid, "dm", 1.0, send))
    assert r == {"ok": True}
    assert doc["pending"][pid]["state"] == "success"


def test_receipt_save_failure_is_not_success(mod, monkeypatch):
    doc = {"pending": {}}
    seq = [True, False]
    monkeypatch.setattr(mod.store, "save", lambda d: seq.pop(0) if seq else False)
    def send():
        return {"ok": True}
    pid = policy.pending_id("3", "comment", 12)
    r = asyncio.run(mod._send_pending(None, doc, "3", pid, "comment", 1.0, send))
    assert r["code"] == "receipt_save_failed"
    assert r["server_ok"] is True
    assert doc["pending"][pid]["state"] == "unknown"


def test_unbind_moves_unknown_to_tombstone_and_blocks_same_action():
    pid = policy.pending_id("3", "comment", "11", base_url="http://127.0.0.1:8080", source="11")
    doc = {"bound": {"3": {"api_key": "OLD", "user_id": 1,
                            "base_url": "http://127.0.0.1:8080"}},
           "pending": {pid: {"state": "unknown", "cid": "3",
                              "base_url": "http://127.0.0.1:8080", "source": "11"}},
           "pending_tombstones": {}, "digest": {"3": {"text": "private"}}}
    store.drop_binding(doc, "3")
    assert doc["pending"] == {}
    assert pid in doc["pending_tombstones"]
    store.put_binding(doc, "3", "NEW", "Synthetic", 1,
                      base_url="http://127.0.0.1:8080")
    assert not policy.can_send(doc, pid)
    assert policy.can_send(doc, policy.pending_id("3", "comment", "12",
                                                  base_url="http://127.0.0.1:8080",
                                                  source="12"))
