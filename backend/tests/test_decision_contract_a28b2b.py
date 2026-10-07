# -*- coding: utf-8 -*-
"""A28-②b Decision Contract 守卫（派单 AMBRACE_paidan_A28b2b_decision_contract_20261007.md）。

红线就一条：**只建结构＋影子，不许接决策链**。所以守卫分三组：
① 结构与字段（派单点名的五个字段一个不多一个不少，`confidence` 恒 None 要说得清为什么）；
② 纯函数不伸手（不查库、不调模型、不执行工具）；
③ 默认关＝零行为，且**全仓没有任何决策路径读契约**（唯一的消费者是影子留痕）。
"""
from __future__ import annotations

import inspect
from dataclasses import fields

import pytest

from app.agent import decision_contract as dc
from app.agent.workspace import create_workspace


def _state(**kw):
    base = {"character_id": 13, "user_id": 3, "session_id": 9, "intent": "chat",
            "ai_response": "嗯，我在", "tools_used": [], "retrieved_memories": [],
            "active_topics": "", "perception": None, "plan_strategy": None,
            "emotional_state": "", "group_id": None, "skip_memory_save": False,
            "cognitive_loop_enabled": True, "channel_hint": None}
    base.update(kw)
    return base


# ── ① 结构 ────────────────────────────────────────────────────────────────────

def test_契约字段就是派单点名的那几个_不多不少():
    names = {f.name for f in fields(dc.DecisionContract)}
    assert {"intent", "action", "reason", "confidence", "constraints"} <= names
    # 多出来的两个都是"必须有消费方"要求带上的：候选面（枚举）与它的读数本身
    assert names == {"intent", "action", "reason", "confidence", "constraints", "candidates"}, sorted(names)


def test_confidence_恒None_并在读数里承认没来源():
    c = dc.build_contract(_state())
    assert c.confidence is None
    cov = c.coverage()
    assert cov["confidence_covered"] is False
    assert "confidence" in cov["missing"]
    assert dc.shadow_payload(c)["confidence_histogram"] == {"None": 1}


def test_词表就是派单那五套系统():
    assert set(dc.SOURCE_ACTIONS) == {"chat", "life", "proactivity", "topic", "tool"}
    assert dc.SOURCE_ACTIONS["chat"] == ("reply",)
    assert dc.SOURCE_ACTIONS["life"] == ("rest", "activity")
    assert dc.SOURCE_ACTIONS["proactivity"] == ("send_message",)
    assert dc.SOURCE_ACTIONS["topic"] == ("continue_goal",)
    assert set(dc.SOURCE_ACTIONS["tool"]) == {"search", "recall", "execute_tool"}
    assert dc.TOTAL_ACTIONS == sum(len(v) for v in dc.SOURCE_ACTIONS.values()) == 8  # 1+2+1+1+3


# ── ② 只枚举/只复盘，不决策 ──────────────────────────────────────────────────

def test_候选枚举只讲能不能选不排序不选型():
    ev = {"chat": True, "topic": True, "recall": True, "search": False,
          "execute_tool": True, "life": False, "send_message": False}
    got = dc.enumerate_candidates(ev)
    assert [g.action for g in got] == ["reply", "rest", "activity", "send_message",
                                       "continue_goal", "search", "recall", "execute_tool"]
    avail = {g.action for g in got if g.available}
    assert avail == {"reply", "continue_goal", "recall", "execute_tool"}
    for g in got:
        if not g.available:
            assert g.evidence == "", "不可用的候选不该编证据"
        else:
            assert g.evidence, f"{g.action} 可用却没说依据"


@pytest.mark.parametrize("state,allow_tools,want", [
    (_state(), True, "reply"),
    (_state(tools_used=["web_search"]), True, "search"),
    (_state(tools_used=["send_memo"]), True, "execute_tool"),
    (_state(tools_used=["web_search"]), False, "reply"),      # 工具没执行 ⇒ 不该报工具行为
    (_state(ai_response=""), True, None),                     # 什么都没做，就别硬安一个
])
def test_动作是复盘不是预测(state, allow_tools, want):
    action, why = dc.resolve_action(state, allow_tools)
    assert action == want
    assert why


def test_约束只登记state里证得出的那几条():
    c = dc.build_contract(_state(group_id=7, skip_memory_save=True, channel_hint="wechat"), allow_tools=False)
    keys = {x["key"] for x in c.constraints}
    assert keys == {"group_chat", "no_memory_save", "tools_disabled", "external_channel"}, keys
    for one in c.constraints:
        assert one["why"].startswith("state[") or "allow_tools" in one["why"], one
    plain = dc.build_contract(_state(cognitive_loop_enabled=False))
    assert {x["key"] for x in plain.constraints} == {"cognitive_loop_off"}


def test_模块不伸手():
    src = inspect.getsource(dc)
    for banned in ("async_session_factory", "sqlalchemy", "select(", "db.add", "commit()",
                   "chat_completion", "llm_client", "httpx", "save_memory", "interrupt",
                   "Command(", "graph."):
        assert banned not in src, f"decision_contract.py 出现 {banned}：它只许登记，不许伸手"


# ── ③ 默认关＝零行为；影子是唯一消费方；不接决策链 ────────────────────────────

def test_新键默认关():
    from app.flags.agent_flags import AGENT_FLAGS
    assert AGENT_FLAGS[dc.SHADOW_FLAG] is False


def test_闸关时不建不写不落痕(monkeypatch):
    import app.agent.trace as trace_mod

    def _boom(**kw):
        raise AssertionError("闸关着却落痕了")

    monkeypatch.setattr(trace_mod, "enqueue_task_log", _boom)
    ws = create_workspace(character_id=13, user_id=3)
    st = _state(workspace=ws)
    assert dc.shadow_enabled() is False
    assert dc.record_decision_contract(st) is None
    assert ws.decision_contract == {} and ws.candidate_actions == []


def test_闸开时建结构并落一条影子(monkeypatch):
    import app.agent.trace as trace_mod
    from app.flags.agent_flags import AGENT_FLAGS

    traced = []
    monkeypatch.setattr(trace_mod, "enqueue_task_log", lambda **kw: traced.append(kw))
    monkeypatch.setitem(AGENT_FLAGS, dc.SHADOW_FLAG, True)

    ws = create_workspace(character_id=13, user_id=3)
    got = dc.record_decision_contract(_state(workspace=ws, tools_used=["web_search"]), allow_tools=True)
    assert got and got["action"] == "search"
    assert ws.decision_contract["action"] == "search"
    assert ws.candidate_actions and all(c["action"] for c in ws.candidate_actions)
    assert len(traced) == 1
    assert traced[0]["route"] == dc.SHADOW_ROUTE and traced[0]["trigger"] == "agent"
    import json
    payload = json.loads(traced[0]["steps_json"])[0]
    assert {"intent", "action", "reason", "confidence", "constraints",
            "candidate_actions_available", "available_actions", "total_actions"} <= set(payload)
    assert payload["total_actions"] == dc.TOTAL_ACTIONS


def test_影子挂掉也只是没留痕_不影响主链路(monkeypatch):
    import app.agent.trace as trace_mod
    from app.flags.agent_flags import AGENT_FLAGS

    def _bad(**kw):
        raise RuntimeError("日志通道坏了")

    monkeypatch.setattr(trace_mod, "enqueue_task_log", _bad)
    monkeypatch.setitem(AGENT_FLAGS, dc.SHADOW_FLAG, True)
    got = dc.record_decision_contract(_state(workspace=create_workspace()), allow_tools=True)
    assert got and got["action"] == "reply"          # 契约照样建成，异常不外抛


def test_接线点全仓唯一():
    from pathlib import Path
    app = Path(__file__).resolve().parents[1] / "app"
    hits = []
    for p in app.rglob("*.py"):
        if p.name == "decision_contract.py":
            continue
        text = p.read_text(encoding="utf-8")
        if "record_decision_contract" in text:
            hits.append(str(p.relative_to(app.parent)).replace("\\", "/"))
    assert hits == ["app/agent/runtime.py"], f"契约被接到别处了：{hits}"


def test_没有任何执行路径读契约():
    """②b 的红线：契约只被写；除"装配＋落痕"这几个文件外，全仓不许出现 decision_contract 字样。

    （真有人拿它做决策时，必须先改这条守卫并附读数——那正是派单说的"转 on 需读数"。）
    """
    from pathlib import Path
    app = Path(__file__).resolve().parents[1] / "app"
    allowed = {"decision_contract.py", "workspace.py", "workspace_projection.py", "runtime.py",
               "agent_flags.py", "flag_catalog.py"}   # 后三个文件出现键名是"登记/接线"，不是"读契约"
    readers = []
    for f in app.rglob("*.py"):
        if f.name in allowed:
            continue
        if "decision_contract" in f.read_text(encoding="utf-8"):
            readers.append(f.name)
    assert not readers, f"有人开始读契约做决策了：{readers}"


def test_工作台既有字段语义不动():
    """`last_decision` 仍是反思的落点（②a 的口径），②b 另开字段，不覆盖不改名。"""
    from app.agent.workspace import CognitiveWorkspace
    names = [f.name for f in fields(CognitiveWorkspace)]
    assert names[-1] == "decision_contract"
    assert names.index("last_decision") < names.index("current_state")
    ws = create_workspace()
    assert "decision_contract" in ws.to_dict()
