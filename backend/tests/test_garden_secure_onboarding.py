# -*- coding: utf-8 -*-
"""空状态安全初始化 → adopt 既有账号 → 只读核身份；不 register。"""
import pathlib
import sys

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "http://127.0.0.1:8080"
HTTPS = "https://garden.example.invalid"


class FakeAdmin:
    calls = []
    chars = [{"character_id": "sam", "account_exists": True, "agent": "sam-agent",
              "display": "Sam", "retired": False}]
    disabled = False

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

    def whoami(self, pk):
        FakeAdmin.calls.append(("whoami", pk))
        return {"partner": {"disabled": FakeAdmin.disabled, "used": 1, "remaining": 9},
                "characters": FakeAdmin.chars}

    def me(self):
        return {"agent": {"name": "sam-agent"}}

    def register(self, *a, **k):
        FakeAdmin.calls.append(("register", a))
        raise AssertionError("secure onboarding 不得调用 register")


@pytest.fixture()
def setup_env(tmp_path, monkeypatch):
    import garden_setup as setup
    import garden_state as state
    keyfile = tmp_path / "partner.key"
    keyfile.write_text("PK_SYNTHETIC_SECURE\n", encoding="utf-8")
    statefile = tmp_path / "state.json"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "secrets.key"))
    monkeypatch.setattr(state, "STATE_PATH", statefile)
    # 真实环境主密钥先于 init 存在；并发 init 测试也先预热 key，避免把 key 初次生成竞态混入状态竞态
    state._crypto().load_or_create_master_key()
    monkeypatch.setattr(setup, "GardenForum", FakeAdmin)
    FakeAdmin.calls = []
    FakeAdmin.disabled = False
    FakeAdmin.chars = [{"character_id": "sam", "account_exists": True,
                        "agent": "sam-agent", "display": "Sam", "retired": False}]
    return setup, state, keyfile, statefile


def _init(setup, keyfile, url=HTTPS):
    return setup.main(["init", "--url", url, "--key-file", str(keyfile)])


def test_init_empty_state_encrypts_without_register(setup_env):
    setup, state, keyfile, statefile = setup_env
    assert _init(setup, keyfile) == 0
    assert statefile.is_file()
    raw = statefile.read_text(encoding="utf-8")
    assert "PK_SYNTHETIC_SECURE" not in raw
    loaded = state.load()
    assert loaded["partner_key"] == "PK_SYNTHETIC_SECURE"
    assert loaded["bound"] == {}
    assert not any(c[0] == "register" for c in FakeAdmin.calls)
    assert any(c[0] == "whoami" for c in FakeAdmin.calls)


def test_init_existing_state_never_overwritten(setup_env):
    setup, state, keyfile, statefile = setup_env
    assert _init(setup, keyfile) == 0
    before = statefile.read_bytes()
    assert _init(setup, keyfile) == 2
    assert statefile.read_bytes() == before


def test_init_requires_https_or_loopback(setup_env):
    setup, state, keyfile, statefile = setup_env
    assert _init(setup, keyfile, "http://example.com") == 2
    assert not statefile.exists()


def test_init_disabled_host_rejected(setup_env):
    setup, state, keyfile, statefile = setup_env
    FakeAdmin.disabled = True
    assert _init(setup, keyfile) == 2
    assert not statefile.exists()


def test_init_save_failure_no_file(setup_env, monkeypatch):
    setup, state, keyfile, statefile = setup_env
    monkeypatch.setattr(state, "_save_unlocked", lambda doc, path=None: False)
    assert _init(setup, keyfile) == 3
    assert not statefile.exists()


def test_init_empty_key_rejected(setup_env, tmp_path):
    setup, state, keyfile, statefile = setup_env
    empty = tmp_path / "empty.key"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _init(setup, empty)
    assert not statefile.exists()


def test_adopt_after_init_chain_no_register(setup_env, monkeypatch):
    setup, state, keyfile, statefile = setup_env
    assert _init(setup, keyfile) == 0
    fake_agent = keyfile.parent / "agent.key"
    fake_agent.write_text("AK_SYNTHETIC_SAM\n", encoding="utf-8")
    async def lookup(cid):
        return ("Sam", 7)
    monkeypatch.setattr(setup, "_lookup", lookup)
    rc = setup.main(["adopt", "--url", HTTPS, "--id", "13", "--remote-id", "sam",
                     "--agent-key-file", str(fake_agent)])
    assert rc == 0
    loaded = state.load()
    assert loaded["bound"]["13"]["remote_id"] == "sam"
    assert loaded["bound"]["13"]["api_key"] == "AK_SYNTHETIC_SAM"
    assert loaded["partner_key"] == "PK_SYNTHETIC_SECURE"
    assert not any(c[0] == "register" for c in FakeAdmin.calls)



def test_init_race_state_created_during_whoami_is_preserved(setup_env):
    setup, state, keyfile, statefile = setup_env
    competitor = {}
    class RaceAdmin(FakeAdmin):
        def whoami(self, pk):
            # 模拟 whoami 网络等待期间另一个进程创建了含绑定的状态
            d = state.load()
            d["base_url"] = HTTPS
            d["partner_key"] = "PK_COMPETITOR"
            d["bound"] = {"99": {"api_key": "AK_COMPETITOR", "name": "竞争角色",
                                "user_id": 7, "base_url": HTTPS}}
            assert state.save(d)
            competitor["bytes"] = statefile.read_bytes()
            return {"partner": {"disabled": False, "used": 1, "remaining": 9},
                    "characters": FakeAdmin.chars}
    import garden_setup as setup_mod
    setup_mod.GardenForum = RaceAdmin
    rc = _init(setup, keyfile)
    assert rc == 2
    assert statefile.read_bytes() == competitor["bytes"]
    assert state.load()["bound"]["99"]["name"] == "竞争角色"


def test_init_two_concurrent_one_created_one_exists(setup_env):
    from concurrent.futures import ThreadPoolExecutor
    setup, state, keyfile, statefile = setup_env
    def call():
        return _init(setup, keyfile)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rcs = [f.result() for f in [pool.submit(call), pool.submit(call)]]
    assert sorted(rcs) == [0, 2]
    loaded = state.load()
    assert loaded["partner_key"] == "PK_SYNTHETIC_SECURE"
    assert loaded.get("manual_only") is True


def test_init_create_failure_leaves_no_partial_file(setup_env, monkeypatch):
    setup, state, keyfile, statefile = setup_env
    import garden_state as state_mod
    monkeypatch.setattr(state_mod.os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("link denied")))
    assert _init(setup, keyfile) == 3
    assert not statefile.exists()


def test_manual_only_blocks_background_tick():
    import garden_state as state
    assert state.is_manual_only({"manual_only": True}) is True
    assert state.is_manual_only({}) is False


def test_manual_only_real_on_tick_makes_no_network(monkeypatch):
    import asyncio
    from app.plugins import plugin_store, registry
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    mod = registry._loaded["garden_forum"]["module"]
    calls = []
    class GF:
        def __init__(self, *a, **k): calls.append("init")
        def state(self, *a, **k): calls.append("state")
    doc = {"base_url": HTTPS, "partner_key": "PK_SYNTHETIC", "manual_only": True,
           "bound": {"13": {"api_key": "AK", "user_id": 7, "base_url": HTTPS,
                            "remote_id": "sam"}}}
    monkeypatch.setattr(mod, "GardenForum", GF)
    mod._lock = asyncio.Lock()
    monkeypatch.setattr(mod.sdk, "get_config", lambda: {"garden_forum_on": True})
    monkeypatch.setattr(mod.store, "load", lambda: doc)
    asyncio.run(mod.on_tick({}))
    assert calls == []


def _write_competitor(state, statefile, cid, remote):
    d = state.load()
    d["bound"][str(cid)] = {"api_key": "AK_COMPETITOR_%s" % cid, "name": "竞争角色",
                            "user_id": 7, "base_url": HTTPS, "remote_id": remote}
    assert state.save(d)
    return statefile.read_bytes()


def test_adopt_merges_concurrent_other_binding(setup_env, monkeypatch):
    setup, state, keyfile, statefile = setup_env
    assert _init(setup, keyfile) == 0
    class RaceAdmin(FakeAdmin):
        def whoami(self, pk):
            _write_competitor(state, statefile, 99, "other")
            return {"partner": {"disabled": False, "used": 1, "remaining": 9},
                    "characters": FakeAdmin.chars}
    setup.GardenForum = RaceAdmin
    async def lookup(cid): return ("Sam", 7)
    monkeypatch.setattr(setup, "_lookup", lookup)
    agent = keyfile.parent / "agent.key"
    agent.write_text("AK_SYNTHETIC_SAM\n", encoding="utf-8")
    rc = setup.main(["adopt", "--url", HTTPS, "--id", "13", "--remote-id", "sam",
                     "--agent-key-file", str(agent)])
    assert rc == 0
    loaded = state.load()
    assert loaded["bound"]["99"]["remote_id"] == "other"
    assert loaded["bound"]["13"]["remote_id"] == "sam"


def test_adopt_same_role_concurrent_change_rejected(setup_env, monkeypatch):
    setup, state, keyfile, statefile = setup_env
    assert _init(setup, keyfile) == 0
    competitor_bytes = {}
    class RaceAdmin(FakeAdmin):
        def whoami(self, pk):
            competitor_bytes["v"] = _write_competitor(state, statefile, 13, "other")
            return {"partner": {"disabled": False, "used": 1, "remaining": 9},
                    "characters": FakeAdmin.chars}
    setup.GardenForum = RaceAdmin
    async def lookup(cid): return ("Sam", 7)
    monkeypatch.setattr(setup, "_lookup", lookup)
    agent = keyfile.parent / "agent.key"
    agent.write_text("AK_SYNTHETIC_SAM\n", encoding="utf-8")
    rc = setup.main(["adopt", "--url", HTTPS, "--id", "13", "--remote-id", "sam",
                     "--agent-key-file", str(agent)])
    assert rc == 2
    assert statefile.read_bytes() == competitor_bytes["v"]
    assert state.load()["bound"]["13"]["remote_id"] == "other"


@pytest.mark.parametrize("value", ["true", None, 1, 0, [], {}])
def test_manual_invalid_values_fail_closed(value):
    import garden_state as state
    doc = {"manual_only": value}
    assert state.manual_mode(doc) == "invalid"
    assert state.manual_blocks_tick(doc) is True


def test_manual_explicit_false_and_legacy_are_not_blocked():
    import garden_state as state
    assert state.manual_mode({"manual_only": False}) == "off"
    assert state.manual_blocks_tick({"manual_only": False}) is False
    assert state.manual_mode({}) == "legacy"
    assert state.manual_blocks_tick({}) is False


def test_manual_invalid_persisted_blocks_real_tick(monkeypatch):
    import asyncio
    from app.plugins import plugin_store, registry
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    mod = registry._loaded["garden_forum"]["module"]
    calls = []
    class GF:
        def __init__(self, *a, **k): calls.append("init")
        def state(self, *a, **k): calls.append("state")
    doc = {"base_url": HTTPS, "partner_key": "PK_SYNTHETIC",
           "manual_only": "true",
           "bound": {"13": {"api_key": "AK", "user_id": 7, "base_url": HTTPS,
                            "remote_id": "sam"}}}
    monkeypatch.setattr(mod, "GardenForum", GF)
    mod._lock = asyncio.Lock()
    monkeypatch.setattr(mod.sdk, "get_config", lambda: {"garden_forum_on": True})
    monkeypatch.setattr(mod.store, "load", lambda: doc)
    asyncio.run(mod.on_tick({}))
    assert calls == []


def test_stale_snapshot_cannot_overwrite_newer_state(tmp_path, monkeypatch):
    import garden_state as state
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    p = tmp_path / "state.json"
    assert state.save(state.load(p), p)
    stale = state.load(p)
    fresh = state.load(p)
    fresh["bound"] = {"99": {"api_key": "AK_FRESH", "name": "fresh",
                            "user_id": 7, "base_url": HTTPS}}
    assert state.save(fresh, p) is True
    assert state.save(stale, p) is False
    back = state.load(p)
    assert back["bound"]["99"]["name"] == "fresh"


def test_update_state_then_old_save_is_stale(tmp_path, monkeypatch):
    import garden_state as state
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    p = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_PATH", p)
    assert state.save(state.load(p), p)
    old = state.load(p)
    def add(doc):
        doc["bound"] = {"99": {"api_key": "AK_MERGE", "name": "merge",
                               "user_id": 7, "base_url": HTTPS}}
        return doc
    ok, _ = state.update_state(add)
    assert ok is True
    # 旧快照 save 不得覆盖 update_state 的新数据
    assert state.save(old) is False
    assert state.load()["bound"]["99"]["name"] == "merge"


def test_save_creates_missing_parent_only_for_target(tmp_path, monkeypatch):
    import garden_state as state
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    p = tmp_path / "new" / "deeper" / "state.json"
    assert state.save(state.load(p), p) is True
    assert p.is_file()


def test_independent_process_cas_blocks_stale_save(tmp_path, monkeypatch):
    import subprocess, sys
    import garden_state as state
    key = tmp_path / "k.key"
    st = tmp_path / "state.json"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(key))
    assert state.save(state.load(st), st)
    stale = state.load(st)
    code = (
        "import sys, pathlib; sys.path.insert(0, %r); sys.path.insert(0, %r); import garden_state as s; "
        "p=pathlib.Path(%r); d=s.load(p); d['bound']={'99':{'api_key':'AK_PROC','name':'proc','user_id':7,'base_url':%r}}; "
        "raise SystemExit(0 if s.save(d,p) else 1)"
        % (str(PLUGIN), str(PLUGIN.parents[2] / "backend"), str(st), HTTPS)
    )
    env = dict(__import__("os").environ, AMBRACE_CREDENTIAL_KEY_FILE=str(key))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert state.save(stale, st) is False
    assert state.load(st)["bound"]["99"]["name"] == "proc"


def test_failed_replace_does_not_advance_rev(tmp_path, monkeypatch):
    import garden_state as state
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    p = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_PATH", p)
    assert state.save(state.load(p), p)
    a = state.load(p)
    rev_before = a["_rev"]
    real_replace = state.os.replace
    monkeypatch.setattr(state.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    a["bound"] = {"13": {"api_key": "AK_A", "name": "A", "user_id": 7, "base_url": HTTPS}}
    assert state.save(a) is False
    assert a["_rev"] == rev_before, "失败不能提前推进调用方版本"
    monkeypatch.setattr(state.os, "replace", real_replace)
    b = state.load(p)
    b["bound"] = {"99": {"api_key": "AK_B", "name": "B", "user_id": 7, "base_url": HTTPS}}
    assert state.save(b) is True
    # A 重试时版本已过期，不能覆盖 B
    assert state.save(a) is False
    assert state.load(p)["bound"]["99"]["name"] == "B"


def test_missing_snapshot_cannot_clear_state_created_after_load(tmp_path, monkeypatch):
    import garden_state as state
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    p = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_PATH", p)
    missing = state.load(p)
    assert missing.get("_state_source") == "missing"
    created = state.load(p)
    created["bound"] = {"99": {"api_key": "AK_CREATED", "name": "created",
                               "user_id": 7, "base_url": HTTPS}}
    assert state.save(created) is True
    before = p.read_bytes()
    assert state.save(missing) is False
    assert p.read_bytes() == before
    assert state.load(p)["bound"]["99"]["name"] == "created"


def test_corrupt_state_is_not_treated_as_empty(tmp_path, monkeypatch):
    import garden_state as state
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    default = state.STATE_PATH
    default_before = (default.exists(), default.stat().st_mtime_ns if default.exists() else None,
                      default.stat().st_size if default.exists() else None)
    p = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_PATH", p)
    p.write_text("{not-json", encoding="utf-8")
    before = p.read_bytes()
    doc = state.load(p)
    assert doc.get("_state_source") == "corrupt"
    assert state.save(doc) is False
    assert p.read_bytes() == before
    # 不得把合成状态写到项目运行目录/默认状态
    after = (default.exists(), default.stat().st_mtime_ns if default.exists() else None,
             default.stat().st_size if default.exists() else None)
    assert after == default_before


def test_corrupt_then_vanished_target_still_refuses_save(tmp_path, monkeypatch):
    import garden_state as state
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    p = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_PATH", p)
    p.write_text("{not-json", encoding="utf-8")
    doc = state.load(p)
    p.unlink()
    assert state.save(doc) is False
    assert not p.exists()


def test_non_object_json_is_corrupt_not_empty(tmp_path, monkeypatch):
    import garden_state as state
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    p = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_PATH", p)
    p.write_text("[1,2,3]", encoding="utf-8")
    before = p.read_bytes()
    doc = state.load(p)
    assert doc.get("_state_source") == "corrupt"
    assert state.save(doc) is False
    assert p.read_bytes() == before


def test_deleted_recreated_aba_blocks_old_snapshot(tmp_path, monkeypatch):
    import garden_state as state
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "k.key"))
    p = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_PATH", p)
    assert state.save(state.load(p), p)
    old = state.load(p)
    p.unlink()
    new = state.load(p)
    new["bound"] = {"99": {"api_key": "AK_REBORN", "name": "reborn",
                           "user_id": 7, "base_url": HTTPS}}
    assert state.save(new) is True
    assert state.save(old) is False
    assert state.load(p)["bound"]["99"]["name"] == "reborn"
