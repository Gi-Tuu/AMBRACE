"""Agent 状态定义"""
from typing import Callable, TypedDict


class AgentState(TypedDict):
    """LangGraph Agent 的完整状态"""

    # ---- 输入 ----
    user_message: str          # 用户消息文本
    continue_payload: dict | None   # 继续指令场景：{last_ai_content}（用户点「继续」时注入）
    character_id: int          # 当前 AI 角色 ID
    user_id: int               # 用户 ID
    session_id: int            # 聊天会话 ID
    lang: str                  # 界面语言（zh/en），由服务层注入，context_builder 读取
    task_id: int | None        # 任务 ID（仅 runtime.py 直调节点函数时传入，graph 路径恒为 None）

    # ---- 处理中 ----
    intent: str                # 用户意图: "chat" / "query" / "command"
    source_id: int | None       # 当前消息ID（用于记忆关联）
    retrieved_memories: list[dict]   # 检索到的相关记忆
    context_messages: list[dict]     # 最近聊天上下文
    character_info: dict       # 角色信息（名称/人格/风格）
    temperature: float         # LLM 温度（context_builder 按角色/认知策略设定，generate_response 读取）

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
    skip_memory_save: bool     # 跳过记忆落库（社交短回复等机器生成内容，2026-08-18 Phase E）

    # ---- 认知循环（v2.1）----
    cognitive_loop_enabled: bool      # 认知循环开关（角色级，默认关）
    perception: dict | None           # 感知结果 {intent, emotion, topic, length_hint}
    plan_strategy: str | None         # 规划策略行（【策略：…；长度：…】）
    reflection_result: dict | None    # 反思结果（触发/自查/是否通过）
    active_topics: list[dict]         # 进行中的话题（conversation_topics）

    # ---- 真流式（SSE）运行时注入（2026-08-19）----
    # 以下字段由服务层在 agent.ainvoke() 前注入 initial_state，
    # 必须在 TypedDict 中声明，否则 LangGraph 1.x 会静默丢弃未声明 key，
    # 导致 stream_sink 为 None → 流式路径永不触发（打字机失效）。
    stream_sink: Callable | None      # 异步回调：(event, payload) → 发送 SSE delta/typing 事件
    tts: bool                         # 本次回复是否需要 TTS
    voice_params: dict                # TTS 语音参数（voice_id/情绪等）
    tts_subdir: str | None            # TTS 音频存放子目录
    block_sink: Callable | None       # 异步回调：发送 block 事件（流式 TTS 分块）

    # ---- 真流式输出（由 _stream_generate / generate_response 回填）----
    streamed: bool                    # 本次是否走了真流式路径
    raw_response: str                 # 含标记的原始 LLM 输出（parse_response 用）
    stream_blocks: list[dict]         # 流式切块（SEARCH/TOOL/TEXT 块，增量落库用）
    stream_display: str               # 剥离全部标记的干净展示文本（落库正文）
    stream_saved: list                # 已落库的 block id（防重复写）
