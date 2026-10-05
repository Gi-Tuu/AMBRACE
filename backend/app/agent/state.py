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
    channel_hint: str | None   # 渠道来源提示（任务 A）：wechat_ilink=微信桥；App 不传=图等效。仅进 LLM 上下文提示，不落库
    channel_hint_injected: bool                  # 渠道提示已注入标记（P3-2：防 loop 再决策重复累积，不依赖消息文本）

    # ---- 处理中 ----
    intent: str                # 用户意图: "chat" / "query" / "command"
    source_id: int | None       # 当前消息ID（用于记忆关联）
    retrieved_memories: list[dict]   # 检索到的相关记忆
    utility_feedback_done: bool      # 召回后效用反馈已调度标记（小增量 2026-09-16；flag 关时永不写入）
    context_messages: list[dict]     # 最近聊天上下文
    character_info: dict       # 角色信息（名称/人格/风格）
    temperature: float         # LLM 温度（context_builder 按角色/认知策略设定，generate_response 读取）

    # ---- 输出 ----
    ai_response: str           # AI 回复内容
    reasoning_level: int        # 思考过程挡位：0=关闭 / 1=简单思考 / 2=深度思考（2026-08-10）
    reasoning: str | None       # LLM 思考过程（上屏用：已做人称归一 + 穿帮脱敏，2026-09-10）
    raw_reasoning: str | None   # 原始思考（挡位 2 后台留底，不进对方可见消息，2026-09-10）
    character_name: str         # 角色名（context_builder 写入，供内心活动指令与人称归一）
    user_name: str              # 对方昵称（同上；缺失时归一兜底为「你」）
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

    # ---- 群聊上下文（#72 PR-C P3/P4）----
    # A28-S4 补声明：这两键一直只由 `runtime._build_initial_state`（社交/群聊链**直调节点**、不经编译图）
    # 写入，所以过去没暴露问题；但按本文件下方真流式字段同一条坑——**没在 TypedDict 声明的 key，
    # 一旦走编译图就被 LangGraph 1.x 静默丢弃**——声明补齐，取值口径不变（非群聊仍为 None/False）。
    group_id: int | None
    group_shared_fact: bool

    # ---- 认知工作台（A28-S3，2026-10-05）----
    # 必须在此声明，否则 LangGraph 1.x 静默丢弃（与下方真流式字段同一个坑）。
    # 第一阶段**只写不读**：节点往里挂 Observation / focus / decision，没有任何 prompt 段消费它
    # ⇒ 回复内容逐字节不变；等第二／三阶段再把它投影进上下文。
    workspace: object          # CognitiveWorkspace 实例（app/agent/workspace.py；纯运行时对象，不落库）
    observations: list[dict]   # 扁平观测列表（与 workspace.observations 同一批记录的引用口径）
    decision: dict | None      # 本轮决定（含 reason），第一阶段由 reflect 等就地写入

    # ---- 跨节点必声明（A29，2026-10-05 普查后补）----
    # LangGraph 1.x 只把这里声明过的键带出节点：没声明＝节点内改了也白改，下游静默读到 None。
    # 下面两条都是「早就有写有读、但因为没声明而一直没生效」的历史遗漏（普查见 plans 的 A29 行），
    # 补声明＝让它们在各自注释里描述的行为真正发生，故各配一条"能穿过图"的守卫。
    marker_truncated: bool              # parse_response 置位 → 服务层据此走证据B兜底／通道B优先补提
    _host_user_msg_index: int | None    # build_context 记宿主 user 下标 → 红线②按真锚点判越位

    # ---- 真流式（SSE）运行时注入（2026-08-19）----
    # 以下字段由服务层在 agent.ainvoke() 前注入 initial_state，
    # 必须在 TypedDict 中声明，否则 LangGraph 1.x 会静默丢弃未声明 key，
    # 导致 stream_sink 为 None → 流式路径永不触发（打字机失效）。
    stream_sink: Callable | None      # 异步回调：(event, payload) → 发送 SSE delta/typing 事件
    tts: bool                         # 本次回复是否需要 TTS
    voice_params: dict                # TTS 语音参数（voice_id/情绪等）
    tts_subdir: str | None            # TTS 音频存放子目录
    block_sink: Callable | None       # 异步回调：发送 block 事件（流式 TTS 分块）
    character_states_snapshot: dict | None  # M1-S10：本轮八维+trust 快照（延迟/情感/life_share 复用，免重复查库）

    # ---- 真流式输出（由 _stream_generate / generate_response 回填）----
    streamed: bool                    # 本次是否走了真流式路径
    raw_response: str                 # 含标记的原始 LLM 输出（parse_response 用）
    stream_blocks: list[dict]         # 流式切块（SEARCH/TOOL/TEXT 块，增量落库用）
    stream_display: str               # 剥离全部标记的干净展示文本（落库正文）
    stream_saved: list                # 已落库的 block id（防重复写）
