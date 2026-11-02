# -*- coding: utf-8 -*-
"""花园 one-shot 失败/未知/并发：不重发、不假成功、不越过任务身份。"""
import asyncio
import pathlib
import sys

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
BASE = "http://127.0.0.1:8080"


@pytest.fixture(scope="module")
def mod():
    from app.plugins import plugin_store, registry
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
    return registry._loaded["garden_forum"]["module"]


class FakeGF:
    calls = []
    state_data = {}
    say_result = {"ok": True}

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base

    def state(self, limit=10):
        self.__class__.calls.append(("state", limit))
        return dict(self.__class__.state_data)

    def say(self, act, **kw):
        self.__class__.calls.append((act, kw))
        return dict(self.__class__.say_result)


def doc():
    return {
        "base_url": BASE, "partner_key": "PK_SYNTHETIC_ONLY",
        "bound": {"3": {"api_key": "AK_SYNTHETIC_ONLY", "name": "合成小叶",
                        "user_id": 1, "base_url": BASE}},
        "one_shot": {}, "pending": {}, "pending_tombstones": {}, "posts": {}, "usage": {},
        "_credential_meta": {"ok": True, "crypto": True, "decrypt_failed": 0, "plaintext": []},
    }


def cfg(**over):
    c = {"garden_forum_on": True, "garden_forum_allow_llm": True,
         "garden_forum_allow_write": True, "garden_forum_url": BASE,
         "public_profiles": {"3": {"name": "合成小叶", "bio": "合成公开简介"}},
         "garden_forum_max_writes_per_beat": 4, "garden_forum_max_writes_per_day": 20,
         "garden_forum_max_generations_per_beat": 1,
         "garden_forum_max_generations_per_day": 6,
         "garden_forum_daily_post": 0}
    c.update(over)
    return c


@pytest.fixture()
def wired(mod, monkeypatch):
    FakeGF.calls = []
    FakeGF.say_result = {"ok": True}
    FakeGF.state_data = {"replies_to_me": [{"id": 11, "post_id": 7, "body": "土表干了"}],
                         "unread_dm": [], "unanswered": [], "circles": [],
                         "today_threads": []}
    monkeypatch.setattr(mod, "GardenForum", FakeGF)
    monkeypatch.setattr(mod.store, "save", lambda d: True)
    return mod


def run(mod, c, d, task="task", cid=3, owner=1):
    return asyncio.run(mod.run_one_shot(cid, task, owner_user_id=owner, cfg=c, doc=d, now=1000))


def test_model_timeout_records_failure_and_retry_rejected(wired, monkeypatch):
    async def timeout(**kw):
        raise asyncio.TimeoutError()
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", timeout)
    d = doc()
    r = run(wired, cfg(), d, task="task-timeout")
    assert r["status"] == "failed" and r["code"] == "timeout"
    assert [x for x in FakeGF.calls if x[0] != "state"] == []
    assert d["one_shot"]["task-timeout"]["state"] == "failed"
    before = list(FakeGF.calls)
    r2 = run(wired, cfg(), d, task="task-timeout")
    assert r2["status"] == "rejected" and r2["code"] == "duplicate_task"
    assert FakeGF.calls == before


def test_unknown_send_is_not_resent(wired, monkeypatch):
    async def fake(**kw):
        return "一句合成回复"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake)
    FakeGF.say_result = {"ok": False, "code": "http_500"}
    d = doc()
    r = run(wired, cfg(), d, task="task-unknown")
    assert r["status"] == "unknown" and r["action"] == "act.comment"
    assert d["one_shot"]["task-unknown"]["state"] == "unknown"
    before = list(FakeGF.calls)
    r2 = run(wired, cfg(), d, task="task-unknown")
    assert r2["status"] == "rejected" and r2["code"] == "duplicate_task"
    assert FakeGF.calls == before


def test_concurrent_same_task_sends_once(wired, monkeypatch):
    calls = []
    async def fake(**kw):
        calls.append(kw)
        return "一句合成回复"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake)
    d = doc()

    async def both():
        return await asyncio.gather(
            wired.run_one_shot(3, "task-concurrent", owner_user_id=1, cfg=cfg(), doc=d, now=1000),
            wired.run_one_shot(3, "task-concurrent", owner_user_id=1, cfg=cfg(), doc=d, now=1000),
        )
    first, second = asyncio.run(both())
    states = sorted([first["status"], second["status"]])
    assert states == ["rejected", "success"]
    assert len(calls) == 1
    assert len([x for x in FakeGF.calls if x[0] == "act.comment"]) == 1


def test_daily_post_quota_zero_is_rejected_without_model(wired, monkeypatch):
    calls = []
    async def fake(**kw):
        calls.append(kw)
        return "标题\n正文"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake)
    FakeGF.state_data = {"replies_to_me": [], "unread_dm": [], "unanswered": [],
                         "circles": [{"slug": "watering", "name": "浇水"}]}
    d = doc()
    r = run(wired, cfg(garden_forum_daily_post=0), d, task="task-quota")
    assert r["status"] == "silent" and r["code"] == "no_target"
    assert calls == []


def _save_counter(mod, monkeypatch, fail_at):
    calls = {"n": 0}
    def save(doc):
        calls["n"] += 1
        return calls["n"] not in fail_at
    monkeypatch.setattr(mod.store, "save", save)
    return calls


def test_intent_save_failure_zero_network(wired, monkeypatch):
    _save_counter(wired, monkeypatch, {1})
    d = doc()
    r = run(wired, cfg(), d, task="task-intent-save")
    assert r["status"] == "rejected" and r["code"] == "intent_save_failed"
    assert FakeGF.calls == []


def test_pending_save_failure_is_unknown_and_not_sent(wired, monkeypatch):
    async def fake(**kw):
        return "一句合成回复"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake)
    _save_counter(wired, monkeypatch, {3})  # 1 intent / 2 inflight / 3 pending 落盘失败
    d = doc()
    r = run(wired, cfg(), d, task="task-pending-save")
    assert r["status"] == "unknown"
    assert [x for x in FakeGF.calls if x[0] != "state"] == []
    assert d["one_shot"]["task-pending-save"]["state"] == "unknown"


def test_request_interrupted_marks_unknown_no_retry(wired, monkeypatch):
    async def fake(**kw):
        return "一句合成回复"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake)

    def boom(self, act, **kw):
        FakeGF.calls.append((act, kw))
        raise TimeoutError("loopback timeout")
    FakeGF.say = boom
    d = doc()
    r = run(wired, cfg(), d, task="task-interrupted")
    assert r["status"] == "unknown"
    assert d["one_shot"]["task-interrupted"]["state"] == "unknown"
    sends = [x for x in FakeGF.calls if x[0] != "state"]
    assert len(sends) == 1
    before = list(FakeGF.calls)
    r2 = run(wired, cfg(), d, task="task-interrupted")
    assert r2["status"] == "rejected" and r2["code"] == "duplicate_task"
    assert FakeGF.calls == before


def test_receipt_save_failure_after_server_success_marks_unknown(wired, monkeypatch):
    async def fake(**kw):
        return "一句合成回复"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake)
    _save_counter(wired, monkeypatch, {5})  # 5 = 服务成功后的回执落盘
    d = doc()
    r = run(wired, cfg(), d, task="task-receipt-save")
    assert r["status"] == "unknown"
    assert d["one_shot"]["task-receipt-save"]["state"] == "unknown"
    assert len([x for x in FakeGF.calls if x[0] == "act.comment"]) == 1


def test_host_disabled_and_guard_blocked_no_state_or_model(wired, monkeypatch):
    calls = []
    async def fake(**kw):
        calls.append(kw)
        return "一句合成回复"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake)
    d = doc()
    d["host_state"] = {"state": "disabled", "base_url": BASE, "next_at": 999999}
    r = run(wired, cfg(), d, task="task-host-disabled")
    assert r["status"] == "rejected" and r["code"] == "host_not_usable"
    assert FakeGF.calls == [] and calls == []

    d2 = doc()
    wired._host_guard[BASE] = {"state": "error", "next_at": 999999}
    try:
        r2 = run(wired, cfg(), d2, task="task-guard")
    finally:
        wired._host_guard.pop(BASE, None)
    assert r2["status"] == "rejected" and r2["code"] == "host_guard_blocked"
    assert FakeGF.calls == [] and calls == []

    d3 = doc()
    d3["site_block_until"] = 999999
    r3 = run(wired, cfg(), d3, task="task-backoff")
    assert r3["status"] == "rejected" and r3["code"] == "site_backoff"
    assert FakeGF.calls == [] and calls == []


def test_same_source_pending_from_heartbeat_blocks_different_task(wired, monkeypatch):
    from garden_policy import pending_id
    calls = []
    async def fake(**kw):
        calls.append(kw)
        return "一句合成回复"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake)
    d = doc()
    pid = pending_id("3", "comment", "11", base_url=BASE, source="11")
    d["pending"][pid] = {"state": "success", "cid": "3", "kind": "comment", "at": 1}
    r = run(wired, cfg(), d, task="task-different")
    assert r["status"] == "rejected" and r["code"] == "duplicate_action"
    assert [x for x in FakeGF.calls if x[0] != "state"] == []
    assert calls == []


_CHILD = r"""
import asyncio, json, os, pathlib, sys
backend = pathlib.Path(sys.argv[1]); plugin = pathlib.Path(sys.argv[2])
state_path = pathlib.Path(sys.argv[3]); task = sys.argv[4]; mode = sys.argv[5]
sys.path.insert(0, str(backend)); sys.path.insert(0, str(plugin))
from app.plugins import plugin_store, registry
plugin_store.load_plugin_dir(plugin)
mod = registry._loaded["garden_forum"]["module"]
mod.store.STATE_PATH = state_path  # 隔离：显式 doc 的持久化必须写回本次临时状态
calls = {"state": 0, "say": 0}
class GF:
    def __init__(self, base, key="", name="", timeout=0): self.base = base
    def state(self, limit=10):
        calls["state"] += 1
        return {"replies_to_me": [{"id": 11, "post_id": 7, "body": "合成留言"}],
                "unread_dm": [], "unanswered": [], "circles": []}
    def say(self, act, **kw):
        calls["say"] += 1
        if mode == "kill":
            import os
            os._exit(77)  # 真实动作已发出；在回执保存前终止进程
        if mode == "receipt_fail":
            fail["armed"] = True  # 服务已接受；下一次持久化（回执）失败
            return {"ok": True}
        return {"ok": False, "code": "http_500"} if mode == "unknown" else {"ok": True}
mod.GardenForum = GF
from app.agent import llm_client
async def fake(**kw): return "合成回复"
llm_client.chat_completion = fake
doc = json.loads(state_path.read_text(encoding="utf-8"))
cfg = {"garden_forum_on": True, "garden_forum_allow_llm": True,
       "garden_forum_allow_write": True, "garden_forum_url": doc["base_url"],
       "public_profiles": {"3": {"name": "合成小叶"}},
       "garden_forum_max_writes_per_beat": 4, "garden_forum_max_writes_per_day": 20,
       "garden_forum_max_generations_per_beat": 1,
       "garden_forum_max_generations_per_day": 6, "garden_forum_daily_post": 0}
r = asyncio.run(mod.run_one_shot(3, task, owner_user_id=1, cfg=cfg, doc=doc, now=1000))
state_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
print("RESULT " + json.dumps({"result": r, "calls": calls}))
"""


def _child(tmp_path, workspace, task, mode, state):
    import json, os, subprocess, sys
    script = tmp_path / "oneshot_child.py"
    script.write_text(_CHILD, encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, str(script), str(workspace / "backend"),
         str(workspace / "plugins" / "examples" / "garden_forum"),
         str(state), task, mode],
        cwd=str(workspace / "backend"), env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = [x for x in proc.stdout.splitlines() if x.startswith("RESULT ")][-1]
    return json.loads(line[len("RESULT "):]), proc.returncode


def test_subprocess_restart_unknown_then_duplicate(tmp_path):
    import json, pathlib
    workspace = pathlib.Path(__file__).resolve().parents[2]
    state = tmp_path / "garden_state.json"
    state.write_text(json.dumps(doc(), ensure_ascii=False), encoding="utf-8")
    first, rc1 = _child(tmp_path, workspace, "task-proc-unknown", "unknown", state)
    assert first["result"]["status"] == "unknown"
    assert first["calls"] == {"state": 1, "say": 1}
    persisted = json.loads(state.read_text(encoding="utf-8"))
    assert persisted["one_shot"]["task-proc-unknown"]["state"] == "unknown"
    second, rc2 = _child(tmp_path, workspace, "task-proc-unknown", "success", state)
    assert second["result"]["status"] == "rejected"
    assert second["result"]["code"] == "duplicate_task"
    assert second["calls"] == {"state": 0, "say": 0}
    assert rc1 == 0 and rc2 == 0


_ACTUAL_CHILD = r"""
import asyncio, json, pathlib, sys
backend = pathlib.Path(sys.argv[1]); plugin = pathlib.Path(sys.argv[2])
state_path = pathlib.Path(sys.argv[3]); task = sys.argv[4]; mode = sys.argv[5]
sys.path.insert(0, str(backend)); sys.path.insert(0, str(plugin))
from app.plugins import plugin_store, registry
plugin_store.load_plugin_dir(plugin)
mod = registry._loaded["garden_forum"]["module"]
mod.store.STATE_PATH = state_path
fail = {"armed": False}
_real_save = mod.store.save
def save(doc):
    if fail["armed"]:
        fail["armed"] = False
        return False  # 模拟服务已接受动作后的回执落盘失败
    return _real_save(doc)
mod.store.save = save
if not state_path.exists():
    mod.store.save({"base_url": "http://127.0.0.1:8080",
                    "partner_key": "PK_SYNTHETIC_CHILD",
                    "bound": {"3": {"api_key": "AK_SYNTHETIC_CHILD", "name": "合成小叶",
                                    "user_id": 9701, "base_url": "http://127.0.0.1:8080"}},
                    "one_shot": {}, "pending": {}, "pending_tombstones": {}})
calls = {"state": 0, "say": 0}
class GF:
    def __init__(self, base, key="", name="", timeout=0): self.base = base
    def state(self, limit=10):
        calls["state"] += 1
        return {"replies_to_me": [{"id": 11, "post_id": 7, "body": "合成留言"}],
                "unread_dm": [], "unanswered": [], "circles": []}
    def say(self, act, **kw):
        calls["say"] += 1
        if mode == "kill":
            import os
            os._exit(77)  # 真实动作已发出；在回执保存前终止进程
        if mode == "receipt_fail":
            fail["armed"] = True  # 服务已接受；下一次持久化（回执）失败
            return {"ok": True}
        return {"ok": False, "code": "http_500"} if mode == "unknown" else {"ok": True}
mod.GardenForum = GF
from app.agent import llm_client
async def fake(**kw): return "合成回复"
llm_client.chat_completion = fake
cfg = {"garden_forum_on": True, "garden_forum_allow_llm": True,
       "garden_forum_allow_write": True, "garden_forum_url": "http://127.0.0.1:8080",
       "public_profiles": {"3": {"name": "合成小叶"}},
       "garden_forum_max_writes_per_beat": 4, "garden_forum_max_writes_per_day": 20,
       "garden_forum_max_generations_per_beat": 1,
       "garden_forum_max_generations_per_day": 6, "garden_forum_daily_post": 0}
r = asyncio.run(mod.run_one_shot(3, task, owner_user_id=9701, cfg=cfg, now=1000))
after = mod.store.load().get("one_shot", {}).get(task, {})
print("RESULT " + json.dumps({"result": r, "calls": calls, "persisted": after.get("state")}))
"""


def test_subprocess_real_state_save_unknown_then_duplicate(tmp_path):
    import json, os, subprocess, sys
    workspace = pathlib.Path(__file__).resolve().parents[2]
    state = tmp_path / "garden_real_state.json"
    script = tmp_path / "oneshot_actual_child.py"
    script.write_text(_ACTUAL_CHILD, encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"; env["PYTHONIOENCODING"] = "utf-8"
    def child(task, mode):
        proc = subprocess.run(
            [sys.executable, str(script), str(workspace / "backend"),
             str(workspace / "plugins" / "examples" / "garden_forum"),
             str(state), task, mode],
            cwd=str(workspace / "backend"), env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=120)
        assert proc.returncode == 0, proc.stderr[-2000:]
        line = [x for x in proc.stdout.splitlines() if x.startswith("RESULT ")][-1]
        return json.loads(line[len("RESULT "):]), proc.returncode
    first, rc1 = child("task-real-state", "unknown")
    assert first["result"]["status"] == "unknown"
    assert first["persisted"] == "unknown"
    assert first["calls"] == {"state": 1, "say": 1}
    # 真实插件 save() 已加密写入同一文件；第二独立进程重新 load，不手工写状态
    second, rc2 = child("task-real-state", "success")
    assert second["result"]["status"] == "rejected"
    assert second["result"]["code"] == "duplicate_task"
    assert second["calls"] == {"state": 0, "say": 0}
    assert rc1 == 0 and rc2 == 0


def test_subprocess_killed_after_send_reloads_no_resend(tmp_path):
    import json, os, subprocess, sys
    workspace = pathlib.Path(__file__).resolve().parents[2]
    state = tmp_path / "garden_kill_state.json"
    script = tmp_path / "oneshot_kill_child.py"
    script.write_text(_ACTUAL_CHILD, encoding="utf-8")
    env = os.environ.copy(); env["PYTHONUTF8"] = "1"; env["PYTHONIOENCODING"] = "utf-8"

    def run_child(task, mode):
        return subprocess.run(
            [sys.executable, str(script), str(workspace / "backend"),
             str(workspace / "plugins" / "examples" / "garden_forum"),
             str(state), task, mode],
            cwd=str(workspace / "backend"), env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=120)

    killed = run_child("task-killed", "kill")
    assert killed.returncode == 77, (killed.returncode, killed.stderr[-1000:])
    # 新进程重载真实加密状态；同 task 先恢复 inflight→unknown，再拒重
    proc2 = run_child("task-killed", "success")
    assert proc2.returncode == 0, proc2.stderr[-2000:]
    line2 = [x for x in proc2.stdout.splitlines() if x.startswith("RESULT ")][-1]
    got2 = json.loads(line2[len("RESULT "):])
    assert got2["result"]["status"] == "rejected"
    assert got2["result"]["code"] == "duplicate_task"
    assert got2["calls"] == {"state": 0, "say": 0}
    assert got2["persisted"] == "unknown"
    # 同源不同 task：pending 已恢复 unknown，模型前 duplicate_action，不重发
    proc3 = run_child("task-killed-other", "success")
    assert proc3.returncode == 0, proc3.stderr[-2000:]
    line3 = [x for x in proc3.stdout.splitlines() if x.startswith("RESULT ")][-1]
    got3 = json.loads(line3[len("RESULT "):])
    # 同源已 ask 过/动作 pending 已恢复 unknown：不再调模型/不再发；可为 silent 或 duplicate_action
    assert got3["result"]["status"] in ("silent", "rejected")
    assert got3["calls"] == {"state": 1, "say": 0}



def test_subprocess_receipt_failure_reloads_no_resend(tmp_path):
    import json, os, subprocess, sys
    workspace = pathlib.Path(__file__).resolve().parents[2]
    state = tmp_path / "garden_receipt_state.json"
    script = tmp_path / "oneshot_receipt_child.py"
    script.write_text(_ACTUAL_CHILD, encoding="utf-8")
    env = os.environ.copy(); env["PYTHONUTF8"] = "1"; env["PYTHONIOENCODING"] = "utf-8"

    def run_child(task, mode):
        proc = subprocess.run(
            [sys.executable, str(script), str(workspace / "backend"),
             str(workspace / "plugins" / "examples" / "garden_forum"),
             str(state), task, mode],
            cwd=str(workspace / "backend"), env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=120)
        assert proc.returncode == 0, proc.stderr[-2000:]
        line = [x for x in proc.stdout.splitlines() if x.startswith("RESULT ")][-1]
        return json.loads(line[len("RESULT "):])

    first = run_child("task-receipt", "receipt_fail")
    assert first["result"]["status"] == "unknown"
    assert first["result"]["code"] == "receipt_save_failed"
    assert first["calls"] == {"state": 1, "say": 1}
    assert first["persisted"] == "unknown"
    second = run_child("task-receipt", "success")
    assert second["result"]["status"] == "rejected"
    assert second["result"]["code"] == "duplicate_task"
    assert second["calls"] == {"state": 0, "say": 0}
    third = run_child("task-receipt-other", "success")
    assert third["result"]["status"] in ("silent", "rejected")
    assert third["calls"] == {"state": 1, "say": 0}
