# -*- coding: utf-8 -*-
"""Sam 身份映射：remote_id、显式 adopt、tick 对账闸门与旧绑定兼容。"""
import asyncio
import pathlib
import sys

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "http://127.0.0.1:8080"


@pytest.fixture(autouse=True)
def _plugin_on_path():
    """并行档（-n 8）下 sys.path[0] 可能被其它用例改写：每个用例重新压入插件目录。"""
    _p = str(PLUGIN)
    if _p not in sys.path:
        sys.path.insert(0, _p)
    yield


def _doc():
    return {
        "base_url": BASE,
        "partner_key": "PK_SYNTHETIC_ONLY",
        "bound": {"13": {"api_key": "AK_SYNTHETIC_SAM", "name": "Sam",
                          "user_id": 7, "base_url": BASE, "bound_at": 1,
                          "remote_id": "sam"}},
        "pending": {}, "pending_tombstones": {},
        "_credential_meta": {"ok": True, "crypto": True, "decrypt_failed": 0, "plaintext": []},
    }


def _legacy_doc():
    d = _doc()
    d["bound"] = {"3": {"api_key": "AK_LEGACY", "name": "旧角色",
                        "user_id": 7, "base_url": BASE, "bound_at": 1}}
    return d


def test_state_remote_mapping_and_legacy_compat():
    import garden_state as state
    d = _doc()
    assert state.remote_id_of(d, 13) == "sam"
    assert state.effective_remote_id(d, 13) == "sam"
    assert state.local_for_remote(d, BASE, "sam") == "13"
    assert state.binding_matches(d, 13, BASE, 7)
    assert state.binding_matches(d, 13, BASE, 7, require_remote=True)
    old = _legacy_doc()
    assert state.remote_id_of(old, 3) == ""
    assert state.effective_remote_id(old, 3) == "3"
    assert state.binding_matches(old, 3, BASE, 7) is True
    assert state.binding_matches(old, 3, BASE, 7, require_remote=True) is False


def test_state_same_remote_two_locals_is_ambiguous():
    import garden_state as state
    d = _doc()
    d["bound"]["14"] = {"api_key": "AK2", "name": "另一个", "user_id": 7,
                        "base_url": BASE, "remote_id": "sam"}
    assert state.remote_id_conflict(d, BASE, "sam") == "13"
    assert state.local_for_remote(d, BASE, "sam") is None
    assert state.remote_id_conflict(d, BASE, "other") is None


def test_state_put_binding_keeps_or_sets_remote():
    import garden_state as state
    d = _doc()
    state.put_binding(d, 13, "AK_NEW", "Sam", 7, base_url=BASE, remote_id="sam")
    assert d["bound"]["13"]["remote_id"] == "sam"
    state.put_binding(d, 13, "AK_NEW2", "Sam", 7, base_url=BASE)
    assert d["bound"]["13"]["remote_id"] == "sam"


def _adopt_args(tmp_path, **over):
    kf = tmp_path / "agent.key"
    kf.write_text("AK_SYNTHETIC_ADOPT\n", encoding="utf-8")
    args = type("A", (), {
        "id": "13", "remote_id": "sam", "agent_key_file": str(kf),
        "url": BASE, "key_file": "",
    })()
    for k, v in over.items():
        setattr(args, k, v)
    return args


def test_adopt_imports_key_without_register(tmp_path, monkeypatch):
    import garden_setup as setup
    import garden_state as state
    d = _doc()
    saved = {}
    calls = {"register": 0, "whoami": 0}
    class Admin:
        def whoami(self, pk):
            calls["whoami"] += 1
            return {"characters": [{"character_id": "sam", "account_exists": True,
                                    "display": "Sam", "retired": False, "agent": "sam-agent"}]}
        def register(self, *a, **k):
            calls["register"] += 1
            raise AssertionError("adopt 不得调用 register")
    monkeypatch.setattr(setup, "store", state)
    monkeypatch.setattr(state, "load", lambda: d)
    monkeypatch.setattr(state, "save", lambda doc: saved.update(doc) or True)
    async def lookup(cid):
        return ("Sam", 7)
    monkeypatch.setattr(setup, "_lookup", lookup)
    monkeypatch.setattr(setup, "_admin", lambda url: Admin())
    class GF:
        def __init__(self, base, key="", name="", timeout=0): pass
        def me(self): return {"agent": {"name": "sam-agent"}}
    monkeypatch.setattr(setup, "GardenForum", GF)
    rc = setup.main(["adopt", "--id", "13", "--remote-id", "sam",
                     "--agent-key-file", str(_adopt_args(tmp_path).agent_key_file),
                     "--url", BASE])
    assert rc == 0
    assert calls["register"] == 0 and calls["whoami"] == 1
    assert saved["bound"]["13"]["remote_id"] == "sam"
    assert saved["bound"]["13"]["api_key"] == "AK_SYNTHETIC_ADOPT"


def test_adopt_rejects_missing_remote_wrong_id_and_conflict(tmp_path, monkeypatch):
    import garden_setup as setup
    import garden_state as state
    d = _legacy_doc()
    saves = {"n": 0}
    class Admin:
        def whoami(self, pk):
            return {"characters": [{"character_id": "sam", "account_exists": True, "agent": "sam-agent"}]}
    monkeypatch.setattr(setup, "store", state)
    monkeypatch.setattr(state, "load", lambda: d)
    monkeypatch.setattr(state, "save", lambda doc: saves.__setitem__("n", saves["n"] + 1) or True)
    async def lookup(cid):
        return ("旧角色", 7)
    monkeypatch.setattr(setup, "_lookup", lookup)
    monkeypatch.setattr(setup, "_admin", lambda url: Admin())
    class GF:
        def __init__(self, base, key="", name="", timeout=0): pass
        def me(self): return {"agent": {"name": "sam-agent"}}
    monkeypatch.setattr(setup, "GardenForum", GF)
    keyfile = _adopt_args(tmp_path).agent_key_file
    # cid 13 没有旧绑定；目标 missing 不存在，拒绝
    assert setup.main(["adopt", "--id", "13", "--remote-id", "missing",
                       "--agent-key-file", keyfile, "--url", BASE]) == 4
    # 显式映射缺目标：目标存在性拒绝
    d_missing = _doc()
    d_missing["bound"]["15"] = {"api_key": "AK15", "name": "15", "user_id": 7,
                                "base_url": BASE, "remote_id": "missing"}
    monkeypatch.setattr(state, "load", lambda: d_missing)
    async def lookup15(cid): return ("15", 7)
    monkeypatch.setattr(setup, "_lookup", lookup15)
    assert setup.main(["adopt", "--id", "15", "--remote-id", "missing",
                       "--agent-key-file", keyfile, "--url", BASE]) == 4
    # 本地 3 已映射到 sam，另一个本地 14 不能再抢
    d2 = _doc()
    d2["bound"]["14"] = {"api_key": "AK14", "name": "14", "user_id": 7,
                         "base_url": BASE, "remote_id": "other"}
    monkeypatch.setattr(state, "load", lambda: d2)
    async def lookup14(cid):
        return ("14", 7)
    monkeypatch.setattr(setup, "_lookup", lookup14)
    assert setup.main(["adopt", "--id", "14", "--remote-id", "sam",
                       "--agent-key-file", keyfile, "--url", BASE]) == 2
    assert saves["n"] == 0


def test_adopt_missing_local_owner_no_write(tmp_path, monkeypatch):
    import garden_setup as setup
    import garden_state as state
    d = _doc()
    saved = {"n": 0}
    monkeypatch.setattr(setup, "store", state)
    monkeypatch.setattr(state, "load", lambda: d)
    monkeypatch.setattr(state, "save", lambda doc: saved.__setitem__("n", 1) or True)
    async def lookup(cid):
        return ("", None)
    monkeypatch.setattr(setup, "_lookup", lookup)
    assert setup.main(["adopt", "--id", "13", "--remote-id", "sam",
                       "--agent-key-file", str(_adopt_args(tmp_path).agent_key_file),
                       "--url", BASE]) == 2
    assert saved["n"] == 0


@pytest.fixture(scope="module")
def main_mod():
    from app.plugins import plugin_store, registry
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    return registry._loaded["garden_forum"]["module"]


class FakeAdmin:
    reconcile_result = {"disabled": False, "remaining": 10, "need_register": []}
    calls = []

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

    def reconcile(self, partner_key, local_ids):
        FakeAdmin.calls.append(("reconcile", list(local_ids)))
        return dict(FakeAdmin.reconcile_result)

    def register(self, partner_key, character_id, display="", bio=""):
        FakeAdmin.calls.append(("register", character_id))
        return {"api_key": "AK_REGISTERED", "existed": False}


def _run_reconcile(main_mod, monkeypatch, d, need=None):
    FakeAdmin.calls = []
    FakeAdmin.reconcile_result = {"disabled": False, "remaining": 10,
                                  "need_register": list(need or [])}
    monkeypatch.setattr(main_mod, "GardenForum", FakeAdmin)
    monkeypatch.setattr(main_mod.store, "save", lambda doc: True)
    return asyncio.run(main_mod._reconcile("PK_SYNTHETIC_ONLY", BASE, d,
                                            main_mod.store.bound_ids(d, BASE), 1000))


def test_reconcile_uses_remote_ids(main_mod, monkeypatch):
    d = _doc()
    _run_reconcile(main_mod, monkeypatch, d)
    assert ("reconcile", ["sam"]) in FakeAdmin.calls
    assert not any(c[0] == "register" for c in FakeAdmin.calls)


def test_reconcile_mapped_missing_remote_does_not_rebuild(main_mod, monkeypatch):
    d = _doc()
    _run_reconcile(main_mod, monkeypatch, d, need=["sam"])
    assert not any(c[0] == "register" for c in FakeAdmin.calls)
    assert d["bound"]["13"]["remote_id"] == "sam"
    assert d["bound"]["13"]["api_key"] == "AK_SYNTHETIC_SAM"


def test_reconcile_unmapped_remote_never_registers(main_mod, monkeypatch):
    d = _legacy_doc()  # 本地 3，无 remote
    _run_reconcile(main_mod, monkeypatch, d, need=["sam"])
    assert not any(c[0] == "register" for c in FakeAdmin.calls)


def test_reconcile_legacy_missing_local_id_registers_as_before(main_mod, monkeypatch):
    d = _legacy_doc()
    _run_reconcile(main_mod, monkeypatch, d, need=["3"])
    assert ("register", "3") in FakeAdmin.calls
    assert "remote_id" not in d["bound"]["3"]


def test_adopt_allows_same_local_and_remote_id(tmp_path, monkeypatch):
    import garden_setup as setup
    import garden_state as state
    d = _legacy_doc()
    saved = {}
    class Admin:
        def whoami(self, pk):
            return {"characters": [{"character_id": "3", "account_exists": True,
                                    "agent": "local-agent"}]}
    class GF:
        def __init__(self, base, key="", name="", timeout=0): pass
        def me(self): return {"agent": {"name": "local-agent"}}
    monkeypatch.setattr(setup, "store", state)
    monkeypatch.setattr(state, "load", lambda: d)
    monkeypatch.setattr(state, "save", lambda doc: saved.update(doc) or True)
    async def lookup(cid): return ("旧角色", 7)
    monkeypatch.setattr(setup, "_lookup", lookup)
    monkeypatch.setattr(setup, "_admin", lambda url: Admin())
    monkeypatch.setattr(setup, "GardenForum", GF)
    rc = setup.main(["adopt", "--id", "3", "--remote-id", "3",
                     "--agent-key-file", str(_adopt_args(tmp_path).agent_key_file),
                     "--url", BASE])
    assert rc == 0
    assert saved["bound"]["3"]["remote_id"] == "3"


def test_adopt_rejects_other_agents_key_and_invalid_key(tmp_path, monkeypatch):
    import garden_setup as setup
    import garden_state as state
    d = _legacy_doc()
    saved = {"n": 0}
    class Admin:
        def whoami(self, pk):
            return {"characters": [{"character_id": "sam", "account_exists": True,
                                    "agent": "sam-agent"}]}
    class WrongKey:
        def __init__(self, base, key="", name="", timeout=0): pass
        def me(self): return {"agent": {"name": "other-agent"}}
    class BadKey:
        def __init__(self, base, key="", name="", timeout=0): pass
        def me(self):
            from garden_forum_sdk import GardenError
            raise GardenError("invalid_agent_key", "bad", 401, 401)
    monkeypatch.setattr(setup, "store", state)
    monkeypatch.setattr(state, "load", lambda: d)
    monkeypatch.setattr(state, "save", lambda doc: saved.__setitem__("n", 1) or True)
    async def lookup(cid): return ("旧角色", 7)
    monkeypatch.setattr(setup, "_lookup", lookup)
    monkeypatch.setattr(setup, "_admin", lambda url: Admin())
    keyfile = str(_adopt_args(tmp_path).agent_key_file)
    monkeypatch.setattr(setup, "GardenForum", WrongKey)
    assert setup.main(["adopt", "--id", "13", "--remote-id", "sam",
                       "--agent-key-file", keyfile, "--url", BASE]) == 2
    monkeypatch.setattr(setup, "GardenForum", BadKey)
    assert setup.main(["adopt", "--id", "13", "--remote-id", "sam",
                       "--agent-key-file", keyfile, "--url", BASE]) == 2
    assert saved["n"] == 0


def test_adopt_save_failure_keeps_original(tmp_path, monkeypatch):
    import garden_setup as setup
    import garden_state as state
    d = _doc()
    class Admin:
        def whoami(self, pk):
            return {"characters": [{"character_id": "sam", "account_exists": True,
                                    "agent": "sam-agent"}]}
    class GF:
        def __init__(self, base, key="", name="", timeout=0): pass
        def me(self): return {"agent": {"name": "sam-agent"}}
    monkeypatch.setattr(setup, "store", state)
    monkeypatch.setattr(state, "load", lambda: d)
    monkeypatch.setattr(state, "save", lambda doc: False)
    async def lookup(cid): return ("旧角色", 7)
    monkeypatch.setattr(setup, "_lookup", lookup)
    monkeypatch.setattr(setup, "_admin", lambda url: Admin())
    monkeypatch.setattr(setup, "GardenForum", GF)
    rc = setup.main(["adopt", "--id", "13", "--remote-id", "sam",
                     "--agent-key-file", str(_adopt_args(tmp_path).agent_key_file),
                     "--url", BASE])
    assert rc == 3
    assert d["bound"]["13"]["api_key"] == "AK_SYNTHETIC_SAM"
    assert d["bound"]["13"]["remote_id"] == "sam"


def test_adopt_legacy_different_remote_is_rejected_without_write(tmp_path, monkeypatch):
    import garden_setup as setup
    import garden_state as state
    d = _legacy_doc()  # 角色 3 的 legacy 有效远端=3
    saves = {"n": 0}
    class Admin:
        def whoami(self, pk):
            return {"characters": [{"character_id": "sam", "account_exists": True,
                                    "agent": "sam-agent"}]}
    class GF:
        def __init__(self, base, key="", name="", timeout=0): pass
        def me(self): return {"agent": {"name": "sam-agent"}}
    monkeypatch.setattr(setup, "store", state)
    monkeypatch.setattr(state, "load", lambda: d)
    monkeypatch.setattr(state, "save", lambda doc: saves.__setitem__("n", 1) or True)
    async def lookup(cid): return ("旧角色", 7)
    monkeypatch.setattr(setup, "_lookup", lookup)
    monkeypatch.setattr(setup, "_admin", lambda url: Admin())
    monkeypatch.setattr(setup, "GardenForum", GF)
    rc = setup.main(["adopt", "--id", "3", "--remote-id", "sam",
                     "--agent-key-file", str(_adopt_args(tmp_path).agent_key_file),
                     "--url", BASE])
    assert rc == 2
    assert saves["n"] == 0
    assert d["bound"]["3"]["api_key"] == "AK_LEGACY"


def test_adopt_legacy_same_remote_can_recover_key(tmp_path, monkeypatch):
    import garden_setup as setup
    import garden_state as state
    d = _legacy_doc()
    saved = {}
    class Admin:
        def whoami(self, pk):
            return {"characters": [{"character_id": "3", "account_exists": True,
                                    "agent": "local-agent"}]}
    class GF:
        def __init__(self, base, key="", name="", timeout=0): pass
        def me(self): return {"agent": {"name": "local-agent"}}
    monkeypatch.setattr(setup, "store", state)
    monkeypatch.setattr(state, "load", lambda: d)
    monkeypatch.setattr(state, "save", lambda doc: saved.update(doc) or True)
    async def lookup(cid): return ("旧角色", 7)
    monkeypatch.setattr(setup, "_lookup", lookup)
    monkeypatch.setattr(setup, "_admin", lambda url: Admin())
    monkeypatch.setattr(setup, "GardenForum", GF)
    rc = setup.main(["adopt", "--id", "3", "--remote-id", "3",
                     "--agent-key-file", str(_adopt_args(tmp_path).agent_key_file),
                     "--url", BASE])
    assert rc == 0
    assert saved["bound"]["3"]["remote_id"] == "3"
    assert saved["bound"]["3"]["api_key"] == "AK_SYNTHETIC_ADOPT"


def test_adopt_cross_site_and_tenant_rejected(tmp_path, monkeypatch):
    import garden_setup as setup
    import garden_state as state
    saves = {"n": 0}
    class Admin:
        def whoami(self, pk):
            return {"characters": [{"character_id": "3", "account_exists": True,
                                    "agent": "local-agent"}]}
    class GF:
        def __init__(self, base, key="", name="", timeout=0): pass
        def me(self): return {"agent": {"name": "local-agent"}}
    monkeypatch.setattr(setup, "store", state)
    monkeypatch.setattr(state, "save", lambda doc: saves.__setitem__("n", 1) or True)
    monkeypatch.setattr(setup, "_admin", lambda url: Admin())
    monkeypatch.setattr(setup, "GardenForum", GF)
    d_site = _legacy_doc()
    d_site["bound"]["3"]["base_url"] = "http://127.0.0.1:9999"
    monkeypatch.setattr(state, "load", lambda: d_site)
    async def lookup(cid): return ("旧角色", 7)
    monkeypatch.setattr(setup, "_lookup", lookup)
    keyfile = str(_adopt_args(tmp_path).agent_key_file)
    assert setup.main(["adopt", "--id", "3", "--remote-id", "3",
                       "--agent-key-file", keyfile, "--url", BASE]) == 2
    d_tenant = _legacy_doc()
    d_tenant["bound"]["3"]["user_id"] = 8
    monkeypatch.setattr(state, "load", lambda: d_tenant)
    assert setup.main(["adopt", "--id", "3", "--remote-id", "3",
                       "--agent-key-file", keyfile, "--url", BASE]) == 2
    assert saves["n"] == 0
