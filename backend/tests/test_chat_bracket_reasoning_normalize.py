# -*- coding: utf-8 -*-
"""小批次任务 1（P1-6，2026-09-16）：括号推理并入同一归一管线。

背景（交接文档已核实）：`chat_service._run_agent_core` 里「证据 A 括号推理」是在既有
reasoning 归一之后直接字符串拼接进 `final_state["reasoning"]` 的，因此该片段没过
`normalize_reasoning_for_display`——「用户」/名字自称/策略·长度·我决定加图 这类元话语
仍可能随该片段上屏。

本文件锁定：
1. `_merge_bracket_reasoning`：片段与既有思考合并后整段归一（元话语消失、语义保留）；
2. 归一后为空时不写入、不清空既有 reasoning；
3. `_run_agent_core` 端到端：正文开头的括号推理经归一后落 `final_state["reasoning"]`，
   且不再残留元话语（旧实现会残留「策略：简短…」）。
"""
import asyncio

from app.application import chat_service

# ── 样本：括号推理片段（含工单式元话语 + 自然内心）────────────────
_BRACKET_META = "策略：简短；我决定加图；其实我想先陪他坐一会儿。"
_BRACKET_NATURAL = "他嘴上说没事，手指却一直抠着杯沿。算了，不戳穿，先给他倒杯热的。"


def _state(**kw) -> dict:
    st = {"reasoning": None, "character_name": "Sam", "user_name": "轩"}
    st.update(kw)
    return st


# ────────────────── 1：片段并入后走归一 ──────────────────

def test_括号推理元话语被归一剔除_semantics_kept():
    st = _state()
    assert chat_service._merge_bracket_reasoning(st, _BRACKET_META) is True
    assert st["reasoning"] == "其实我想先陪他坐一会儿。"
    assert "策略" not in st["reasoning"] and "加图" not in st["reasoning"]


def test_括号推理自然内心保留():
    st = _state()
    assert chat_service._merge_bracket_reasoning(st, _BRACKET_NATURAL) is True
    assert st["reasoning"] == _BRACKET_NATURAL


def test_既有reasoning在前_片段接在其后():
    st = _state(reasoning="他今天听着挺累，我先陪他说两句。")
    assert chat_service._merge_bracket_reasoning(st, "其实我想让他早点睡。") is True
    assert st["reasoning"] == "他今天听着挺累，我先陪他说两句。其实我想让他早点睡。"


def test_用户与名字自称随片段一并归一():
    st = _state()
    raw = "用户说回来了，sam应该先别急着回，sam把肉盛出来，问他吃没。"
    assert chat_service._merge_bracket_reasoning(st, raw) is True
    assert "用户" not in st["reasoning"]
    assert "sam" not in st["reasoning"].lower()
    assert "轩说回来了" in st["reasoning"]
    assert "我把肉盛出来" in st["reasoning"]


# ────────────────── 2：归一后为空 / 空片段不写入 ──────────────────

def test_纯元话语片段不清空既有reasoning():
    # 合并后整段仍有既有内容 → 归一保留既有、元话语片段被剔除
    st = _state(reasoning="他回来了。")
    chat_service._merge_bracket_reasoning(st, "策略：简短；长度：短。")
    assert st["reasoning"] == "他回来了。"


def test_纯元话语片段无既有reasoning时不写入():
    st = _state(reasoning=None)
    assert chat_service._merge_bracket_reasoning(st, "策略：简短；长度：短。") is False
    assert st["reasoning"] is None


def test_空片段noop不新增字段():
    st = _state()
    assert chat_service._merge_bracket_reasoning(st, "") is False
    assert st["reasoning"] is None


# ────────────────── 3：_run_agent_core 端到端（回归旁路）──────────────────

class _FakeAgent:
    def __init__(self, state: dict):
        self._state = state

    async def ainvoke(self, initial_state):  # noqa: ARG002 - 替身只回放状态
        return dict(self._state)


def _close_coro(coro=None, *_a, **_k):
    """spawn_background 替身：关闭未 await 的协程，避免「never awaited」告警。"""
    if hasattr(coro, "close"):
        coro.close()


def test_run_agent_core括号推理经归一后上屏(monkeypatch):
    async def _no_cold(*_a, **_k):
        return False

    async def _level(*_a, **_k):
        return 0

    async def _emo(*_a, **_k):
        return ""

    async def _no_notes(*_a, **_k):
        return None

    import app.agent.trace as trace

    monkeypatch.setattr(trace, "enqueue_task_log", lambda **_k: None)
    monkeypatch.setattr(trace, "new_task_id", lambda: "test-task")
    monkeypatch.setattr(chat_service, "_cold_war_block", _no_cold)
    monkeypatch.setattr(chat_service, "_load_reasoning_level", _level)
    monkeypatch.setattr(chat_service, "_resolve_emotional_state", _emo)
    monkeypatch.setattr(chat_service, "_save_phone_desktop_notes", _no_notes)
    monkeypatch.setattr(chat_service, "spawn_background", _close_coro)
    monkeypatch.setattr(chat_service, "agent", _FakeAgent({
        "ai_response": f"（{_BRACKET_META}）\n行，等你。",
        "reasoning": None,
        "character_name": "Sam",
        "user_name": "轩",
        "streamed": False,
    }))

    core = asyncio.run(chat_service._run_agent_core(
        1, 1, 2, "回来啦", "zh", None, user_timer=False, search_loop=False,
    ))
    assert core is not None
    assert core["final_text"] == "行，等你。"
    # 旁路修复点：括号片段并入后统一归一——元话语不得再出现在 reasoning 里
    assert core["final_state"]["reasoning"] == "其实我想先陪他坐一会儿。"
    assert core["final_state"]["reasoning_bracket_stripped"] is True
