# -*- coding: utf-8 -*-
"""A4 批 2 / T4 P2（2026-10-01）：召回门**生效**接线（开关 `recall_gate`）。

口径（设计 §P2 / 派单书）：
  - 关＝**不 import、不计算**、逐字节旧行为；
  - 开 ⇒ 检索前先做一次纯函数判定，`判为「纯寒暄 / 纯符号」` 的轮次跳过检索与记忆注入；
  - 判定 / 导入异常一律 **fail-open**（照旧检索，宁多不漏）。

本文件只打桩 `search_memories` / `reinforce_memories` / 影子留痕，不触网、不连库、不起服务。
"""
from __future__ import annotations

import asyncio

import pytest

from app.agent import nodes as nodes_mod
from app.agent.loop import AGENT_FLAGS
from app.memory import recall_gate

_FLAG = "recall_gate"
_SHADOW = "recall_gate_shadow"


@pytest.fixture(autouse=True)
def _restore_flags():
    saved = {k: AGENT_FLAGS.get(k) for k in (_FLAG, _SHADOW)}
    yield
    for k, v in saved.items():
        if v is None:
            AGENT_FLAGS.pop(k, None)
        else:
            AGENT_FLAGS[k] = v


def _state(msg="在吗"):
    return {"user_message": msg, "character_id": 13, "user_id": 1, "perception": {}}


def _patch(monkeypatch, *, hits=None, calls=None):
    calls = calls if calls is not None else {}

    async def _fake_search(**kw):
        calls["search"] = kw
        return list(hits or [])

    async def _fake_reinforce(ids, **kw):
        calls["reinforce"] = list(ids)

    monkeypatch.setattr("app.memory.search_memories", _fake_search, raising=False)
    monkeypatch.setattr("app.memory.service.reinforce_memories", _fake_reinforce, raising=False)
    return calls


def test_开关关时逐字节旧行为_判定函数即使炸也不影响(monkeypatch):
    AGENT_FLAGS[_FLAG] = False
    calls = _patch(monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("flag off 时不得调用判定")

    monkeypatch.setattr(recall_gate, "decide_retrieval", _boom)
    out = asyncio.run(nodes_mod.retrieve_memories(_state()))
    assert "search" in calls, "开关关 ⇒ 必须照旧检索"
    assert out["retrieved_memories"] == []


def test_开关开时纯寒暄跳过检索(monkeypatch):
    AGENT_FLAGS[_FLAG] = True
    calls = _patch(monkeypatch, hits=[{"id": 1, "content": "x"}])
    out = asyncio.run(nodes_mod.retrieve_memories(_state("晚安")))
    assert "search" not in calls, "纯寒暄 ⇒ 不应检索"
    assert out["retrieved_memories"] == []
    assert "reinforce" not in calls, "跳过检索 ⇒ 也不该记强化"


def test_开关开时问句照旧检索并强化(monkeypatch):
    AGENT_FLAGS[_FLAG] = True
    calls = _patch(monkeypatch, hits=[{"id": 7, "content": "y"}])
    out = asyncio.run(nodes_mod.retrieve_memories(_state("你还记得我上次说的那件事吗")))
    assert "search" in calls
    assert out["retrieved_memories"] == [{"id": 7, "content": "y"}]
    assert calls.get("reinforce") == [7]


def test_判定异常时fail_open继续检索(monkeypatch):
    AGENT_FLAGS[_FLAG] = True
    calls = _patch(monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(recall_gate, "decide_retrieval", _boom)
    asyncio.run(nodes_mod.retrieve_memories(_state("晚安")))
    assert "search" in calls, "判定异常 ⇒ 回到旧行为继续检索"


def test_键缺失时按关处理(monkeypatch):
    AGENT_FLAGS.pop(_FLAG, None)
    calls = _patch(monkeypatch)
    asyncio.run(nodes_mod.retrieve_memories(_state("晚安")))
    assert "search" in calls


def test_跳过时影子记录带skipped_by_gate(monkeypatch):
    AGENT_FLAGS[_FLAG] = True
    AGENT_FLAGS[_SHADOW] = True
    _patch(monkeypatch)
    seen = {}

    def _fake_observe(msg, **kw):
        seen.update(kw)

    monkeypatch.setattr(recall_gate, "observe_retrieval_decision", _fake_observe)
    asyncio.run(nodes_mod.retrieve_memories(_state("晚安")))
    assert seen.get("hit_count") == 0
    assert seen.get("skipped_by_gate") is True


def test_gate_enabled_缺省关且读不到也按关():
    AGENT_FLAGS.pop(_FLAG, None)
    assert recall_gate.gate_enabled() is False
    AGENT_FLAGS[_FLAG] = True
    assert recall_gate.gate_enabled() is True


def test_影子记录体含skipped_by_gate字段():
    rec = recall_gate.plan_shadow_record("晚安", skipped_by_gate=True)
    assert rec["skipped_by_gate"] is True
    assert rec["retrieve"] is False and rec["reason"] == recall_gate.REASON_SMALL_TALK
