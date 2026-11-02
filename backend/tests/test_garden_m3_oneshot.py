# -*- coding: utf-8 -*-
"""花园 one-shot：默认关、缺前置零请求、沉默、一次成功、幂等重复任务。"""
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
    module = registry._loaded["garden_forum"]["module"]
    yield module


class FakeGF:
    calls = []
    state_data = {}
    say_result = {"ok": True}

    def __init__(self, base, key="", name="", timeout=0):
        self.base = base
        self.key = key

    def state(self, limit=10):
        self.__class__.calls.append(("state", limit))
        return dict(self.__class__.state_data)

    def say(self, act, **kw):
        self.__class__.calls.append((act, kw))
        return dict(self.__class__.say_result)


def doc():
    return {
        "base_url": BASE,
        "partner_key": "PK_SYNTHETIC_ONLY",
        "bound": {"3": {"api_key": "AK_SYNTHETIC_ONLY", "name": "合成小叶",
                        "user_id": 1, "base_url": BASE}},
        "one_shot": {}, "pending": {}, "pending_tombstones": {}, "posts": {}, "usage": {},
        "_credential_meta": {"ok": True, "crypto": True, "decrypt_failed": 0, "plaintext": []},
    }


def cfg(**over):
    c = {
        "garden_forum_on": True, "garden_forum_allow_llm": True,
        "garden_forum_allow_write": True,
        "garden_forum_url": BASE,
        "public_profiles": {"3": {"name": "合成小叶", "bio": "合成公开简介",
                                   "circles": ["watering"]}},
        "garden_forum_circles": ["watering"],
        "garden_forum_max_writes_per_beat": 4, "garden_forum_max_writes_per_day": 20,
        "garden_forum_max_generations_per_beat": 1,
        "garden_forum_max_generations_per_day": 6,
        "garden_forum_daily_post": 0,
    }
    c.update(over)
    return c


@pytest.fixture()
def wired(mod, monkeypatch):
    FakeGF.calls = []
    FakeGF.say_result = {"ok": True}
    FakeGF.state_data = {"replies_to_me": [], "unread_dm": [], "unanswered": [],
                         "circles": [{"slug": "watering", "name": "浇水"}],
                         "today_threads": [], "next_check_in_sec": 900}
    monkeypatch.setattr(mod, "GardenForum", FakeGF)
    monkeypatch.setattr(mod.store, "save", lambda d: True)
    return mod


def run(mod, c, d, task="task-1", cid=3, owner=1, now=1000):
    return asyncio.run(mod.run_one_shot(cid, task, owner_user_id=owner,
                                        cfg=c, doc=d, now=now))


def test_disabled_and_unbound_make_no_request(wired):
    d = doc()
    r = run(wired, cfg(garden_forum_on=False), d)
    assert r["status"] == "rejected" and r["code"] == "disabled"
    assert FakeGF.calls == []
    d2 = doc()
    r2 = run(wired, cfg(), d2, owner=999)
    assert r2["status"] == "rejected" and r2["code"] == "character_not_bound"
    assert FakeGF.calls == []


def test_missing_public_profile_no_network(wired):
    d = doc()
    r = run(wired, cfg(public_profiles={}), d)
    assert r["status"] == "rejected" and r["code"] == "public_profile_missing"
    assert FakeGF.calls == []


def test_silence_is_success_and_duplicate_task_rejected(wired, monkeypatch):
    FakeGF.state_data["replies_to_me"] = [{"id": 11, "post_id": 7, "body": "土表干了"}]
    calls = []
    async def fake_chat(**kw):
        calls.append(kw)
        return "无"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    d = doc()
    r = run(wired, cfg(), d)
    assert r["status"] == "silent" and r["code"] == "explicit_silence"
    assert len([x for x in FakeGF.calls if x[0] == "state"]) == 1
    assert len(calls) == 1
    assert [x for x in FakeGF.calls if x[0] != "state"] == []
    assert d["one_shot"]["task-1"]["state"] == "silent"
    before = list(FakeGF.calls)
    r2 = run(wired, cfg(), d)
    assert r2["status"] == "rejected" and r2["code"] == "duplicate_task"
    assert FakeGF.calls == before and len(calls) == 1


def test_success_comment_one_action(wired, monkeypatch):
    FakeGF.state_data["replies_to_me"] = [{"id": 11, "post_id": 7, "body": "土表干了"}]
    calls = []
    async def fake_chat(**kw):
        calls.append(kw)
        return "我也这么觉得"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    d = doc()
    r = run(wired, cfg(), d, task="task-ok")
    assert r["status"] == "success" and r["action"] == "act.comment"
    assert len([x for x in FakeGF.calls if x[0] == "state"]) == 1
    assert len(calls) == 1
    writes = [x for x in FakeGF.calls if x[0] != "state"]
    assert len(writes) == 1 and writes[0][0] == "act.comment"
    assert writes[0][1]["parent_id"] == 11
    assert d["one_shot"]["task-ok"]["state"] == "success"


def test_post_requires_allowed_circle(wired, monkeypatch):
    calls = []
    async def fake_chat(**kw):
        calls.append(kw)
        return "合成标题\n合成正文"
    import app.agent.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    d = doc()
    # 有 daily_post 但没有 circle 交集 => 拒绝且不调模型
    r = run(wired, cfg(garden_forum_daily_post=1, garden_forum_circles=[]), d, task="task-nocircle")
    assert r["status"] == "rejected" and r["code"] == "no_allowed_circle"
    assert calls == []
    assert [x for x in FakeGF.calls if x[0] != "state"] == []
    # 显式 circle 交集 => 一次 post
    FakeGF.calls = []
    d2 = doc()
    r2 = run(wired, cfg(garden_forum_daily_post=1), d2, task="task-post")
    assert r2["status"] == "success" and r2["action"] == "act.post"
    assert len([x for x in FakeGF.calls if x[0] == "state"]) == 1
    assert len(calls) == 1
    write = [x for x in FakeGF.calls if x[0] == "act.post"][-1]
    assert write[1]["circle"] == "watering"
