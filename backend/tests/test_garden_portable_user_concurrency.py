# -*- coding: utf-8 -*-
"""跨进程文件锁/CAS：并发抢同一 role 只一个成功；持锁进程退出后恢复。"""
import os
import pathlib
import subprocess
import sys
import textwrap

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))


def _worker_code(state, uid, cid, role, key):
    return textwrap.dedent('''
        import pathlib, sys
        sys.path.insert(0, %r)
        sys.path.insert(0, %r)
        import garden_state as store
        store.STATE_PATH = pathlib.Path(%r)
        def m(doc):
            if store.user_binding_for_role(doc, "https://garden.example.invalid", %r):
                raise RuntimeError("role_bound")
            store.put_user_binding(doc, %d, base_url="https://garden.example.invalid",
                                   role_id=%r, character_id=%d,
                                   remote_character_id="sam", agent="a",
                                   user_key=%r)
            return doc
        try:
            ok, _ = store.update_state(m)
            print("OK" if ok else "FAIL")
        except store.UpdateConflict:
            print("CONFLICT")
    ''' % (str(BACKEND), str(PLUGIN), str(state), role, uid, role, cid, key))


def test_concurrent_same_role_one_wins(tmp_path, monkeypatch):
    import garden_state as store
    keyfile = tmp_path / "master.key"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(keyfile))
    state = tmp_path / "state.json"
    assert store.save({"manual_only": True, "bound": {}, "user_bindings": {}}, state)
    env = dict(os.environ, AMBRACE_CREDENTIAL_KEY_FILE=str(keyfile),
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    procs = [subprocess.Popen([sys.executable, "-c", _worker_code(state, u, c, "role-x", "UK_%d" % u)],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace")
             for u, c in ((1, 13), (2, 14))]
    outs = [p.communicate(timeout=30)[0].strip() for p in procs]
    assert sorted(outs) == ["CONFLICT", "OK"], outs
    doc = store.load(state)
    active = [k for k, v in doc["user_bindings"].items() if v.get("active")]
    assert len(active) == 1


def test_lock_released_after_holder_exits(tmp_path, monkeypatch):
    import garden_state as store
    keyfile = tmp_path / "master.key"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(keyfile))
    state = tmp_path / "state.json"
    assert store.save({"manual_only": True, "bound": {}, "user_bindings": {}}, state)
    env = dict(os.environ, AMBRACE_CREDENTIAL_KEY_FILE=str(keyfile),
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    code = textwrap.dedent('''
        import pathlib, sys
        sys.path.insert(0, %r)
        sys.path.insert(0, %r)
        import garden_state as store
        with store._state_lock(pathlib.Path(%r)):
            print("HELD", flush=True)
    ''' % (str(BACKEND), str(PLUGIN), str(state)))
    p = subprocess.run([sys.executable, "-c", code], env=env, timeout=30,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert "HELD" in p.stdout
    ok, _ = store.update_state(lambda d: d, path=state)
    assert ok is True


def test_other_process_persists_observe_epoch(tmp_path, monkeypatch):
    import garden_state as store
    keyfile = tmp_path / "master.key"
    monkeypatch.setenv("AMBRACE_CREDENTIAL_KEY_FILE", str(keyfile))
    state = tmp_path / "state.json"
    assert store.save({"manual_only": True, "bound": {}, "user_bindings": {}}, state)
    before = int(store.load(state).get("observe_epoch") or 0)
    env = dict(os.environ, AMBRACE_CREDENTIAL_KEY_FILE=str(keyfile),
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    code = textwrap.dedent('''
        import pathlib, sys
        sys.path.insert(0, %r)
        sys.path.insert(0, %r)
        import garden_state as store
        store.STATE_PATH = pathlib.Path(%r)
        def m(doc):
            doc["observe_epoch"] = int(doc.get("observe_epoch") or 0) + 1
            return doc
        print(store.update_state(m)[0])
    ''' % (str(BACKEND), str(PLUGIN), str(state)))
    p = subprocess.run([sys.executable, "-c", code], env=env, timeout=30,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert "True" in p.stdout
    assert int(store.load(state).get("observe_epoch") or 0) == before + 1
