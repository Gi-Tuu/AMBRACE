# -*- coding: utf-8 -*-
"""M3 真实插件加载 + tick/context_inject 接线（只替换网络/模型/状态边界）。"""
import asyncio
import pathlib
import sys

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "garden_forum"
sys.path.insert(0, str(PLUGIN))
import garden_policy as policy
BASE = "http://127.0.0.1:8080"


@pytest.fixture(scope="module")
def mod():
    from app.plugins import plugin_store, registry
    old = registry._loaded.get("garden_forum")
    old_enabled = registry._enabled.get("garden_forum")
    old_module = sys.modules.get("ai_plugin_garden_forum")
    old_path = list(sys.path)
    info = plugin_store.load_plugin_dir(PLUGIN)
    assert info and info["name"] == "garden_forum"
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


def base_doc():
    return {
        "base_url": "http://127.0.0.1:8080",
        "partner_key": "PK_SYNTHETIC_ONLY",
        "bound": {"3": {"api_key": "AK_SYNTHETIC_ONLY", "name": "合成小叶",
                        "user_id": 1, "base_url": "http://127.0.0.1:8080"}},
        "next_at": {}, "digest": {}, "posts": {}, "pending": {},
        "_credential_meta": {"ok": True, "crypto": True, "decrypt_failed": 0, "plaintext": []},
    }


class FakeGF:
    calls = []
    state_data = {}
    reconcile_result = {"disabled": False}

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base
        self.key = key

    def reconcile(self, partner_key, local_ids):
        self.__class__.calls.append("reconcile")
        return dict(self.__class__.reconcile_result)

    def state(self, limit=10):
        self.__class__.calls.append("state")
        return dict(self.state_data)

    def say(self, act, **kw):
        self.__class__.calls.append((act, kw))
        return {"ok": True}


@pytest.fixture()
def wired(mod, monkeypatch):
    mod._lock = asyncio.Lock()
    mod._reconciled = True
    mod._rng.seed(13)
    mod._host_guard.clear()
    FakeGF.calls = []
    FakeGF.reconcile_result = {"disabled": False}
    FakeGF.state_data = {"circles": [{"slug": "watering", "name": "浇水"}],
                         "replies_to_me": [], "unread_dm": [], "unanswered": [],
                         "digest_zh": "", "next_check_in_sec": 900}
    doc = base_doc()
    monkeypatch.setattr(mod.store, "load", lambda: doc)
    monkeypatch.setattr(mod.store, "save", lambda d: True)
    monkeypatch.setattr(mod, "GardenForum", FakeGF)
    async def fake_dnd(cid):
        return False
    monkeypatch.setattr(mod, "_in_dnd", fake_dnd)
    return mod, doc, FakeGF


def run_tick(mod, cfg, monkeypatch):
    monkeypatch.setattr(mod.sdk, "get_config", lambda: cfg)
    asyncio.run(mod.on_tick({"character_id": 3, "user_id": 1}))


def test_real_loader_registers_both_hooks(mod):
    hooks = mod.sdk  # loaded plugin registered via real SDK
    assert hooks is not None
    from app.plugins import registry
    got = registry._loaded["garden_forum"]["hooks"]
    assert "schedule_tick" in got and "context_inject" in got


def test_tick_off_sends_nothing(wired, monkeypatch):
    mod, doc, gf = wired
    run_tick(mod, {"garden_forum_on": False}, monkeypatch)
    assert [c[0] for c in gf.calls if c != "state"] == []


def test_tick_no_gates_reads_state_but_no_say(wired, monkeypatch):
    mod, doc, gf = wired
    run_tick(mod, {"garden_forum_on": True, "garden_forum_allow_llm": False,
                   "garden_forum_allow_write": False,
                   "public_profiles": {"3": {"name": "合成小叶"}}}, monkeypatch)
    assert [c[0] for c in gf.calls if c != "state"] == []


def test_tick_write_gate_reads_post_but_no_model(wired, monkeypatch):
    mod, doc, gf = wired
    gf.state_data["unanswered"] = [{"id": 7}]
    calls = []
    async def fake_chat(**kw):
        calls.append(kw)
        return "不会用到的正文"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    run_tick(mod, {"garden_forum_on": True, "garden_forum_allow_llm": False,
                   "garden_forum_allow_write": True, "read_posts_per_beat": 1,
                   "public_profiles": {"3": {"name": "合成小叶"}}}, monkeypatch)
    assert [c[0] for c in gf.calls if c != "state"] == ["act.read_post"]
    assert calls == []


def test_tick_comment_goes_through_pending(wired, monkeypatch):
    mod, doc, gf = wired
    gf.state_data["replies_to_me"] = [{"id": 11, "post_id": 7, "body": "土表干了"}]
    async def fake_chat(**kw):
        return "我也这么觉得"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    run_tick(mod, {"garden_forum_on": True, "garden_forum_allow_llm": True,
                   "garden_forum_allow_write": True,
                   "public_profiles": {"3": {"name": "合成小叶"}}}, monkeypatch)
    writes = [c for c in gf.calls if c != "state"]
    assert writes and writes[0][0] == "act.comment"
    assert writes[0][1]["parent_id"] == 11
    assert doc["pending"][next(iter(doc["pending"]))]["state"] == "success"


def test_tick_post_uses_allowed_circle(wired, monkeypatch):
    mod, doc, gf = wired
    async def fake_chat(**kw):
        return "陶盆的水量\n确实蒸发得快一些。"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    run_tick(mod, {"garden_forum_on": True, "garden_forum_allow_llm": True,
                   "garden_forum_allow_write": True, "garden_forum_daily_post": 2,
                   "garden_forum_circles": ["watering"],
                   "public_profiles": {"3": {"name": "合成小叶", "circles": ["watering"]}}}, monkeypatch)
    writes = [c for c in gf.calls if c != "state"]
    assert writes and writes[0][0] == "act.post"
    assert writes[0][1]["circle"] == "watering"


def test_tick_post_without_allowed_circle_does_not_call_model(wired, monkeypatch):
    mod, doc, gf = wired
    called = []
    async def fake_chat(**kw):
        called.append(1)
        return "标题够长\n正文够长"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    run_tick(mod, {"garden_forum_on": True, "garden_forum_allow_llm": True,
                   "garden_forum_allow_write": True, "garden_forum_daily_post": 2,
                   "garden_forum_circles": [],
                   "public_profiles": {"3": {"name": "合成小叶", "circles": []}}}, monkeypatch)
    assert [c[0] for c in gf.calls if c != "state"] == []
    assert called == []


def test_missing_public_profile_blocks_model_and_write(wired, monkeypatch):
    mod, doc, gf = wired
    gf.state_data["replies_to_me"] = [{"id": 11, "post_id": 7, "body": "x"}]
    called = []
    async def fake_chat(**kw):
        called.append(1)
        return "好"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    run_tick(mod, {"garden_forum_on": True, "garden_forum_allow_llm": True,
                   "garden_forum_allow_write": True, "public_profiles": {}}, monkeypatch)
    assert [c[0] for c in gf.calls if c != "state"] == []
    assert called == []


def test_one_model_call_per_beat_even_with_daily_post(wired, monkeypatch):
    mod, doc, gf = wired
    gf.state_data["replies_to_me"] = [{"id": 11, "post_id": 7, "body": "x"}]
    calls = []
    async def fake_chat(**kw):
        calls.append(1)
        return "我也这么觉得"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    run_tick(mod, {"garden_forum_on": True, "garden_forum_allow_llm": True,
                   "garden_forum_allow_write": True, "garden_forum_daily_post": 5,
                   "garden_forum_circles": ["watering"],
                   "public_profiles": {"3": {"name": "合成小叶", "circles": ["watering"]}}}, monkeypatch)
    assert len(calls) == 1
    assert [c[0] for c in gf.calls if c != "state"] == ["act.comment"]


def test_context_inject_only_matching_user(wired, monkeypatch):
    mod, doc, gf = wired
    doc["digest"] = {"3": {"text": "近 7 天你读了 4 篇帖。", "at": __import__("time").time()}}
    monkeypatch.setattr(mod.sdk, "get_config", lambda: {"garden_forum_on": True, "garden_forum_inject": True,
                                  "garden_forum_url": "http://127.0.0.1:8080"})
    msgs = []
    asyncio.run(mod.on_inject({"character_id": 3, "user_id": 1, "context_messages": msgs}))
    assert len(msgs) == 1 and "花园论坛" in msgs[0]["content"]
    msgs2 = []
    asyncio.run(mod.on_inject({"character_id": 3, "user_id": 2, "context_messages": msgs2}))
    assert msgs2 == []


def test_context_inject_empty_digest_clears(wired, monkeypatch):
    mod, doc, gf = wired
    doc["digest"] = {"3": {"text": "", "at": 1}}
    monkeypatch.setattr(mod.sdk, "get_config", lambda: {"garden_forum_on": True, "garden_forum_inject": True,
                                  "garden_forum_url": "http://127.0.0.1:8080"})
    msgs = []
    asyncio.run(mod.on_inject({"character_id": 3, "user_id": 1, "context_messages": msgs}))
    assert msgs == []


# ─────────────── 真实 registry→SDK→loopback HTTP 闭环 ───────────────
import http.server
import json as _json
import threading


class _Stub:
    def __init__(self, say_mode="ok"):
        self.records = []
        self.say_mode = say_mode
        self.entered = threading.Event()
        self.release = threading.Event()
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                return
            def _reply(self, obj, code=200):
                data = _json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = _json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                outer.records.append({"path": self.path,
                                      "key": self.headers.get("X-Agent-Key"),
                                      "body": body})
                if self.path.endswith("/api/host/state"):
                    return self._reply({"circles": [{"slug": "watering", "name": "浇水"}],
                                        "unanswered": [], "replies_to_me": [],
                                        "unread_dm": [], "digest_zh": "",
                                        "next_check_in_sec": 900})
                if self.path.endswith("/api/host/say"):
                    if outer.say_mode == "500":
                        return self._reply({"error": {"code": "internal_error",
                                                      "message_zh": "boom"}}, 500)
                    if outer.say_mode == "drop":
                        self.connection.close()
                        return
                    if outer.say_mode == "block":
                        outer.entered.set()
                        outer.release.wait(5)
                    return self._reply({"ok": True})
                return self._reply({"error": {"code": "not_found"}}, 404)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(3)


def _http_doc(base):
    return {"base_url": base, "partner_key": "SYNTH_PK",
            "bound": {"3": {"api_key": "SYNTH_AK", "name": "Synthetic",
                            "user_id": 1, "base_url": base}},
            "pending": {}, "next_at": {}, "digest": {}, "posts": {},
            "_credential_meta": {"ok": True, "crypto": True, "decrypt_failed": 0,
                                 "plaintext": []}}


def _wire_http(mod, monkeypatch, base, doc, cfg):
    from app.plugins import registry
    monkeypatch.setattr(mod, "_lock", asyncio.Lock())
    monkeypatch.setattr(mod, "_reconciled", True)
    monkeypatch.setattr(mod, "_in_dnd", lambda *a, **k: _false())
    monkeypatch.setattr(mod.store, "load", lambda: doc)
    monkeypatch.setattr(mod.store, "save", lambda d: True)
    monkeypatch.setattr(mod.sdk, "get_config", lambda: cfg)
    monkeypatch.setattr(mod.sdk, "log", lambda *a, **k: None)
    monkeypatch.setitem(registry._enabled, "garden_forum", True)


async def _false():
    return False


def test_http_registry_to_real_sdk_comment_fields(mod, monkeypatch):
    stub = _Stub()
    try:
        base = "http://127.0.0.1:%d" % stub.port
        doc = _http_doc(base)
        cfg = {"garden_forum_on": True, "garden_forum_url": base,
               "garden_forum_allow_llm": True, "garden_forum_allow_write": True,
               "read_posts_per_beat": 0, "garden_forum_daily_post": 0,
               "public_profiles": {"3": {"name": "Synthetic", "circles": ["watering"]}},
               "garden_forum_circles": ["watering"]}
        _wire_http(mod, monkeypatch, base, doc, cfg)
        stub_records = stub.records
        async def fake_chat(**kw):
            return "我也这么觉得"
        import app.agent.llm_client as llm
        monkeypatch.setattr(llm, "chat_completion", fake_chat)
        # 重新绑定 state handler：直接给 replies_to_me
        class H2(stub.httpd.RequestHandlerClass):
            def do_POST(self2):
                n = int(self2.headers.get("Content-Length") or 0)
                body = _json.loads(self2.rfile.read(n).decode("utf-8") or "{}")
                stub_records.append({"path": self2.path,
                                     "key": self2.headers.get("X-Agent-Key"),
                                     "body": body})
                if self2.path.endswith("/api/host/state"):
                    payload = {"circles": [{"slug": "watering", "name": "浇水"}],
                               "unanswered": [], "replies_to_me": [{"id": 11, "post_id": 7,
                                                                    "body": "土表干了"}],
                               "unread_dm": [], "digest_zh": "", "next_check_in_sec": 900}
                    return self2._reply(payload)
                if self2.path.endswith("/api/host/say"):
                    return self2._reply({"ok": True})
                return self2._reply({"error": {"code": "not_found"}}, 404)
        stub.httpd.RequestHandlerClass = H2
        from app.plugins import registry
        asyncio.run(registry.run_hook("schedule_tick", {}, user_id=1))
        state = [r for r in stub_records if r["path"].endswith("/api/host/state")]
        say = [r for r in stub_records if r["path"].endswith("/api/host/say")]
        assert state and say
        assert state[0]["key"] == "SYNTH_AK"
        assert say[0]["body"]["act"] == "act.comment"
        assert say[0]["body"]["post_id"] == 7 and say[0]["body"]["parent_id"] == 11
        assert say[0]["body"]["text"] == "我也这么觉得"
    finally:
        stub.close()


def test_http_registry_post_circle_from_profile_intersection(mod, monkeypatch):
    stub = _Stub()
    try:
        base = "http://127.0.0.1:%d" % stub.port
        doc = _http_doc(base)
        cfg = {"garden_forum_on": True, "garden_forum_url": base,
               "garden_forum_allow_llm": True, "garden_forum_allow_write": True,
               "read_posts_per_beat": 0, "garden_forum_daily_post": 1,
               "public_profiles": {"3": {"name": "Synthetic", "circles": ["watering"]}},
               "garden_forum_circles": ["watering"]}
        _wire_http(mod, monkeypatch, base, doc, cfg)
        async def fake_chat(**kw):
            return "陶盆的水量\n确实蒸发得快一些。"
        import app.agent.llm_client as llm
        monkeypatch.setattr(llm, "chat_completion", fake_chat)
        from app.plugins import registry
        asyncio.run(registry.run_hook("schedule_tick", {}, user_id=1))
        say = [r for r in stub.records if r["path"].endswith("/api/host/say")]
        assert say and say[0]["body"]["act"] == "act.post"
        assert say[0]["body"]["circle"] == "watering"
    finally:
        stub.close()


def test_real_sdk_500_custom_code_is_unknown_and_not_retried(mod, monkeypatch):
    stub = _Stub(say_mode="500")
    try:
        base = "http://127.0.0.1:%d" % stub.port
        doc = _http_doc(base)
        monkeypatch.setattr(mod.store, "save", lambda d: True)
        gf = mod.GardenForum(base, "SYNTH_AK", timeout=3)
        calls = []
        def send():
            calls.append(1)
            return gf.say("act.comment", text="x", post_id=7, parent_id=11, raises=False)
        pid = "http500-action"
        asyncio.run(mod._send_pending(gf, doc, "3", pid, "comment", 1, send))
        r2 = asyncio.run(mod._send_pending(gf, doc, "3", pid, "comment", 2, send))
        assert len(calls) == 1
        assert doc["pending"][pid]["state"] == "unknown"
        assert r2["code"] == "pending_unknown"
    finally:
        stub.close()


def test_real_sdk_dropped_response_unknown_and_not_retried(mod, monkeypatch):
    stub = _Stub(say_mode="drop")
    try:
        base = "http://127.0.0.1:%d" % stub.port
        doc = _http_doc(base)
        monkeypatch.setattr(mod.store, "save", lambda d: True)
        gf = mod.GardenForum(base, "SYNTH_AK", timeout=3)
        calls = []
        def send():
            calls.append(1)
            return gf.say("act.comment", text="x", post_id=7, parent_id=11, raises=False)
        pid = "dropped-action"
        asyncio.run(mod._send_pending(gf, doc, "3", pid, "comment", 1, send))
        asyncio.run(mod._send_pending(gf, doc, "3", pid, "comment", 2, send))
        assert len(calls) == 1
        assert doc["pending"][pid]["state"] == "unknown"
    finally:
        stub.close()


def test_real_sdk_cancel_marks_unknown_without_resend(mod, monkeypatch):
    stub = _Stub(say_mode="block")
    try:
        base = "http://127.0.0.1:%d" % stub.port
        doc = _http_doc(base)
        monkeypatch.setattr(mod.store, "save", lambda d: True)
        gf = mod.GardenForum(base, "SYNTH_AK", timeout=5)
        calls = []
        def send():
            calls.append(1)
            return gf.say("act.comment", text="x", post_id=7, parent_id=11, raises=False)
        pid = "cancel-action"

        async def run():
            task = asyncio.create_task(
                mod._send_pending(gf, doc, "3", pid, "comment", 1, send))
            assert await asyncio.to_thread(stub.entered.wait, 5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        stub.release.set()
        assert doc["pending"][pid]["state"] == "unknown"
        r = asyncio.run(mod._send_pending(gf, doc, "3", pid, "comment", 2, send))
        assert r["code"] == "pending_unknown"
        assert len(calls) == 1
    finally:
        stub.close()


def test_dm_uses_message_id_so_same_sender_new_message_sends(wired):
    mod, doc, gf = wired
    g = gf("http://127.0.0.1:8080")
    cfg = {"garden_forum_allow_llm": True, "garden_forum_allow_write": True,
           "public_profiles": {"3": {"name": "Synthetic"}}}
    asyncio.run(mod._say(g, doc, "3", "dm",
                         {"id": 11, "from_agent": "same-peer"}, "first", 1, cfg))
    asyncio.run(mod._say(g, doc, "3", "dm",
                         {"id": 12, "from_agent": "same-peer"}, "second", 2, cfg))
    assert [c[0] for c in gf.calls if c != "state"] == ["act.message", "act.message"]


def test_two_post_intents_same_day_each_generate_and_send(wired, monkeypatch):
    mod, doc, gf = wired
    g = gf("http://127.0.0.1:8080")
    cfg = {"garden_forum_allow_llm": True, "garden_forum_allow_write": True,
           "garden_forum_daily_post": 3, "garden_forum_circles": ["watering"],
           "public_profiles": {"3": {"name": "Synthetic", "circles": ["watering"]}}}
    texts = iter(["First title here\nFirst body here.",
                  "Second title here\nSecond body here."])
    async def fake_chat(**kw):
        return next(texts)
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    st = {"circles": [{"slug": "watering"}]}
    asyncio.run(mod._try_post(g, doc, "3", doc["bound"]["3"], cfg, 1, st, "2026-10-27",
                              cfg["public_profiles"]["3"]))
    asyncio.run(mod._try_post(g, doc, "3", doc["bound"]["3"], cfg, 2, st, "2026-10-27",
                              cfg["public_profiles"]["3"]))
    assert [c[0] for c in gf.calls if c != "state"] == ["act.post", "act.post"]


def test_generation_pre_save_failure_does_not_call_model_twice(wired, monkeypatch):
    mod, doc, gf = wired
    gf.state_data["replies_to_me"] = [{"id": 11, "post_id": 7, "body": "Synthetic"}]
    calls = []
    async def fake_chat(**kw):
        calls.append(1)
        return "无"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    monkeypatch.setattr(mod.store, "save", lambda d: False)
    cfg = {"garden_forum_on": True, "garden_forum_allow_llm": True,
           "garden_forum_allow_write": True,
           "garden_forum_max_generations_per_day": 1,
           "public_profiles": {"3": {"name": "Synthetic"}}}
    run_tick(mod, cfg, monkeypatch)
    run_tick(mod, cfg, monkeypatch)
    assert len(calls) <= 1


def test_quota_refusal_does_not_consume_dm_then_next_day_retries(wired, monkeypatch):
    mod, doc, gf = wired
    gf.state_data["unread_dm"] = [{"id": 11, "from_agent": "peer", "body": "question"}]
    calls = []
    async def fake_chat(**kw):
        calls.append(1)
        return "answer"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    cfg = {"garden_forum_allow_llm": True, "garden_forum_allow_write": True,
           "garden_forum_max_generations_per_day": 1,
           "public_profiles": {"3": {"name": "Synthetic"}}}
    now = 1000.0
    policy.usage_begin(doc, now)["day_gen"] = 1
    asyncio.run(mod._one_character("http://127.0.0.1:8080", doc, "3", cfg, now, 1200))
    assert not calls
    assert "11" not in __import__("garden_state").seen_dms(doc, "3", now)
    asyncio.run(mod._one_character("http://127.0.0.1:8080", doc, "3", cfg,
                                   now + 86400, 1200))
    assert calls == [1]
    assert [c[0] for c in gf.calls].count("act.message") == 1


def test_write_gate_off_does_not_consume_comment(wired):
    mod, doc, gf = wired
    gf.state_data["replies_to_me"] = [{"id": 11, "post_id": 7, "body": "x"}]
    cfg = {"garden_forum_allow_llm": True, "garden_forum_allow_write": False,
           "public_profiles": {"3": {"name": "Synthetic"}}}
    asyncio.run(mod._one_character("http://127.0.0.1:8080", doc, "3", cfg, 1000, 1200))
    assert "11" not in __import__("garden_state").seen_replies(doc, "3", 1000)


def test_disabled_save_failure_keeps_next_tick_closed(wired, monkeypatch):
    mod, doc, gf = wired
    persisted = __import__("copy").deepcopy(doc)
    gf.reconcile_result = {"disabled": True}
    monkeypatch.setattr(mod, "_reconciled", False)
    monkeypatch.setattr(mod, "_host_guard", {})
    monkeypatch.setattr(mod.store, "load", lambda: __import__("copy").deepcopy(persisted))
    monkeypatch.setattr(mod.store, "save", lambda d: False)
    monkeypatch.setattr(mod.sdk, "get_config", lambda: {"garden_forum_on": True,
                                                        "garden_forum_url": BASE})
    asyncio.run(mod.on_tick({}))
    asyncio.run(mod.on_tick({}))
    assert gf.calls.count("reconcile") == 1
    assert "state" not in gf.calls


def test_disabled_expiry_reconcile_success_allows_state(wired, monkeypatch):
    mod, doc, gf = wired
    clock = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: clock[0])
    monkeypatch.setattr(mod, "_reconciled", False)
    monkeypatch.setattr(mod, "_host_guard", {})
    monkeypatch.setattr(mod.store, "save", lambda d: True)
    monkeypatch.setattr(mod.sdk, "get_config", lambda: {"garden_forum_on": True,
                                                        "garden_forum_url": BASE})
    gf.reconcile_result = {"disabled": True}
    asyncio.run(mod.on_tick({}))
    assert doc["host_state"]["state"] == "disabled"
    clock[0] = doc["host_state"]["next_at"] + 1
    gf.reconcile_result = {"disabled": False}
    asyncio.run(mod.on_tick({}))
    assert "reconcile" in gf.calls and "state" in gf.calls


def test_unsaved_host_guard_expiry_rechecks_before_actor_state(wired, monkeypatch):
    import copy
    mod, doc, gf = wired
    persisted = copy.deepcopy(doc)
    clock = [1000.0]
    requests = []
    def reconcile(self, *args):
        requests.append("reconcile")
        return {"disabled": True}
    def state(self, *args):
        requests.append("state")
        return dict(self.state_data)
    monkeypatch.setattr(gf, "reconcile", reconcile, raising=False)
    monkeypatch.setattr(gf, "state", state)
    monkeypatch.setattr(mod, "_host_guard", {})
    monkeypatch.setattr(mod, "_reconciled", False)
    monkeypatch.setattr(mod.time, "time", lambda: clock[0])
    monkeypatch.setattr(mod.store, "load", lambda: copy.deepcopy(persisted))
    monkeypatch.setattr(mod.store, "save", lambda value: False)
    monkeypatch.setattr(mod.sdk, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod.sdk, "get_config", lambda: {
        "garden_forum_on": True, "garden_forum_url": persisted["base_url"]})
    asyncio.run(mod.on_tick({}))
    assert requests == ["reconcile"]
    clock[0] = mod._host_guard[persisted["base_url"]]["next_at"] + 1
    asyncio.run(mod.on_tick({}))
    assert requests == ["reconcile", "reconcile"]
