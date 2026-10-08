# -*- coding: utf-8 -*-
"""A28-②a 认知投影守卫（派单 AMBRACE_paidan_A28b2a_projection_20261007.md）。

四组断言对应派单的四条要求：
① 字段映射与来源标注正确（Current State 走结构化、World 只投白名单路且**不 dump 整份**）；
② 覆盖率读数（填了什么／来源／覆盖 0 清单＋原因）能独立取到；
③ 红线：长期 topics/goals ≠ workspace.goal、不覆盖节点写的 focus、不与 Working State 合并；
④ 默认关＝零行为：闸关着不取数、不落痕；整体 fail-open；上下文装配仍然没人读 workspace。
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from app.agent import workspace_projection as wp
from app.agent.workspace import create_workspace


def _world_stub(routes_items: int = 5) -> dict:
    """一份"形状像 world_state_snapshot"的返回（八条路都有，含两派生视图）。"""
    item = {
        "source_store": "world_facts", "source_table": "world_facts", "subject_type": "user",
        "subject_id": 3, "actor": "user", "epistemic_status": "FACT", "predicate": "location",
        "kind": "state", "value": "湖光校区", "asserted_at": "2026-10-07T01:00:00",
        "fresh_until": "2026-10-07T13:00:00", "is_authoritative": True, "fresh_at_as_of": True,
        "line": "用户现在在湖光校区",           # 渲染文本：投影**不该带上**
    }
    routes = {}
    for name in ("world_facts", "character_current_status", "character_states", "life_states",
                 "working_state", "user_facts", "current_state_anchor", "status_memories"):
        routes[name] = {
            "route": name, "kind": "derived" if name in ("current_state_anchor", "status_memories") else "store",
            "source_module": "app.events.world_state", "source_at": "backend/app/events/world_state.py:170",
            "authoritative_source": "唯一事实源说明", "as_of_basis": "新鲜窗判据用 as_of",
            "status": "ok", "error": None, "count": routes_items,
            "elapsed_ms": 1, "items": [dict(item) for _ in range(routes_items)],
            "predicates": ["location"],
        }
    return {"version": "v1", "user_id": 3, "character_id": 13, "as_of": "2026-10-07T01:30:00",
            "as_of_source": "now_naive_utc()", "tz_basis": "naive-UTC",
            "routes": routes, "cross_store": {}, "summary": {"ok": 8, "item_count": 40}}


def _cs_stub() -> dict:
    return {"entries": [
        {"key": "location", "label": "位置", "value": "湖光校区", "source": "world_fact"},
        {"key": "job", "label": "工作", "value": "实习中", "source": "global_slot"},
    ], "empty": False}


# ── ① 字段映射与来源 ──────────────────────────────────────────────────────────

def test_CurrentState_用结构化entries_不拼成一段文本():
    ws = create_workspace(character_id=13, user_id=3)
    wp.project_current_state(ws, _cs_stub())
    assert isinstance(ws.current_state, dict)
    assert [e["value"] for e in ws.current_state["entries"]] == ["湖光校区", "实习中"]
    assert ws.current_state["source"] == "app/memory/current_state.get_current_user_state"
    # 没有任何"把 entries 渲染成一串"塞进来
    assert all(not isinstance(v, str) or "；" not in v for v in [ws.current_state["entries"][0]["label"]])
    assert "anchor_text" not in ws.current_state and "text" not in ws.current_state


def test_CurrentState_空entries_不写字段():
    ws = create_workspace(character_id=13, user_id=3)
    assert wp.project_current_state(ws, {"entries": [], "empty": True}) is None
    assert ws.current_state == {}


def test_World_只投白名单路_每路限量_且不带渲染文本():
    ws = create_workspace(character_id=13, user_id=3)
    world = _world_stub(routes_items=5)
    wp.project_world(ws, world)
    assert set(ws.world["routes"]) <= set(wp.PROJECTION_ROUTES), sorted(ws.world["routes"])
    assert "current_state_anchor" not in ws.world["routes"]      # 派生文本视图不进投影
    assert "status_memories" not in ws.world["routes"]
    for name, one in ws.world["routes"].items():
        assert len(one["items"]) <= wp.MAX_ITEMS_PER_ROUTE, name
        for it in one["items"]:
            assert "line" not in it, "渲染文案不该进 Workspace"
            # 认知标签与新鲜度/时点必须留着
            assert it.get("epistemic_status") == "FACT"
            assert it.get("asserted_at")
        assert one["authoritative_source"] and one["as_of_basis"]
    assert ws.world["as_of"] == world["as_of"]
    assert ws.world["source"] == "app/events/world_state.world_state_snapshot"


def test_WorkingState_独立字段_不与持久层合并():
    ws = create_workspace(character_id=13, user_id=3)
    wp.project_world(ws, _world_stub())
    assert wp.project_working_state(ws, _world_stub()) is True
    assert ws.working_state["note"] == "投影副本，不与持久滚动工作记忆合并"
    assert "routes['working_state']" in ws.working_state["source"]
    assert "working_state_service.get_latest" in ws.working_state["source"]
    # Working State 的持久化出口仍然只有那一个：投影不自己 import 它、不自己开会话
    src = inspect.getsource(wp)
    assert "from app.application.working_state_service import" not in src


def test_Identity_只抄调用方已有的字段():
    ws = create_workspace(character_id=13, user_id=3)
    assert wp.project_identity(ws, {"character_name": "小阳", "user_name": "阿明", "无关键": "x"}) is True
    assert ws.identity == {"character_name": "小阳", "user_name": "阿明"}
    ws2 = create_workspace()
    assert wp.project_identity(ws2, {}) is False and ws2.identity == {}


# ── ② 覆盖率读数 ─────────────────────────────────────────────────────────────

def test_覆盖率读数_列filled_来源_与覆盖0清单加原因():
    ws = create_workspace(character_id=13, user_id=3)
    ws.add_observation({"source": "memory", "status": "retrieved", "summary": "他住在湖光校区"})
    rep = wp.project_workspace(ws, current_state=_cs_stub(), world=_world_stub(),
                               identity={"character_name": "小阳"}, perception={"topic": "实习"})
    assert rep["total_fields"] == len(wp.PROJECTED_FIELDS)
    assert rep["filled_count"] == len(rep["filled"])
    assert 0 < rep["coverage"] <= 1
    for field_name, source in rep["filled"].items():
        assert source, f"{field_name} 没有来源标注"
    zero = {x["field"]: x["reason"] for x in rep["zero_coverage"]}
    assert set(zero) | set(rep["filled"]) == set(wp.PROJECTED_FIELDS)
    for f, why in zero.items():
        assert why and (f in wp.DEFER_REASONS or "本轮没有" in why), f"{f} 的留空原因没写实"
    # 既有写入点（observations/last_decision）算"有数据"，但不被本模块重写
    assert "observations" in rep["filled"]
    assert ws.projection == rep


def test_observations与last_decision_的来源标成既有写入点():
    ws = create_workspace(character_id=1, user_id=1)
    rep = wp.coverage_report(ws)
    ws.add_observation({"source": "tool", "status": "ok", "summary": "搜到 3 条"})
    ws.record_decision({"kind": "reflection", "result": {"ok": True}})
    rep2 = wp.coverage_report(ws)
    assert "observations" in rep2["filled"] and "last_decision" in rep2["filled"]
    assert "nodes.py" in rep2["filled"]["observations"]


# ── ③ 红线 ────────────────────────────────────────────────────────────────────

def test_长期topics不等于workspace_goal():
    ws = create_workspace(character_id=13, user_id=3)
    topics = [{"topic": "答辩 PPT", "goal": True}, {"topic": "换手机", "follow_up": True}]
    assert wp.project_topics(ws, topics) is True
    assert ws.active_topics[0]["topic"] == "答辩 PPT"
    assert ws.goal is None, "派单红线：长期 topics/goals 不得等同本轮选中的目标"
    rep = wp.project_workspace(ws, topics=topics)
    assert "goal" in {x["field"] for x in rep["zero_coverage"]}


def test_focus_不覆盖感知节点写过的值():
    ws = create_workspace(character_id=13, user_id=3)
    ws.set_focus("节点给的原始关注")
    assert wp.project_focus(ws, {"topic": "投影想改的关注"}) is False
    assert ws.focus == "节点给的原始关注"
    ws2 = create_workspace()
    assert wp.project_focus(ws2, {"topic": "实习"}) is True and ws2.focus == "实习"


def test_topics_非结构化输入_不猜只留空():
    ws = create_workspace()
    assert wp.project_topics(ws, "一段文本") is False and ws.active_topics == []
    assert wp.project_topics(ws, []) is False


def test_投影模块不查库不写库不调模型():
    src = inspect.getsource(wp)
    for banned in ("async_session_factory", "sqlalchemy", "select(", "db.execute", "db.add",
                   "commit()", "chat_completion", "llm_client", "httpx", "save_memory"):
        assert banned not in src, f"workspace_projection.py 出现 {banned}：投影只许调用既有只读接口"


# ── ④ 默认关＝零行为 ＋ fail-open ＋ 接线唯一 ─────────────────────────────────

def test_新键默认关():
    from app.flags.agent_flags import AGENT_FLAGS
    assert wp.SHADOW_FLAG in AGENT_FLAGS
    assert AGENT_FLAGS[wp.SHADOW_FLAG] is False


def test_闸关时不取数不落痕(monkeypatch):
    import app.events.world_state as ws_mod
    import app.memory.current_state as cs_mod
    import app.agent.trace as trace_mod

    hits = []

    def _boom(*a, **k):              # 计数桩：抛异常会被投影的 fail-open 吞掉，测不出"到底调没调"
        hits.append(a)
        return None

    monkeypatch.setattr(cs_mod, "get_current_user_state", _boom)
    monkeypatch.setattr(ws_mod, "world_state_snapshot", _boom)
    monkeypatch.setattr(trace_mod, "enqueue_task_log", _boom)

    ws = create_workspace(character_id=13, user_id=3)
    state = {"workspace": ws, "character_id": 13, "user_id": 3, "session_id": 9,
             "character_name": "小阳", "user_name": "阿明", "perception": {"topic": "实习"}}
    rep = asyncio.run(wp.project_into_workspace(state))
    assert hits == [], "闸关着却取了数／落了痕：%s" % hits
    assert rep["shadow_flag"] is False
    assert rep["filled"].get("identity") and rep["filled"].get("focus")
    assert "current_state" not in rep["filled"] and "world" not in rep["filled"]


def test_闸开时取既有接口并落一条影子留痕(monkeypatch):
    import app.events.world_state as ws_mod
    import app.memory.current_state as cs_mod
    import app.agent.trace as trace_mod

    calls = []

    async def _cs(*, character_id, user_id, **k):
        calls.append("current_state")
        return _cs_stub()

    async def _world(user_id, character_id, **k):
        calls.append("world")
        return _world_stub()

    traced = []

    def _enqueue(**kw):
        traced.append(kw)

    import app.agent.topic_tracker as tt_mod

    async def _rows(cid, uid, **k):
        calls.append("active_topics")
        return [{"topic": "答辩 PPT"}]

    monkeypatch.setattr(cs_mod, "get_current_user_state", _cs)
    monkeypatch.setattr(ws_mod, "world_state_snapshot", _world)
    monkeypatch.setattr(tt_mod, "load_active_topics_rows", _rows)
    monkeypatch.setattr(trace_mod, "enqueue_task_log", _enqueue)
    from app.flags.agent_flags import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, wp.SHADOW_FLAG, True)

    ws = create_workspace(character_id=13, user_id=3)
    state = {"workspace": ws, "character_id": 13, "user_id": 3, "session_id": 9}
    rep = asyncio.run(wp.project_into_workspace(state))
    assert calls == ["current_state", "world", "active_topics"]
    assert rep["shadow_flag"] is True
    assert "current_state" in rep["filled"] and "world" in rep["filled"] and "working_state" in rep["filled"]
    assert len(traced) == 1
    assert traced[0]["route"] == wp.SHADOW_ROUTE and traced[0]["trigger"] == "agent"
    import json
    assert json.loads(traced[0]["steps_json"])[0]["filled_count"] == rep["filled_count"]


def test_取数失败也只是少投字段_绝不冒泡(monkeypatch):
    import app.events.world_state as ws_mod
    import app.memory.current_state as cs_mod
    from app.flags.agent_flags import AGENT_FLAGS

    async def _bad(*a, **k):
        raise RuntimeError("库断了")

    monkeypatch.setattr(cs_mod, "get_current_user_state", _bad)
    monkeypatch.setattr(ws_mod, "world_state_snapshot", _bad)
    monkeypatch.setitem(AGENT_FLAGS, wp.SHADOW_FLAG, True)

    ws = create_workspace(character_id=13, user_id=3)
    rep = asyncio.run(wp.project_into_workspace({"workspace": ws, "character_id": 13, "user_id": 3}))
    assert "current_state" not in rep["filled"] and "world" not in rep["filled"]


def test_没有工作台的旧路径_静默跳过():
    assert asyncio.run(wp.project_into_workspace({})) is None
    assert asyncio.run(wp.project_into_workspace({"workspace": None})) is None


def test_投影调用点全仓只有一处():
    root = Path(__file__).resolve().parents[1] / "app"
    hits = []
    for p in root.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if "project_into_workspace" in text and p.name != "workspace_projection.py":
            hits.append(str(p.relative_to(root)))
    assert hits == [str(Path("agent/runtime.py"))], f"接线点扩散了：{hits}"


def test_上下文装配仍然没人读workspace_投影不许顺手进prompt():
    from app.agent import context_builder as cb

    targets = [Path(cb.__file__)]
    ctx_dir = Path(cb.__file__).parent / "context"
    if ctx_dir.is_dir():
        targets += sorted(ctx_dir.glob("*.py"))
    reading = [p.name for p in targets if "workspace" in p.read_text(encoding="utf-8")]
    assert not reading, f"投影被接进上下文装配了：{reading}"


@pytest.mark.parametrize("bad", [None, "", 0, {}, [], {"empty": True}, {"entries": []}])
def test_覆盖率判据不把空壳当有数据(bad):
    ws = create_workspace()
    ws.current_state = bad if isinstance(bad, dict) else {}
    rep = wp.coverage_report(ws)
    assert "current_state" not in rep["filled"]


def test_workspace新增字段进快照且既有键一字未动():
    from app.agent.workspace import CognitiveWorkspace

    fields = list(CognitiveWorkspace.__dataclass_fields__)
    for new in ("current_state", "world", "working_state", "projection"):
        assert new in fields
    assert fields[:15] == ["character_id", "user_id", "session_id", "identity", "focus", "goal",
                           "active_need", "observations", "open_loops", "active_topics",
                           "candidate_actions", "constraints", "last_decision", "confidence",
                           "pending_commitments"], "既有字段顺序被改了（to_dict 消费方按名字读，但顺序是历史承诺）"
    snap = create_workspace(character_id=1).to_dict()
    assert set(snap) >= set(fields), sorted(set(fields) - set(snap))


def test_workspace本体依旧不伸手拿数据():
    from app.agent import workspace as ws_mod

    src = inspect.getsource(ws_mod)
    imported = {l.split()[1].split(".")[0] for l in src.splitlines()
                if l.startswith("import ") or l.startswith("from ")}
    assert imported <= {"__future__", "dataclasses", "typing"}, sorted(imported)
    for banned in ("async_session_factory", "AGENT_FLAGS", "get_current_user_state", "world_state_snapshot"):
        assert banned not in src


# ══════════ 10-08 补：identity 的取键必须等于装配阶段真写进 state 的那些 ══════════
# 来历：`context/assembly.py` 写 `user_name`／`character_name`（:130-134）与
# `character_info={"self_statement":…}`（:102）；而投影旧写法找的 `persona_summary`／`personality`／`bio`
# 全仓没有任何一处往 agent state 写 ⇒ 人设那一格永远空。
# 反证实测（10-08，把取键改回旧写法跑一遍）：**三条里只有第一条红**（`persona_summary` 拿不到），
# 另两条是回归锁（旧写法下也过，防以后有人把 identity 又改回单键／让坏形状炸主链路）——别把这三条都当成"改前必红"。
def _live_shape_state(ws):
    """照 assembly 真写出来的形状造一份 state（不查库、不调模型）。"""
    return {
        "workspace": ws,
        "character_id": 13, "user_id": 3, "session_id": 11,
        "character_name": "萨姆",
        "user_name": "小美",
        "character_info": {"self_statement": "我叫萨姆，话少，不腻歪。", "bio": "你哥，商场上冷了点。"},
        "ai_response": "嗯。",
    }


def test_identity_吃到装配阶段真有的四个键():
    ws = create_workspace(character_id=13, user_id=3, session_id=11)
    asyncio.run(wp.project_into_workspace(_live_shape_state(ws)))
    assert ws.identity.get("character_name") == "萨姆"
    assert ws.identity.get("user_name") == "小美"
    assert ws.identity.get("persona_summary") == "我叫萨姆，话少，不腻歪。", "人设要从 character_info.self_statement 兜底"
    assert ws.identity.get("bio") == "你哥，商场上冷了点。", "bio 要从 character_info.bio 拿（装配阶段真写的那个）"


def test_identity_填上后覆盖率不再报它为零():
    ws = create_workspace(character_id=13, user_id=3, session_id=11)
    rep = asyncio.run(wp.project_into_workspace(_live_shape_state(ws)))
    assert "identity" in rep["filled"], rep["filled"]
    assert "identity" not in [z["field"] for z in rep["zero_coverage"]]


def test_character_info_形状不对也只是少填一格不抛():
    ws = create_workspace(character_id=13, user_id=3, session_id=11)
    st = _live_shape_state(ws)
    st["character_info"] = "坏值"                       # 装配阶段理论上不会给，但投影不许因此炸
    rep = asyncio.run(wp.project_into_workspace(st))
    assert ws.identity.get("character_name") == "萨姆"   # 其余照填
    assert "persona_summary" not in ws.identity
    assert "bio" not in ws.identity
    assert rep["shadow_flag"] is False                   # 闸仍关着：没顺便去查库


# ══════════ 10-08 补：identity 不许留「全仓没人写」的死键，且写入点必须真的存在 ══════════
# 来历：v1 离线读数（11.1%）压根没往 identity 里喂东西，于是「identity 覆盖 0」既像缺陷又像事实；
# 核对代码后确认：`persona_summary`／`personality`／`bio` 三个顶层键全仓零写入点，
# 而 `character_info` 两处装配点只写了 self_statement ⇒ bio 当时是「补了取键、没补来源」。
DEAD_TOP_KEYS = ('state.get("persona_summary")', 'state.get("personality")', 'state.get("bio")')
CHAR_INFO_WRITERS = ("agent/context/assembly.py", "agent/runtime.py")


def test_identity取值链里没有死键():
    src = inspect.getsource(wp)
    for dead in DEAD_TOP_KEYS:
        assert dead not in src, "死键又回来了：" + dead


def test_character_info_两处装配点真的都带上self_statement和bio():
    app_root = Path(__file__).resolve().parents[1] / "app"
    hits = []
    for rel in CHAR_INFO_WRITERS:
        text = (app_root / rel).read_text(encoding="utf-8-sig")
        for line in text.splitlines():
            if 'state["character_info"]' in line and "=" in line and "==" not in line:
                assert "self_statement" in line, rel + " 的 character_info 丢了 self_statement"
                assert "bio" in line, rel + " 的 character_info 丢了 bio（identity 那格又要永远空）"
                hits.append(rel)
    assert len(hits) >= 2, "写入点没扫到 2 处（实测 %s）⇒ 装配形状被改了，这条守卫要看住" % hits
