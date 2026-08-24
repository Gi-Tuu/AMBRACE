"""LangGraph Agent 工作流程图 — 认知循环 v2.1：5 节点，单次 LLM 调用

流程:
START -> perceive -> retrieve_memories -> build_context -> generate_response -> reflect -> END

- perceive: 读取角色认知循环开关，开启时执行本地感知（零 LLM）
- reflect: 概率+高风险触发本地自查（零 LLM，不重生成），结果由服务层落库
"""
from langgraph.graph import StateGraph, END
from app.agent.state import AgentState
from app.agent.nodes import (
    perceive,
    retrieve_memories,
    build_context,
    generate_response,
    reflect,
)


def build_agent() -> StateGraph:
    """构建并编译 AI 好友 Agent 工作流（认知循环 v2.1）"""
    workflow = StateGraph(AgentState)

    # 注册节点
    workflow.add_node("perceive", perceive)
    workflow.add_node("retrieve_memories", retrieve_memories)
    workflow.add_node("build_context", build_context)
    workflow.add_node("generate_response", generate_response)
    workflow.add_node("reflect", reflect)

    # 定义流程边
    workflow.set_entry_point("perceive")
    workflow.add_edge("perceive", "retrieve_memories")
    workflow.add_edge("retrieve_memories", "build_context")
    workflow.add_edge("build_context", "generate_response")
    workflow.add_edge("generate_response", "reflect")
    workflow.add_edge("reflect", END)

    return workflow.compile()


# 编译后的全局 Agent 实例
agent = build_agent()
