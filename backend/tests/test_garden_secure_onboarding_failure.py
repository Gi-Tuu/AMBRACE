# -*- coding: utf-8 -*-
"""安全初始化/adopt 失败路径：key 归属、重复、跨站、保存失败，均不写/不 register。"""
import pathlib
import sys

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "http://127.0.0.1:8080"
HTTPS = "https://garden.example.invalid"


class FakeAdmin:
    calls = []
    disabled = False
    chars = [{"character_id": "sam", "account_exists": True, "agent": "sam-agent",
              "display": "Sam", "retired": False}]

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

    def whoami(self, pk):
        FakeAdmin.calls.append("whoami")
        return {"partner": {"disabled": FakeAdmin.disabled, "used": 1, "remaining": 9},
                "characters": FakeAdmin.chars}

    def me(self):
        return {"agent": {"name": "sam-agent"}}

    def register(self, *a, **k):
        FakeAdmin.calls.append("register")
        raise AssertionError("不得 register")


@pytest.fixture()
def secure(tmp_path, monkeypatch):
    import garden_setup as setup
    import garden_state as state
    keyfile = tmp_path / "partner.key"
    keyfile.write_text("PK_FAILURE_SYNTHETIC\n", encoding="utf-8")
    statefile = tmp_path / "state.json"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "secrets.key"))
    monkeypatch.setattr(state, "STATE_PATH", statefile)
    monkeypatch.setattr(setup, "GardenForum", FakeAdmin)
    async def lookup(cid):
        return ("Sam", 7)
    monkeypatch.setattr(setup, "_lookup", lookup)
    FakeAdmin.calls = []
    FakeAdmin.disabled = False
    FakeAdmin.chars = [{"character_id": "sam", "account_exists": True,
                        "agent": "sam-agent", "display": "Sam", "retired": False}]
    return setup, state, keyfile, statefile


def _init(setup, keyfile):
    return setup.main(["init", "--url", HTTPS, "--key-file", str(keyfile)])


def _adopt(setup, keyfile, cid="13", rid="sam", url=HTTPS, agent_name="sam-agent"):
    agent = keyfile.parent / f"agent-{cid}.key"
    agent.write_text("AK_AGENT_SYNTHETIC\n", encoding="utf-8")
    class GF:
        def __init__(self, base, key="", name="", timeout=0): pass
        def whoami(self, pk):
            return {"partner": {"disabled": False, "used": 1, "remaining": 9},
                    "characters": FakeAdmin.chars}
        def me(self): return {"agent": {"name": agent_name}}
    setup.GardenForum = GF
    return setup.main(["adopt", "--url", url, "--id", cid, "--remote-id", rid,
                       "--agent-key-file", str(agent)])


def test_adopt_wrong_agent_key_rejected(secure):
    setup, state, keyfile, statefile = secure
    assert _init(setup, keyfile) == 0
    before = statefile.read_bytes()
    assert _adopt(setup, keyfile, agent_name="other-agent") == 2
    assert statefile.read_bytes() == before
    assert state.load()["bound"] == {}


def test_adopt_empty_key_rejected(secure, tmp_path):
    setup, state, keyfile, statefile = secure
    assert _init(setup, keyfile) == 0
    empty = tmp_path / "empty-agent.key"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        setup.main(["adopt", "--url", HTTPS, "--id", "13", "--remote-id", "sam",
                    "--agent-key-file", str(empty)])
    assert state.load()["bound"] == {}


def test_adopt_duplicate_remote_rejected(secure):
    setup, state, keyfile, statefile = secure
    assert _init(setup, keyfile) == 0
    assert _adopt(setup, keyfile, "13", "sam") == 0
    before = statefile.read_bytes()
    assert _adopt(setup, keyfile, "14", "sam") == 2
    assert statefile.read_bytes() == before


def test_adopt_cross_site_on_existing_binding_rejected(secure):
    setup, state, keyfile, statefile = secure
    assert _init(setup, keyfile) == 0
    assert _adopt(setup, keyfile, "13", "sam") == 0
    before = statefile.read_bytes()
    assert _adopt(setup, keyfile, "13", "sam", url="https://other-garden.invalid") == 2
    assert statefile.read_bytes() == before


def test_adopt_save_failure_keeps_file_and_pending(secure):
    setup, state, keyfile, statefile = secure
    assert _init(setup, keyfile) == 0
    loaded = state.load()
    loaded["pending"] = {"synthetic-pid": {"state": "unknown", "cid": "13"}}
    assert state.save(loaded)
    before = statefile.read_bytes()
    real_save = state._save_unlocked
    state._save_unlocked = lambda doc, path=None: False
    try:
        assert _adopt(setup, keyfile, "13", "sam") == 3
    finally:
        state._save_unlocked = real_save
    assert statefile.read_bytes() == before
    assert state.load()["pending"]["synthetic-pid"]["state"] == "unknown"


def test_init_refuses_plaintext_or_existing_state(secure):
    setup, state, keyfile, statefile = secure
    statefile.write_text('{"partner_key": "PLAINTEXT_NOT_ALLOWED", "bound": {}}', encoding="utf-8")
    before = statefile.read_bytes()
    assert _init(setup, keyfile) == 2
    assert statefile.read_bytes() == before
    assert not any(c == "register" for c in FakeAdmin.calls)
