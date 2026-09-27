# -*- coding: utf-8 -*-
"""S1 搜索「发起方」口径测试（2026-09-27）

user（用户请求）＝必须回复、不许静默（空生成回落上一轮正文）；
self（角色自主）＝结果只作参考，不说＝本轮不产出消息（不回填、不替换、不报错）。
"""
import asyncio
import inspect

from app.agent import loop

# 改动前（HEAD）的 user 模板逐字副本：钉住「user 分支文案不变」这条硬约束
_OLD_USER_TEMPLATE = (
    "【搜索结果】（你已经搜索完成，现在直接基于这些真实信息回复；不要说自己去'搜索了'）。\n"
    "{result}\n\n"
    "注意：1. 如果结果有用，自然引用回答用户；2. 如果结果与问题无关或质量差，说明没查到靠谱的并给出你自己的看法（例如'网上说法不太靠谱，我估计…'）；"
    "3. 你已经搜索完成，绝不要说'我去搜一下/等着我去查'这类话；如果这次结果仍不够或与问题无关，可以再输出一次 [SEARCH] 补充查询（最多再查 1 次），否则不要再输出 [SEARCH] 标记。"
    "4. 网络信息属未证实来源（Observation: UNVERIFIED），涉及事实/数字/做法请谨慎转述，不确定就说明是'网上说法'。"
)


def _state(ai_response: str = "", *, prior: list | None = None):
    return {"ai_response": ai_response, "context_messages": list(prior or []), "reasoning": None, "tools_used": []}


def _run(final_state, *, search_results=None, regen_texts=None, initiator=None, throttle=True, inject=True):
    """执行 run_search_loop：search_results 依次返回（耗尽后返回空串）；regen_texts 依次作为再决策输出。

    initiator=None 表示**不传该参数**（走默认值），用于验证「默认＝user」。
    """
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
        kwargs = {
            "user_id": 1, "character_id": 2,
            "run_search": run_search, "throttle": lambda _u: throttle,
            "inject_enabled": lambda: inject, "save_history": save_history,
        }
        if initiator is not None:
            kwargs["initiator"] = initiator
        out_state, out_steps = asyncio.run(loop.run_search_loop(final_state, **kwargs))
        return out_state, out_steps, calls, regen_count
    finally:
        nodes.generate_response = orig


def _injected(out_state) -> str:
    """本轮注入的【搜索结果】消息正文（context_messages 末尾那条 system）"""
    return out_state["context_messages"][-1]["content"]


# ── 1. 默认参数＝user、旧行为不变 ──────────────────────────────────────────

def test_默认参数为user():
    assert inspect.signature(loop.run_search_loop).parameters["initiator"].default == "user"


def test_默认与显式user同输入同输出():
    st_a = _state("正文[SEARCH]猫咪吃什么[/SEARCH]")
    st_b = _state("正文[SEARCH]猫咪吃什么[/SEARCH]")
    out_a, steps_a, calls_a, regen_a = _run(st_a)
    out_b, steps_b, calls_b, regen_b = _run(st_b, initiator="user")
    assert out_a["ai_response"] == out_b["ai_response"] == "最终回复"
    assert steps_a == steps_b
    assert _injected(out_a) == _injected(out_b)
    assert calls_a["search"] == calls_b["search"] == ["猫咪吃什么"]
    assert regen_a == regen_b == 1
    # self 语义键不得出现在 user 分支
    assert loop.SEARCH_NO_MESSAGE_KEY not in out_a
    assert loop.SEARCH_NO_MESSAGE_KEY not in out_b


def test_user模板文案与改动前逐字一致():
    out, _steps, _calls, _regen = _run(_state("正文[SEARCH]q[/SEARCH]"))
    assert _injected(out) == _OLD_USER_TEMPLATE.format(result="结果：默认内容")


def test_未知发起方按user处理不静默():
    out, _steps, _calls, _regen = _run(
        _state("正文第一轮[SEARCH]q[/SEARCH]"), initiator="auto", regen_texts=[""])
    assert out["ai_response"] == "正文第一轮"
    assert loop.SEARCH_NO_MESSAGE_KEY not in out


# ── 2. user 分支：引用结果、不说「我去搜」、空生成回落上一轮 ────────────────

def test_user分支搜索结果被引用且不含去搜话术():
    out, steps, calls, _regen = _run(
        _state("正文第一轮[SEARCH]猫咪吃什么[/SEARCH]"),
        regen_texts=["网上说猫吃鱼和冻干都行，我照这个给你说"])
    assert steps[0]["ok"] is True
    assert calls["history"] == [(2, "猫咪吃什么")]
    # 真实结果进入注入上下文（可被引用），且模板禁止「我去搜/我去查」话术
    assert "结果：默认内容" in _injected(out)
    assert "绝不要说'我去搜一下/等着我去查'" in _injected(out)
    # 最终回复：就是基于结果写出的正文，不含去搜类话术
    assert out["ai_response"] == "网上说猫吃鱼和冻干都行，我照这个给你说"
    for banned in ("我去搜", "我去查", "[SEARCH]"):
        assert banned not in out["ai_response"]


def test_user分支生成空回落上一轮文本():
    out, steps, _calls, regen = _run(
        _state("正文第一轮[SEARCH]q[/SEARCH]"), regen_texts=[""])
    assert out["ai_response"] == "正文第一轮"
    assert out["ai_response"].strip() != ""
    assert regen == 1 and len(steps) == 1
    assert loop.SEARCH_NO_MESSAGE_KEY not in out


def test_user分支补查后生成空仍回落上一轮():
    # 第一轮正文 + 补查轮正文：最终再生成为空 ⇒ 回落到最近一轮非空正文
    out, _steps, calls, _regen = _run(
        _state("首轮正文[SEARCH]q1[/SEARCH]"),
        regen_texts=["补查轮正文[SEARCH]q2[/SEARCH]", ""])
    assert calls["search"] == ["q1", "q2"]
    assert out["ai_response"] == "补查轮正文"


# ── 3. self 分支：不说＝不产出消息 ─────────────────────────────────────────

def test_self分支生成空为本轮不产出消息():
    prior = [{"role": "user", "content": "既有消息"}]
    out, steps, calls, regen = _run(
        _state("正文第一轮[SEARCH]q[/SEARCH]", prior=prior),
        initiator="self", regen_texts=[""])
    # 不产出消息语义：显式标记 + 正文为空（不回填上一轮正文）
    assert out[loop.SEARCH_NO_MESSAGE_KEY] is True
    assert out["ai_response"] == ""
    # 不替换已有消息：注入前的上下文原样保留，只多了一条搜索结果参考
    assert out["context_messages"][0] == prior[0]
    assert len(out["context_messages"]) == len(prior) + 1
    # 搜索照常执行、不报错（steps 完整）
    assert steps == [{"action": "SEARCH", "query": "q", "ok": True, "round": 1}]
    assert calls["search"] == ["q"] and regen == 1


def test_self分支只剩标记也算不产出():
    out, steps, _calls, _regen = _run(
        _state("正文第一轮[SEARCH]q1[/SEARCH]"),
        initiator="self", regen_texts=["[SEARCH]q2[/SEARCH]", "[SEARCH]q3[/SEARCH]"])
    assert len(steps) == 2
    assert out["ai_response"] == ""  # 兜底剥离后只剩空 ⇒ 不回填「正文第一轮」
    assert out[loop.SEARCH_NO_MESSAGE_KEY] is True


def test_self分支生成非空正常返回():
    out, steps, _calls, _regen = _run(
        _state("正文第一轮[SEARCH]q[/SEARCH]"), initiator="self",
        regen_texts=["刚看到个说法挺有意思，顺便跟你提一句"])
    assert out["ai_response"] == "刚看到个说法挺有意思，顺便跟你提一句"
    assert loop.SEARCH_NO_MESSAGE_KEY not in out
    assert steps[0]["ok"] is True


def test_self分支模板为参考口径():
    out, _steps, _calls, _regen = _run(_state("正文[SEARCH]q[/SEARCH]"), initiator="self")
    content = _injected(out)
    assert content != _OLD_USER_TEMPLATE.format(result="结果：默认内容")
    assert "只作参考" in content and "没有人在等你回复" in content
    assert "你可以什么都不说" in content and "本轮不会发出消息" in content
    assert "我去搜一下/等着我去查" in content  # 两条分支都禁止该话术
    # user 版不得出现「可以不说」的自选口子
    out_u, *_ = _run(_state("正文[SEARCH]q[/SEARCH]"))
    assert "什么都不说" not in _injected(out_u)


# ── 4. 用户请求分支不许走自选路径（显式 user + 模型想静默） ─────────────────

def test_显式user即使想静默也必须给回复():
    # 模型再生成连出空串两次（等价于「决定什么都不说」）：user 分支仍不许静默
    out, steps, calls, regen = _run(
        _state("用户请求的正文[SEARCH]q1[/SEARCH]"), initiator="user",
        regen_texts=["", ""])
    assert out["ai_response"] == "用户请求的正文"
    assert out["ai_response"].strip() != ""
    assert loop.SEARCH_NO_MESSAGE_KEY not in out
    assert len(steps) == 1 and calls["search"] == ["q1"] and regen == 1


# ── 5. 轮数上限 / 重试策略两条分支一致 ─────────────────────────────────────

def test_轮数上限_user与self一致():
    out_u, steps_u, calls_u, regen_u = _run(
        _state("[SEARCH]q1[/SEARCH]"), initiator="user",
        regen_texts=["[SEARCH]q2[/SEARCH]", "[SEARCH]q3[/SEARCH]"])
    out_s, steps_s, calls_s, regen_s = _run(
        _state("[SEARCH]q1[/SEARCH]"), initiator="self",
        regen_texts=["[SEARCH]q2[/SEARCH]", "[SEARCH]q3[/SEARCH]"])
    assert loop.MAX_SEARCH_ROUNDS == 2
    for steps, calls, regen, out in ((steps_u, calls_u, regen_u, out_u), (steps_s, calls_s, regen_s, out_s)):
        assert [s["round"] for s in steps] == [1, 2]
        assert calls["search"] == ["q1", "q2"]  # 第 3 个标记被剥离不再执行
        assert regen == 2
        assert "[SEARCH]" not in out["ai_response"]


def test_重试策略_user与self一致():
    out_u, steps_u, calls_u, _regen_u = _run(
        _state("正文[SEARCH]q[/SEARCH]"), initiator="user",
        search_results=["", "结果：第二次成功"])
    out_s, steps_s, calls_s, _regen_s = _run(
        _state("正文[SEARCH]q[/SEARCH]"), initiator="self",
        search_results=["", "结果：第二次成功"])
    assert loop.SEARCH_RETRY == 1
    assert len(calls_u["search"]) == len(calls_s["search"]) == 2  # 首次空结果 + 重试 1 次
    assert steps_u[0]["ok"] is True and steps_s[0]["ok"] is True
    assert "结果：第二次成功" in _injected(out_u) and "结果：第二次成功" in _injected(out_s)


def test_失败降级两分支都不编造():
    # 搜索彻底失败（含重试）：user 回落本轮正文；self 也按原口径剥离标记
    out_u, steps_u, calls_u, regen_u = _run(
        _state("正文第一轮[SEARCH]q[/SEARCH]"), initiator="user", search_results=["", ""])
    out_s, steps_s, calls_s, regen_s = _run(
        _state("正文第一轮[SEARCH]q[/SEARCH]"), initiator="self", search_results=["", ""])
    assert out_u["ai_response"] == out_s["ai_response"] == "正文第一轮"
    assert steps_u == steps_s == [{"action": "SEARCH", "query": "q", "ok": False, "round": 1}]
    assert len(calls_u["search"]) == len(calls_s["search"]) == 2  # 失败仍只重试 1 次
    assert regen_u == regen_s == 0  # 未成功不触发再决策
    assert loop.SEARCH_NO_MESSAGE_KEY not in out_s  # 没搜成功 ⇒ 不套用「不说」语义
