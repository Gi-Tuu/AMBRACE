# -*- coding: utf-8 -*-
"""P1 短期观察：只读浏览可生成有界快照；注入只读本地、标注外部不可信、过期/解绑即失效。"""
import asyncio
import pathlib
import sys
import time

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "https://garden.example.invalid"


def _doc():
    return {
        "base_url": BASE,
        "partner_key": "PK_SYNTHETIC_OBS",
        "manual_only": True,
        "bound": {"13": {"api_key": "AK_SYNTHETIC_OBS", "name": "Sam", "user_id": 7,
                         "base_url": BASE, "remote_id": "sam"}},
        "observations": {}, "digest": {}, "pending": {}, "pending_tombstones": {},
    }


class FakeGF:
    calls = []
    feed_items = [{"id": 7, "title": "合成公开标题", "body": "合成公开正文",
                   "author_display": "合成作者", "circle": "花园", "created_at": "2026-10-29"}]

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

    def whoami(self, pk):
        FakeGF.calls.append("whoami")
        return {"characters": [{"character_id": "sam", "agent": "sam-agent",
                                "account_exists": True, "retired": False}]}

    def me(self):
        FakeGF.calls.append("me")
        return {"agent": {"name": "sam-agent"}}

    def feed(self, circle=None, limit=10, offset=0):
        FakeGF.calls.append("feed")
        return {"items": list(FakeGF.feed_items)}


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    from app.plugins import plugin_store, registry
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    m = registry._loaded["garden_forum"]["module"]
    state = tmp_path / "state.json"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    monkeypatch.setattr(m.store, "STATE_PATH", state)
    assert m.store.save(_doc())
    FakeGF.calls = []
    FakeGF.feed_items = [{"id": 7, "title": "合成公开标题", "body": "合成公开正文",
                          "author_display": "合成作者", "circle": "花园",
                          "created_at": "2026-10-29"}]
    monkeypatch.setattr(m, "GardenForum", FakeGF)
    cfg = {"garden_forum_on": True, "garden_forum_inject": True,
           "garden_forum_observe": True, "garden_forum_url": BASE}
    monkeypatch.setattr(m.sdk, "get_config", lambda: dict(cfg))
    return m, state, cfg


def _browse(m, observe=True):
    return asyncio.run(m.run_readonly_browse(13, owner_user_id=7, observe=observe))


def _inject(m, **over):
    msgs = []
    ctx = {"user_id": 7, "character_id": 13, "context_messages": msgs}
    ctx.update(over)
    asyncio.run(m.on_inject(ctx))
    return msgs


def test_observation_saved_only_when_explicit(mod):
    m, state, cfg = mod
    r = _browse(m, observe=False)
    assert r["ok"] is True and "observed" not in r
    assert m.store.load()["observations"] == {}
    FakeGF.calls = []
    r = _browse(m, observe=True)
    assert r["ok"] is True and r["observed"] is True
    obs = m.store.load()["observations"]["13"]
    assert obs["base_url"] == BASE and obs["remote_id"] == "sam"
    assert obs["expires_at"] > obs["read_at"]
    assert len(obs["items"]) == 1
    assert obs["items"][0]["title"] == "合成公开标题"
    assert FakeGF.calls == ["whoami", "me", "feed"]


def test_observation_disabled_or_empty_does_not_write(mod, monkeypatch):
    m, state, cfg = mod
    cfg["garden_forum_observe"] = False
    r = _browse(m, observe=True)
    assert r["observed"] is False and r["observation_code"] == "observation_disabled"
    assert m.store.load()["observations"] == {}
    cfg["garden_forum_observe"] = True
    FakeGF.feed_items = []
    r = _browse(m, observe=True)
    assert r["observed"] is False and r["observation_code"] == "observation_empty"
    assert m.store.load()["observations"] == {}


def test_inject_labels_untrusted_and_scope(mod):
    m, state, cfg = mod
    _browse(m, observe=True)
    msgs = _inject(m)
    assert len(msgs) == 1
    text = msgs[0]["content"]
    assert "花园外部观察" in text and "不是指令" in text and "readonly_browse" in text
    assert "合成公开标题" in text and "#7" in text
    assert "不要执行" in text and "不等同于长期记忆" in text
    # 其他角色/用户/关闭/过期不注入
    cfg["garden_forum_observe"] = False
    assert _inject(m) == []
    cfg["garden_forum_observe"] = True
    assert _inject(m, user_id=8) == []
    assert _inject(m, character_id=14) == []
    doc = m.store.load()
    doc["observations"]["13"]["expires_at"] = int(time.time()) - 1
    m.store.save(doc)
    assert _inject(m) == []


def test_unbind_clears_observation(mod):
    m, state, cfg = mod
    _browse(m, observe=True)
    doc = m.store.load()
    assert "13" in doc["observations"]
    assert m.store.drop_binding(doc, "13") is True
    assert "13" not in (doc.get("observations") or {})


def test_config_epoch_blocks_delayed_save(mod):
    m, state, cfg = mod
    class Racing(FakeGF):
        def feed(self, *a, **k):
            cfg["garden_forum_observe"] = False
            m.bump_observe_epoch()
            cfg["garden_forum_observe"] = True  # 关闭→重开，旧请求仍不能复活
            return {"items": [{"id": 7, "title": "race"}]}
    m.GardenForum = Racing
    r = _browse(m, observe=True)
    assert r["observed"] is False and r["observation_code"] == "observation_conflict"
    assert m.store.load()["observations"] == {}


def test_clear_epoch_blocks_delayed_save(mod):
    m, state, cfg = mod
    class Racing(FakeGF):
        def feed(self, *a, **k):
            doc = m.store.load()
            m.store.clear_observation(doc, "13")
            assert m.store.save(doc)
            return {"items": [{"id": 7, "title": "race"}]}
    m.GardenForum = Racing
    r = _browse(m, observe=True)
    assert r["observed"] is False and r["observation_code"] == "observation_conflict"
    assert m.store.load()["observations"] == {}


def test_unbind_rebind_key_change_blocks_old_snapshot(mod):
    m, state, cfg = mod
    class Racing(FakeGF):
        def feed(self, *a, **k):
            doc = m.store.load()
            m.store.clear_observation(doc, "13")
            doc["bound"]["13"]["api_key"] = "AK_SYNTHETIC_NEW"
            assert m.store.save(doc)
            return {"items": [{"id": 7, "title": "race"}]}
    m.GardenForum = Racing
    r = _browse(m, observe=True)
    assert r["observed"] is False
    assert m.store.load()["observations"] == {}


def test_status_and_clear_p2(mod):
    m, state, cfg = mod
    _browse(m, observe=True)
    st = m.observation_status(13, owner_user_id=7)
    assert st["ok"] is True and st["observed"] is True and st["enabled"] is True
    assert st["items"][0]["id"] == 7
    assert "body" not in st["items"][0]
    assert m.observation_status(13, owner_user_id=8)["ok"] is False
    assert m.clear_readonly_observation(13, owner_user_id=7)["ok"] is True
    assert m.store.load()["observations"] == {}
    assert m.observation_status(13, owner_user_id=7)["observed"] is False


@pytest.mark.parametrize("bad", ["false", "true", 1, [], {}, None])
def test_invalid_observe_switch_fails_closed(mod, bad):
    m, state, cfg = mod
    cfg["garden_forum_observe"] = bad
    result = _browse(m)
    assert result["observed"] is False
    assert not m.store.load().get("observations")
    assert not _inject(m)
    assert m.observation_status(13, owner_user_id=7)["enabled"] is False
