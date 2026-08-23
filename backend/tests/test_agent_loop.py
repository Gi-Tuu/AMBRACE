# -*- coding: utf-8 -*-
"""受控 Agent Loop 测试（Phase B，2026-08-16）：搜索循环/节流/失败重试/补查/超限/Flag 回退"""
import asyncio

from app.agent import loop


def _state(ai_response: str = ""):
    return {"ai_response": ai_response, "context_messages": [], "reasoning": None, "tools_used": []}


def _run(final_state, *, search_results=None, throttle=True, inject=True, regen_texts=None, max_steps=None):
    """执行 run_search_loop 的测试辅助：search_results 依次返回（耗尽后返回空串）；regen_texts 依次作为再决策输出"""
    has_search_results = search_results is not None
    search_results = list(search_results or [])
    regen_texts = list(regen_texts or [])
    calls = {"search": [], "history": []}
    regen_count = 0

    async def run_search(query):
        calls["search"].append(query)
        if not has_search_results:
            return "结果：默认内容"
        return search_results.pop(0) if search_results else ""

    async def save_history(char_id, query):
        calls["history"].append((char_id, query))

    async def fake_regen(state):
        nonlocal regen_count
        text = regen_texts[regen_count] if regen_count < len(regen_texts) else "最终回复"
        regen_count += 1
        state["ai_response"] = text
        return state

    import app.agent.nodes as nodes
    orig = nodes.generate_response
    nodes.generate_response = fake_regen
    try:
        out_steps = asyncio.run(loop.run_search_loop(
            final_state, user_id=1, character_id=2,
            run_search=run_search, throttle=lambda _u: throttle,
            inject_enabled=lambda: inject, save_history=save_history,
            max_steps=max_steps,
        ))
        return out_steps, calls, regen_count
    finally:
        nodes.generate_response = orig


def test_loop_单次搜索成功():
    st = _state("正文第一轮[SEARCH]猫咪吃什么[/SEARCH]")
    (out, steps), calls, regen_count = _run(st, regen_texts=["正文最终回复"])
    assert out["ai_response"] == "正文最终回复"
    assert len(steps) == 1
    assert steps[0]["action"] == "SEARCH"
    assert steps[0]["ok"] is True
    assert calls["search"] == ["猫咪吃什么"]
    assert calls["history"][0] == (2, "猫咪吃什么")
    assert regen_count == 1
    # 注入模板允许结果不足时补查 1 次
    assert "最多再查 1 次" in out["context_messages"][-1]["content"]


def test_loop_节流不通过不搜索():
    st = _state("正文[SEARCH]查询[/SEARCH]")
    (out, steps), calls, regen_count = _run(st, throttle=False)
    assert out["ai_response"] == "正文"
    assert steps == []
    assert calls["search"] == []
    assert regen_count == 0


def test_loop_注入开关关闭不搜索():
    st = _state("正文[SEARCH]查询[/SEARCH]")
    (out, steps), calls, regen_count = _run(st, inject=False)
    assert out["ai_response"] == "正文"
    assert steps == []
    assert calls["search"] == []
    assert regen_count == 0


def test_loop_搜索失败静默降级():
    st = _state("正文[SEARCH]查询[/SEARCH]")
    (out, steps), calls, _ = _run(st, search_results=[""])
    assert out["ai_response"] == "正文"
    assert steps == [{"action": "SEARCH", "query": "查询", "ok": False, "round": 1}]
    assert calls["history"] == []


def test_loop_失败自动重试一次():
    st = _state("[SEARCH]查询[/SEARCH]")
    (out, steps), calls, _ = _run(st, search_results=["", "结果：第二次成功"])
    assert steps[0]["ok"] is True
    assert len(calls["search"]) == 2  # 首次失败 + 重试 1 次
    assert len(calls["history"]) == 1


def test_loop_补查一次():
    st = _state("[SEARCH]第一查[/SEARCH]")
    (out, steps), calls, regen_count = _run(st, regen_texts=["第二轮[SEARCH]补查[/SEARCH]", "最终回复"])
    assert len(steps) == 2
    assert steps[0]["query"] == "第一查"
    assert steps[1]["query"] == "补查"
    assert calls["search"] == ["第一查", "补查"]
    assert out["ai_response"] == "最终回复"
    assert regen_count == 2


def test_loop_超过轮数上限剥离():
    st = _state("[SEARCH]q1[/SEARCH]")
    (out, steps), calls, regen_count = _run(st, regen_texts=["[SEARCH]q2[/SEARCH]", "[SEARCH]q3[/SEARCH]"])
    assert len(steps) == 2  # 最多 2 次真实搜索（3 次 LLM 调用含首轮）
    assert calls["search"] == ["q1", "q2"]
    assert "[SEARCH]" not in out["ai_response"]
    assert regen_count == 2


def test_loop_flag关闭退回单次搜索():
    st = _state("[SEARCH]q1[/SEARCH]")
    loop.AGENT_FLAGS["agent_loop_search"] = False
    try:
        (out, steps), calls, _ = _run(st, regen_texts=["[SEARCH]q2[/SEARCH]"])
        assert len(steps) == 1
        assert calls["search"] == ["q1"]
        assert "[SEARCH]" not in out["ai_response"]
    finally:
        loop.AGENT_FLAGS["agent_loop_search"] = True


def test_loop_无标记不触发():
    st = _state("纯文本回复")
    (out, steps), calls, regen_count = _run(st)
    assert out["ai_response"] == "纯文本回复"
    assert steps == []
    assert calls["search"] == []
    assert regen_count == 0


def test_loop_搜索权限拦截降级(monkeypatch):
    async def _blocked(user_id, query, run_search):
        return {"status": "blocked", "error": "forbid"}

    monkeypatch.setattr(loop, "_execute_search_tool", _blocked)
    st = _state("正文[SEARCH]查询[/SEARCH]")
    (out, steps), calls, _ = _run(st)
    assert out["ai_response"] == "正文"  # 剥离标记降级
    assert steps == [{"action": "SEARCH", "query": "查询", "ok": False, "round": 1}]
    assert calls["search"] == []  # 权限拦截，未真正搜索


def test_loop_搜索走统一执行入口(monkeypatch):
    seen = {}

    async def _fake_execute(spec, payload, **kw):
        seen["spec"] = spec
        seen["payload"] = payload
        return {"status": "ok", "result": "结果：xxx"}

    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _fake_execute)
    st = _state("[SEARCH]查询[/SEARCH]")
    (out, steps), calls, _ = _run(st)
    assert seen.get("payload") == {"query": "查询"}
    assert seen.get("spec").name == "search"
    assert steps[0]["ok"] is True
    assert len(calls["history"]) == 1


def test_loop_限制常量():
    assert loop.MAX_LLM_STEPS == 3
    assert loop.MAX_SEARCH_ROUNDS == 2
    assert loop.SEARCH_RETRY == 1
    assert loop.AGENT_FLAGS.get("agent_loop_search") is True
