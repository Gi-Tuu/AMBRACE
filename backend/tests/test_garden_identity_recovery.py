# -*- coding: utf-8 -*-
"""主密钥丢失/找回：解不开的旧状态是证据，不能被新 key 或空值覆盖。"""
import pathlib
import sys


PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "http://127.0.0.1:8080"


def _doc():
    return {"base_url": BASE, "partner_key": "PK_SYNTHETIC_KEY",
            "bound": {"13": {"api_key": "AK_SYNTHETIC_KEY", "name": "Sam",
                              "user_id": 7, "base_url": BASE, "remote_id": "sam"}},
            "pending": {}, "pending_tombstones": {}}


def test_encrypt_save_and_load_roundtrip(tmp_path, monkeypatch):
    import garden_state as state
    key = tmp_path / "synthetic.key"
    st = tmp_path / "state.json"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(key))
    doc = _doc()
    assert state.save(doc, st) is True
    raw = st.read_text(encoding="utf-8")
    assert "AK_SYNTHETIC_KEY" not in raw and "PK_SYNTHETIC_KEY" not in raw
    loaded = state.load(st)
    assert state.credential_usable(loaded) is True
    assert loaded["bound"]["13"]["api_key"] == "AK_SYNTHETIC_KEY"
    assert loaded["bound"]["13"]["remote_id"] == "sam"


def test_lost_master_key_preserves_original_and_refuses_overwrite(tmp_path, monkeypatch):
    import garden_state as state
    old_key = tmp_path / "old.key"
    new_key = tmp_path / "new.key"
    st = tmp_path / "state.json"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(old_key))
    assert state.save(_doc(), st) is True
    before = st.read_bytes()
    # 模拟原主密钥丢失、环境指向一把新 key
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(new_key))
    loaded = state.load(st)
    assert state.credential_usable(loaded) is False
    assert loaded["_credential_meta"]["decrypt_failed"] == 1
    assert loaded["bound"]["13"]["api_key"] is None
    assert state.save(loaded, st) is False
    assert st.read_bytes() == before


def test_recover_with_original_key_keeps_mapping_and_no_plaintext(tmp_path, monkeypatch):
    import garden_state as state
    key = tmp_path / "old.key"
    st = tmp_path / "state.json"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(key))
    assert state.save(_doc(), st) is True
    # 换新 key 解不开
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "wrong.key"))
    assert state.credential_usable(state.load(st)) is False
    # 找回原 key 后仍可解，且未写成明文
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(key))
    loaded = state.load(st)
    assert state.credential_usable(loaded) is True
    assert loaded["bound"]["13"]["api_key"] == "AK_SYNTHETIC_KEY"
    assert "AK_SYNTHETIC_KEY" not in st.read_text(encoding="utf-8")


def test_adopt_refuses_when_existing_cipher_undecryptable(tmp_path, monkeypatch):
    import garden_setup as setup
    import garden_state as state
    old_key = tmp_path / "old.key"
    st = tmp_path / "state.json"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(old_key))
    assert state.save(_doc(), st) is True
    before = st.read_bytes()
    monkeypatch.setattr(state, "STATE_PATH", st)
    keyfile = tmp_path / "agent.key"
    keyfile.write_text("AK_NEW\n", encoding="utf-8")
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(tmp_path / "wrong.key"))
    rc = setup.main(["adopt", "--id", "13", "--remote-id", "sam",
                     "--agent-key-file", str(keyfile), "--url", BASE])
    assert rc == 3
    assert st.read_bytes() == before


def test_missing_key_file_does_not_create_new_key_during_load(tmp_path, monkeypatch):
    import garden_state as state
    old_key = tmp_path / "old.key"
    st = tmp_path / "state.json"
    missing = tmp_path / "missing.key"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(old_key))
    assert state.save(_doc(), st) is True
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(missing))
    loaded = state.load(st)
    assert state.credential_usable(loaded) is False
    assert not missing.exists(), "load 不应静默创建新主密钥"
