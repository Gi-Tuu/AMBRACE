# -*- coding: utf-8 -*-
"""Ariadne 模块 B：按需二跳联想检索单测。

- actions 层：extract_recall 提取/剥离（中英文括号/无闭合/无标记）、strip_actions、parse_actions；
- loop.run_recall_loop：flag 关=只剥离（零检索零生成）；flag 开+命中=注入【补充记忆】+再生成 1 次
  +触发复习+trace 步骤；无命中=用首轮正文；gate 关=剥离；「时间=YYYY-MM；查询」语法透传时间路。
全部 monkeypatch 接缝（search_memories / generate_response / reinforce_memories / format_memory_line）。
"""
import asyncio

import pytest

from app.agent import actions as actions_mod
from app.agent.actions import extract_recall, parse_actions, strip_actions


# ────────────────────────── actions 层 ──────────────────────────

def test_extract_recall_中英括号与无闭合():
    c, q = extract_recall("[RECALL]青岛旅游[/RECALL]正文")
    assert q == "青岛旅游" and c == "正文"
    c, q = extract_recall("【RECALL】搬家公司电话【/RECALL】后面")
    assert q == "搬家公司电话" and c == "后面"
    c, q = extract_recall("[RECALL]没有闭合")
    assert q == "没有闭合" and c == ""
    assert extract_recall("普通文本，无标记") == ("普通文本，无标记", None)
    assert extract_recall("") == ("", None)


def test_recall_进_strip与parse():
    t = "前【RECALL]查询词[/RECALL]后"
    assert "[RECALL" not in strip_actions(t)
    acts = parse_actions("x[RECALL]青岛[/RECALL]y")
    assert [a.to_step() for a in acts] == [{"action": "RECALL", "query": "青岛"}]


# ────────────────────────── run_recall_loop ──────────────────────────

@pytest.fixture()
def _flag_off(monkeypatch):
    from app.agent import loop
    monkeypatch.setattr(loop, "AGENT_FLAGS", {**loop.AGENT_FLAGS, "memory_recall_second_hop": False})


@pytest.fixture()
def _flag_on(monkeypatch):
    from app.agent import loop
    monkeypatch.setattr(loop, "AGENT_FLAGS", {**loop.AGENT_FLAGS, "memory_recall_second_hop": True})


def _state(text):
    return {"ai_response": text, "context_messages": [{"role": "system", "content": "base"}]}


def test_flag关_只剥离_零检索零生成(monkeypatch, _flag_off):
    from app.agent import loop
    called = {"search": 0, "regen": 0}
    monkeypatch.setattr("app.memory.search_memories", lambda *a, **k: called.__setitem__("search", called["search"] + 1))
    monkeypatch.setattr("app.agent.nodes.generate_response", lambda *a, **k: called.__setitem__("regen", called["regen"] + 1))
    st, steps = asyncio.run(loop.run_recall_loop(_state("好[RECALL]青岛[/RECALL]"), user_id=1, character_id=7))
    assert st["ai_response"] == "好"
    assert steps == [] and called == {"search": 0, "regen": 0}


def test_flag开_命中_注入并再生成(monkeypatch, _flag_on):
    from app.agent import loop
    hits = [
        {"id": 1, "content": "去年夏天去了青岛", "type": "event", "importance": 60.0,
         "created_at": "2025-07-01 10:00:00", "epistemic_status": "FACT"},
        {"id": 2, "content": "当时住在栈桥附近", "type": "event", "importance": 50.0,
         "created_at": "2025-07-02 10:00:00", "epistemic_status": None},
    ]
    seen = {}

    async def _fake_search(*, character_id, query, limit, time_range=None, trace_meta=None):
        seen.update(q=query, limit=limit, tr=time_range, meta=trace_meta)
        return hits

    regen_called = {"n": 0}

    async def _fake_regen(state):
        regen_called["n"] += 1
        state["ai_response"] = "想起来了，我们一起去青岛玩过～"
        return state

    async def _fake_reinforce(*a, **k):
        regen_called["n"] += 1

    monkeypatch.setattr("app.memory.search_memories", _fake_search)
    monkeypatch.setattr("app.agent.nodes.generate_response", _fake_regen)
    monkeypatch.setattr("app.memory.service.reinforce_memories", _fake_reinforce)
    regen_called["n"] = 0
    st, steps = asyncio.run(loop.run_recall_loop(
        _state("嗯[RECALL]青岛[/RECALL]"), user_id=1, character_id=7))
    assert steps and steps[0]["action"] == "RECALL" and steps[0]["n"] == 2
    assert regen_called["n"] == 2  # reinforce + regen 各一次（fake 复用）
    assert st["ai_response"] == "想起来了，我们一起去青岛玩过～"
    # 注入块：追加 system 消息、含【补充记忆】与格式化记忆行
    msgs = st["context_messages"]
    assert len(msgs) == 2 and "【补充记忆】" in msgs[-1]["content"]
    assert "去年夏天去了青岛" in msgs[-1]["content"]
    assert seen["q"] == "青岛" and seen["limit"] == 6  # memory_recall_hop_limit 默认 6


def test_flag开_无命中_用首轮正文不再生成(monkeypatch, _flag_on):
    from app.agent import loop
    regen_called = {"n": 0}

    async def _fake_regen(state):
        regen_called["n"] += 1
        return state

    async def _empty(*a, **k):
        return []

    monkeypatch.setattr("app.memory.search_memories", _empty)
    monkeypatch.setattr("app.agent.nodes.generate_response", _fake_regen)
    st, steps = asyncio.run(loop.run_recall_loop(
        _state("首轮正文[RECALL]没有的记忆[/RECALL]"), user_id=1, character_id=7))
    assert st["ai_response"] == "首轮正文"
    assert steps and steps[0]["n"] == 0
    assert regen_called["n"] == 0
    assert len(st["context_messages"]) == 1  # 未注入


def test_flag开_gate关_剥离不检索(monkeypatch, _flag_on):
    from app.agent import loop
    called = {"search": 0}

    async def _fake_search(*a, **k):
        called["search"] += 1
        return [{"id": 1, "content": "x", "type": "event", "importance": 1.0,
                 "created_at": "2025-01-01", "epistemic_status": None}]

    monkeypatch.setattr("app.memory.search_memories", _fake_search)
    st, steps = asyncio.run(loop.run_recall_loop(
        _state("a[RECALL]q[/RECALL]"), user_id=1, character_id=7, gate=lambda: False))
    assert st["ai_response"] == "a" and steps == [] and called["search"] == 0


def test_flag开_async_gate_通过路径(monkeypatch, _flag_on):
    from app.agent import loop

    async def _gate():
        return True

    async def _hits(*a, **k):
        return [{"id": 9, "content": "旧事", "type": "event", "importance": 40.0,
                 "created_at": "2024-01-01", "epistemic_status": None}]

    regen = {"n": 0}

    async def _regen(state):
        regen["n"] += 1
        state["ai_response"] = "重组后的回复"
        return state

    async def _reinforce(*a, **k):
        regen["n"] += 1

    monkeypatch.setattr("app.memory.search_memories", _hits)
    monkeypatch.setattr("app.agent.nodes.generate_response", _regen)
    monkeypatch.setattr("app.memory.service.reinforce_memories", _reinforce)
    regen["n"] = 0
    st, steps = asyncio.run(loop.run_recall_loop(
        _state("x[RECALL]旧事[/RECALL]"), user_id=1, character_id=7, gate=_gate))
    assert steps and regen["n"] == 2 and st["ai_response"] == "重组后的回复"


def test_时间语法透传时间路(monkeypatch, _flag_on):
    from app.agent import loop
    seen = {}

    async def _fake_search(*, character_id, query, limit, time_range=None, trace_meta=None):
        seen.update(q=query, tr=time_range)
        return [{"id": 3, "content": "青岛海风", "type": "event", "importance": 55.0,
                 "created_at": "2026-07-20", "epistemic_status": "FACT"}]

    async def _regen(state):
        state["ai_response"] = "七月青岛真好"
        return state

    monkeypatch.setattr("app.memory.search_memories", _fake_search)
    monkeypatch.setattr("app.agent.nodes.generate_response", _regen)
    monkeypatch.setattr("app.memory.service.reinforce_memories", _regen)
    st, steps = asyncio.run(loop.run_recall_loop(
        _state("y[RECALL]时间=2026-07；青岛旅游[/RECALL]"), user_id=1, character_id=7))
    assert seen["q"] == "青岛旅游"
    assert seen["tr"] == (__import__("datetime").datetime(2026, 7, 1),
                          __import__("datetime").datetime(2026, 8, 1))
    assert "青岛海风" in st["context_messages"][-1]["content"]


def test_异常时兜底剥离标记(monkeypatch, _flag_on):
    from app.agent import loop

    async def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.memory.search_memories", _boom)
    st, steps = asyncio.run(loop.run_recall_loop(
        _state("正文[RECALL]q[/RECALL]尾"), user_id=1, character_id=7))
    assert st["ai_response"] == "正文尾"
    assert steps == []
