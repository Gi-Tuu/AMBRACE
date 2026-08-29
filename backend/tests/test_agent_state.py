# -*- coding: utf-8 -*-
"""AgentState / LangGraph 真流式通道回归测试（SSE 流式失效 P0 修复）。

根因：LangGraph 1.x 只保留 AgentState(TypedDict) 声明过的字段，chat_service 注入
initial_state 的 stream_sink / lang / tts / block_sink 等被静默丢弃，导致：
  - stream_sink 为 None → nodes.generate_response can_stream=False → 永走非流式（打字机失效）；
  - streamed / stream_display / stream_blocks 等节点回填值传不回服务层。

本文件覆盖（§九 3/4）：
1. 走完整 agent.ainvoke()（真实 LangGraph StateGraph 通道，不经直接节点调用）：
   - 注入 stream_sink → 产生 delta 事件、final streamed=True、stream_display 正确、
     lang / temperature 经 channel 传递到 generate_response；
   - continue_chat 路径不传 stream_sink → can_stream=False 走非流式（行为不变）。
2. temperature 声明后某路径未写入时 float(None) 兜底（§九 1）：用 float(None or 0.8)。
3. AgentState 完整性检查：chat_service 两处 initial_state 的 key 集合 ⊆ AgentState.__annotations__。

说明：perceive / retrieve_memories / build_context / reflect 为 DB/LLM 重节点，已有各自单测；
此处用轻量控制节点复刻 graph.py 拓扑（StateGraph + compile + ainvoke 全通道），保留真实
generate_response（bug 暴露点），从而证明 LangGraph channel 对 AgentState 声明字段的传播。
"""
import asyncio
import ast
from pathlib import Path

from langgraph.graph import END, StateGraph

from app.agent import nodes
from app.agent.state import AgentState


# ── 轻量控制节点（复刻 graph.py 拓扑：perceive→retrieve→build_context→generate_response→reflect）──

async def _ctrl_perceive(state):
    state["cognitive_loop_enabled"] = False
    state["perception"] = None
    return state


async def _ctrl_retrieve(state):
    state["retrieved_memories"] = []
    return state


async def _ctrl_build_context(state):
    """模拟真实 build_context（context_builder L1458-1469 P3-2）：写入 temperature + 上下文。

    set_temperature=False 时模拟 build_context 提前返回（如角色不存在）不写入 temperature 的路径。
    """
    if _CTRL["set_temperature"]:
        state["temperature"] = 0.85
    state["context_messages"] = [{"role": "user", "content": state.get("user_message", "")}]
    state["character_info"] = {"self_statement": ""}
    return state


async def _ctrl_reflect(state):
    state["reflection_result"] = None
    return state


# 可切换的 build_context 行为标志（set_temperature）
_CTRL = {"set_temperature": True}


def _build_test_agent():
    """用真实 AgentState + 真实 generate_response 编译一个测试用 LangGraph 图。"""
    wf = StateGraph(AgentState)
    wf.add_node("perceive", _ctrl_perceive)
    wf.add_node("retrieve_memories", _ctrl_retrieve)
    wf.add_node("build_context", _ctrl_build_context)
    wf.add_node("generate_response", nodes.generate_response)
    wf.add_node("reflect", _ctrl_reflect)
    wf.set_entry_point("perceive")
    wf.add_edge("perceive", "retrieve_memories")
    wf.add_edge("retrieve_memories", "build_context")
    wf.add_edge("build_context", "generate_response")
    wf.add_edge("generate_response", "reflect")
    wf.add_edge("reflect", END)
    return wf.compile()


def _base_initial_state(**over):
    """不含 stream_sink 的基础 initial_state（key 与 chat_service._run_agent_core/continue_chat 一致）。"""
    state = {
        "user_message": "hi", "character_id": 2, "user_id": 1, "session_id": 1,
        "intent": "", "retrieved_memories": [], "context_messages": [],
        "character_info": {}, "ai_response": "", "should_update_memory": False,
        "new_memories": [], "emotional_state": "", "bio_update": None,
        "status_update": None, "source_id": None,
        "lang": "en", "reasoning_level": 0, "tools_used": [],
        "stream_sink": None, "tts": False, "voice_params": {}, "tts_subdir": None,
        "block_sink": None,
    }
    state.update(over)
    return state


async def _get_cfg(user_id):
    return None


# ── §九 3.1：注入 stream_sink 走完整 agent.ainvoke() → delta 事件 + streamed=True ──

def test_agent_ainvoke_stream_propagates_stream_sink_and_fields(monkeypatch):
    """本质回归：stream_sink/lang 经 LangGraph channel 传播，走真流式（打字机/增量 delta）。"""
    captured = {}

    async def _fake_stream(**kw):
        captured["temperature"] = kw.get("temperature")
        yield "你好。"
        yield "今天真不错。"

    monkeypatch.setattr(nodes, "chat_completion_stream", _fake_stream)
    monkeypatch.setattr(nodes, "_has_after_generate_hook", lambda: False)
    monkeypatch.setattr("app.agent.llm_client.get_user_llm_config", _get_cfg)

    events: list[tuple] = []

    async def _sink(event, payload):
        events.append((event, payload))

    agent = _build_test_agent()
    final = asyncio.run(agent.ainvoke(_base_initial_state(stream_sink=_sink)))

    # stream_sink 未被丢弃 → 产生 delta 事件（逐 token 打字机）
    deltas = [p["text"] for e, p in events if e == "delta"]
    assert deltas, "stream_sink 未传播：generate_response 走了非流式，无 delta 事件"
    assert "".join(deltas) == "你好。今天真不错。"
    # 节点回填值传回 final state
    assert final.get("streamed") is True
    assert final.get("stream_display") == "你好。今天真不错。"
    assert final.get("raw_response") == "你好。今天真不错。"
    assert "".join(final.get("stream_blocks") or []) == "你好。今天真不错。"
    assert final.get("stream_saved") == []
    assert final.get("lang") == "en"  # lang 经 channel 保留
    # temperature 由 build_context 写入 → 经 channel 传递到 generate_response（读取到 0.85）
    assert captured.get("temperature") == 0.85
    assert final.get("temperature") == 0.85
    # 真流式正文：stream_display 剥离全部标记后落库（parse_response 覆盖为干净展示文本）
    assert final.get("ai_response") == "你好。今天真不错。"


# ── §九 3.2：continue_chat 不传 stream_sink → can_stream=False 走非流式（行为不变）──

def test_agent_ainvoke_continue_without_sink_stays_non_stream(monkeypatch):
    """continue_chat 路径（无 stream_sink）行为不变：非流式，非流式 chat_completion 读取 temperature。"""
    captured = {}

    async def _fake_completion(**kw):
        captured["temperature"] = kw.get("temperature")
        return "非流式回复。"

    monkeypatch.setattr(nodes, "chat_completion", _fake_completion)
    monkeypatch.setattr("app.agent.llm_client.get_user_llm_config", _get_cfg)

    state = _base_initial_state(
        user_message="（用户没有说话，等你继续）",
        continue_payload={"last_ai_content": "之前的话"},
        lang="zh",
        # 不传 stream_sink（保持 None）
    )
    agent = _build_test_agent()
    final = asyncio.run(agent.ainvoke(state))

    assert final.get("streamed") is False
    assert final.get("stream_sink") is None
    # 非流式路径仍把 build_context 写入的 temperature 传给 chat_completion
    assert captured.get("temperature") == 0.85
    assert final.get("ai_response") == "非流式回复。"


# ── §九 1：temperature 声明后某路径未写入时 float(None) 兜底 ──

def test_agent_ainvoke_temperature_none_falls_back_to_default(monkeypatch):
    """build_context 提前返回未写 temperature（角色不存在路径）时，generate_response 用 0.8，不 float(None) 崩溃。"""
    captured = {}

    async def _fake_stream(**kw):
        captured["temperature"] = kw.get("temperature")
        yield "你好。"

    monkeypatch.setattr(nodes, "chat_completion_stream", _fake_stream)
    monkeypatch.setattr(nodes, "_has_after_generate_hook", lambda: False)
    monkeypatch.setattr("app.agent.llm_client.get_user_llm_config", _get_cfg)

    events: list[tuple] = []

    async def _sink(event, payload):
        events.append((event, payload))

    _CTRL["set_temperature"] = False  # 模拟 build_context 未写 temperature
    try:
        agent = _build_test_agent()
        final = asyncio.run(agent.ainvoke(_base_initial_state(stream_sink=_sink)))
    finally:
        _CTRL["set_temperature"] = True

    assert captured.get("temperature") == 0.8  # float(None or 0.8) → 0.8，未崩溃
    assert final.get("streamed") is True


# ── §九 4：AgentState 完整性检查（chat_service 两处 initial_state key ⊆ __annotations__）──

def test_agentstate_annotations_cover_chat_service_initial_state():
    """防未来遗漏：chat_service 注入 initial_state 的任何 key 必须在 AgentState TypedDict 中声明。

    用 ast 解析源码实际提取两处 initial_state 字面量的 key，而非硬编码，避免测试自身漂移。
    """
    from app.services import chat_service

    tree = ast.parse(Path(chat_service.__file__).read_text(encoding="utf-8"))
    dict_sets: list[set[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "initial_state" and isinstance(node.value, ast.Dict):
                    keys = {
                        k.value for k in node.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    }
                    dict_sets.append(keys)

    assert dict_sets, "未在 chat_service.py 中找到 initial_state 字面量"
    annotations = set(AgentState.__annotations__.keys())
    missing = set().union(*dict_sets) - annotations
    assert not missing, (
        f"chat_service initial_state 的 key 有 {len(missing)} 个未在 AgentState.__annotations__ 中声明："
        f"{sorted(missing)}（LangGraph 1.x 会静默丢弃，导致功能失效）"
    )
