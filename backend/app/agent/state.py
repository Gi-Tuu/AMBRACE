"""Agent 状态定义"""
from typing import TypedDict


class AgentState(TypedDict):
    """LangGraph Agent 的完整状态"""

    # ---- 输入 ----
    user_message: str          # 用户消息文本
    continue_payload: dict | None   # 继续指令场景：{last_ai_content}（用户点「继续」时注入）
    character_id: int          # 当前 AI 角色 ID
    user_id: int               # 用户 ID
    session_id: int            # 聊天会话 ID

    # ---- 处理中 ----
    intent: str                # 用户意图: "chat" / "query" / "command"
    source_id: int | None       # 当前消息ID（用于记忆关联）
    retrieved_memories: list[dict]   # 检索到的相关记忆
    context_messages: list[dict]     # 最近聊天上下文
    character_info: dict       # 角色信息（名称/人格/风格）

    # ---- 输出 ----
    ai_response: str           # AI 回复内容
    reasoning_level: int        # 思考过程挡位：0=关闭 / 1=简单思考 / 2=深度思考（2026-08-10）
    reasoning: str | None       # LLM 思考过程（角色开启「思考过程」开关时产生，2026-08-10）
    tools_used: list[str]       # 本次回复调用的能力（识图/生图/语音回复/扩展，2026-08-10）
    should_update_memory: bool # 是否需要存入新记忆
    new_memories: list[dict]   # 新发现的记忆
    emotional_state: str       # AI 情绪状态标记
    bio_update: str | None       # AI 自述更新内容
    status_update: str | None     # AI 状态更新内容

    # ---- 认知循环（v2.1）----
    cognitive_loop_enabled: bool      # 认知循环开关（角色级，默认关）
    perception: dict | None           # 感知结果 {intent, emotion, topic, length_hint}
    plan_strategy: str | None         # 规划策略行（【策略：…；长度：…】）
    reflection_result: dict | None    # 反思结果（触发/自查/是否通过）
    active_topics: list[dict]         # 进行中的话题（conversation_topics）
