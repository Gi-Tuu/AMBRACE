"""受控 Agent Loop（Phase B，2026-08-16）

把 [SEARCH] 的「二次生成」泛化为受控 decide→execute→observe 循环：
- decide：LLM 输出正文 + 动作标记（现有 generate_response / agent.ainvoke）
- execute：解析动作并执行工具（搜索；失败自动重试 1 次，单工具超时 30s）
- observe：工具结果注入为带标注上下文（【搜索结果】），条件满足再决策（补查）

统一限制（方案 5.3）：最多 2 次搜索 / 3 次 LLM 调用；节流或搜索失败、
超限 → 静默降级（剥离标记、不编造成功）。
（agent_loop_search 已于 2026-09-17 固化为恒定受控多轮搜索，不再经 flag 控制。）
"""
import asyncio
from typing import Awaitable, Callable

from app.agent import actions as _actions
from app.utils.logger import get_logger

_logger = get_logger("agent.loop")

# 统一限制（方案 5.3：max_steps=3 含最终回复 → 最多 2 次真实搜索）
MAX_LLM_STEPS = 3  # LLM 调用轮数上限（首轮 + 2 次再决策）
MAX_SEARCH_ROUNDS = MAX_LLM_STEPS - 1
MAX_RECALL_ROUNDS = 1  # Ariadne 模块 B：记忆二跳最多 1 次（防无限检索/拖慢回复；与 SEARCH 二跳同上限）

# 记忆二跳结果注入模板（observe；与 _SEARCH_RESULT_TEMPLATE 同位）
_RECALL_RESULT_TEMPLATE = (
    "【补充记忆】（你主动调取了更早/更相关的记忆，现在直接结合它们与已有上下文回复；"
    "不要说'我查了一下记忆'。若与上方记忆冲突，以时间更晚、认知状态为 FACT 的为准）：\n{result}"
)
TOOL_TIMEOUT_SEC = 30.0  # 单工具执行超时
SEARCH_RETRY = 1  # 只读工具失败自动重试次数（方案 5.2）

# Feature Flag（2026-08-17 开源包基线：全部默认开启；各 Flag 作用/前值/回滚方法见 docs/feature-flags.md）：
# agent_loop_search：2026-09-17 固化——恒定走受控多轮搜索（曾为灰度开关；关=退回旧单次二次生成，现恒定开，不再经 flag 控制）
# agent_loop_scheduler 开=arbiter 主动任务写 AgentTask trace（含 10% 角色 route=scheduler_gray 对比标记 + 灰度角色真实任务记录）；
# agent_loop_chat 开=主链路日历/备忘等本地工具经统一执行入口 execute_tool；
# agent_tool_events（已固化常开，2026-09-17 用户拍板：功能常驻不下放）：工具执行联动织库增量（tool.executed 订阅）
# agent_trace_group 开=群聊角色回应写 AgentTask trace（只写不读可观测）；
# agent_daily_reflection（已固化常开，2026-09-17 用户拍板：功能常驻不下放）：周复盘（每 7 天 1 次）
# agent_reflection_inject（已固化常开，2026-09-17 用户拍板：功能常驻不下放）：主动消息注入最近复盘（反思驱动）
# agent_context_trim 开=认知注入按角色热度裁剪（低频角色缩小日摘要/织库）；
# agent_loop_group_chat 开=群聊回应走统一 Runtime（逐角色 build_context 注入世界认知，知识不串线）；关=旧单次 JSON 链路（Phase E，2026-08-18 全量开启）；
# agent_loop_social 开=渠道/插件主动候选走统一 Runtime（世界认知注入 + 防 hint 污染记忆）；关=旧裸生成链路（Phase E，2026-08-18 全量开启；X5 渠道化时按渠道语义改名，行为不变）；
# agent_social_light_context 开=群聊/渠道社交短回复走轻量上下文（跳过完整世界认知，单次 prompt ≈-64%；F1/F2，2026-08-18 全量开启）；关=全量 build_context（回退）
AGENT_FLAGS = {
    "agent_loop_scheduler": True,  # 2026-08-17 全量基线（开源包）：主动任务 trace + 10% 灰度 route 对比
    "agent_loop_chat": True,
    # 曾为灰度开关，2026-09-17 固化（用户拍板：功能常驻不下放）：agent_tool_events（工具执行联动织库增量）
    "agent_trace_group": True,
    # 曾为灰度开关，2026-09-17 固化（用户拍板：功能常驻不下放）：agent_daily_reflection（周复盘）
    # 曾为灰度开关，2026-09-17 固化（用户拍板：功能常驻不下放）：agent_reflection_inject（主动消息注入最近复盘）
    "agent_context_trim": True,  # 认知注入按角色热度裁剪（2026-08-16）
    # 曾为灰度开关，2026-09-17 固化（用户拍板：功能常驻不下放）：agent_daily_memory_maintenance（日终记忆维护）
    "agent_loop_group_chat": True,  # Phase E（2026-08-18）：群聊回应走统一 Runtime（2026-08-18 用户拍板全量体验；回退改 False 重启即恢复旧链路）
    "agent_loop_social": True,  # Phase E（2026-08-18）：渠道/插件主动候选走统一 Runtime（X5 渠道化改名；回退改 False 重启即恢复旧链路）
    "agent_social_light_context": True,
    "weave_3d": True,  # 织网 3D（P2 转默认开；仅客户端画布读它选 2D/3D 视图；低端机客户端自动降级 2.5D）  # F1/F2（2026-08-18 用户拍板全量开启）：群聊/渠道社交短回复走轻量上下文（单次 prompt ≈-64%；回退改 False 重启即恢复全量 build_context）。与 agent_loop_group_chat/agent_loop_social 正交：前者管走不走 Runtime，后者管 Runtime 内是否用轻量上下文
    "proactive_naturalness_score": True,  # #28 ①（2026-08-24）：低优先主动消息自然度评分——生成后按规则评分，低于阈值重试 1 次/仍低则降级跳过；关=纯现状
    "proactive_user_rhythm": True,  # #28 ②（2026-08-24）：用户作息学习——从聊天/主动日志推断活跃时段，低优先主动消息在时段外降优先级/推迟；关=纯现状
    # 群聊游戏 Phase 1（2026-08-26）：总开关=群聊游戏（各游戏/记忆指针/AI 自动回合开关已于 2026-09-17 删除，功能常驻，不再下放修改按钮）
    "group_chat_games": True,        # 游戏总开关（关=游戏入口/API 不展示，可回退）
    # ── M1 记忆 P0（2026-08-31，docs/archive/architecture/执行方案_记忆与生成_20260831.md S1）──
    # 曾为灰度开关，2026-09-17 固化（用户拍板：功能常驻不下放）：recall_top5（主路召回出口 5 条）
    "memory_temporal_recall": False,  # Ariadne 模块 A（2026-09-03）：时间维度确定性检索路（默认关=零行为变化；开=用户原话解析出时间区间时补一条确定性时间路召回，与语义路合并重排）
    "memory_recall_second_hop": False,  # Ariadne 模块 B（2026-09-04）：按需二跳联想检索（默认关=只剥离 [RECALL] 标记零行为变化；开=非流式路径镜像 run_search_loop：首轮输出 [RECALL]查询词[/RECALL] → 本地检索 → 注入【补充记忆】→ 再生成 1 次；流式只剥离不中途二跳）
    "memory_story_assemble": False,  # Ariadne 模块 C（2026-09-04）：沿链半故事化组装（默认关；链建链器另案——空 index 时即使开 flag 也走原路径逐字节等价；建链器落地后开=成链小块注入）
    # ── B1-② 记忆链条建链器（2026-09-04，方案 §10-§18，阶段 C0-C5）──
    # memory_chain_builder 开=写入后异步挂链（chain_id/parent_id/node_type，零额外 LLM，
    #   复用 save_memory 已算 embedding，只对 event/insight）；关=不挂链（回归保护）。
    # memory_chain_expand 开=检索命中沿链补≤2 相邻节点（降权 0.9、受 token 配额与 5 轮去重约束）；关=原注入。
    # proactive_outreach_v2（B1-③，2026-09-04，方案 §1-§9）=主动消息自然化总开关——
    #   开=run_tick 汇总层按「闲置分级+素材前提+避开最近意图」选接触意图，message_generator 走意图分支；
    #   关=意图不参与、走旧链路逐字节等价（默认关，可灰度/一键回退）。
    "memory_chain_builder": False,
    "memory_chain_expand": False,
    "proactive_outreach_v2": False,
    # B1-③ 配额让位（2026-09-08，用户拍板）：proactive_inactive_char_skip 开=近 24h 内无任何用户
    #   消息的角色直接停发主动搭话（greeting/proactive_chat/goodnight/status_update/motivation）——
    #   不生成候选、不占每日配额、不写 approved 日志，额度留给有互动的角色（当前=char13）；
    #   关（或 INACTIVE_CHAR_WINDOW_HOURS<=0）=零行为，一键回退。
    "proactive_inactive_char_skip": True,
    # ── outreach 投放口径三闸（2026-09-13，Codex 交接 §二）──
    # 三个独立开关，**全部默认 False = 逐字节现状**；置 False 即一键回退（runtime_flags 可热切，
    # 键已在 AGENT_FLAGS 登记，重启加载新代码后即可经 flag_service 热改）。
    # 灰度：开关开 **且** 角色命中 domain/proactivity/pacing.py 的 OUTREACH_PACING_GRAY_CHARS
    #   （当前仅 char13）+ 比例桶（1.0）才生效；关=不查库、不拦截、零行为变化。
    # ① outreach_hour_window_v1：低效类型（ai_care/life_regression/memory_review）仅
    #    12:00–23:00（北京时间）投放，窗口外跳过（个性化活跃时段只扩不缩）；
    # ② outreach_type_mix_v1：memory_review ≤6/日、ai_care ≤4/日（按已发送计数），并把
    #    memory_review 生成提示词改成「结尾带一个具体、可回答的问题」（可回复化）；
    # ③ outreach_session_rate_v1：同 (character_id, session_id) ≤8/日 且最小间隔 45 分钟
    #    （与 MAX_PER_HOUR 叠加，不替换；同样按已发送计数）。
    # 命中留痕：proactive_trigger_logs.trigger_reason 带 [gate=hour|type|session_rate]。
    "outreach_hour_window_v1": False,
    "outreach_type_mix_v1": False,
    "outreach_session_rate_v1": False,
    "memory_peak_cutoff": False,  # Ariadne 模块 D（2026-09-04）：自然收敛替代硬截断（默认关；开=按 rerank 分数断档/地板收敛，弃权/弱相关场景条数自然减少；阈值经模块 E v2 标定）
    # 曾为灰度开关，2026-09-17 固化（用户拍板：功能常驻不下放）：recall_diversify（按类型多样性重排）
    # ── Life Loop v1.1（2026-08-26；2026-08-27 用户拍板全量开启）──
    "life_loop_enabled": True,            # 主开关：30min 行为决策循环
    "life_loop_llm": True,                # 允许 LLM 生成生活文案（每角色每日≤2次）
    "life_chat_driven_enabled": True,     # 聊天→生活意图链路
    "review_daily_plus": True,            # M1-S7（2026-08-31）：主动复习日额度 3→4（关=回退 3；90min 间隔不变）
    "memory_tiered_decay": False,         # M2-S2（2026-08-31）：分层衰减——高置信持久/低置信加速/跌破阈值冷归档。默认关（灰度开关，开启前先跑 scripts/diagnostics/memory_tiering_snapshot.py 快照）；关=逐字节现状
    "marker_recovery": True,              # M2-S5（2026-08-31）：标记截断保底——A 通道标记被截断时本条源消息立即走通道 B 提取（写侧查重防重复）；关=仅批量补提
    # ── X3 Provider 端口（2026-08-31，docs/archive/architecture/执行方案_扩展化_20260831.md 批次 X3）──
    # provider_registry 开=LLM/TTS 经 app/providers 注册口解析实现（内置 openai_compatible/dashscope 为默认实现，
    # 插件可经 sdk.register_provider 注册并以配置 provider 字段选中）；关=直连内置实现（与旧链路逐字节一致）。
    "provider_registry": True,
    # ── M3-a 工作记忆（2026-09-01，docs/archive/architecture/设计_M3工作记忆_20260901.md）──
    # working_state_enabled 开=turn 结束后异步评估/滚动覆盖 working_state 行（写入链路，fail-open）；
    # 关=完全跳过（无行产生；注入为 M3-b 另行灰度）。默认关（快照脚本可回滚）。
    "working_state_enabled": True,  # 2026-09-01 用户拍板：开启数据积累（注入仍为 M3-b 未灰度）
    # M3-b（2026-09-07）：工作记忆注入**全量**开关。关=只按角色小流量灰度（见 section_working_state.py
    # 的 WORKING_STATE_INJECT_GRAY_CHARS / WORKING_STATE_INJECT_RATIO，当前仅 char13 全量会话）；
    # 开=所有角色注入。用于后续扩量与热回滚（回退=置回 False）。
    "working_state_inject": False,
    "life_home_worldmap_enabled": True,   # 小家大地图（§11）
    # ── 生命感增强 v1（#63，2026-08-27；全部默认关，可独立回退）──
    "reply_delay_enabled": True,         # 机制2：动态回复延迟（用户主动消息才生效）
    "spring_emotion_enabled": True,      # 机制1：弹簧-阻尼情绪（4 维 + 人格基线）
    "life_share_enabled": True,          # 机制4：活动完成自然分享（arpiter 门控 + 配额）
    "preoccupation_enabled": True,       # 机制5：心事微澜（复用 Memory.sub_type）
    # ── #70 方案A：记忆分层检索与注入（2026-08-30；独立可回滚）──
    # memory_tiered_inject 开=Top1 L2(240)/其余 L0 分层注入 + L1 桥接 + L0 参与向量；关=统一 150 字旧链路（逐字节一致）。
    "memory_tiered_inject": False,
    # ── #70 方案B：检索轨迹可观察（2026-08-30；独立可回滚）──
    # memory_trace_debug 开=memory_search trace 补 query/派生/各路命中/RRF/rerank 分数/最终注入（只多写 trace，低风险默认开）；
    # 关=检索/排序/trace 与现状逐字节一致（回归保护）。
    "memory_trace_debug": True,
    # ── #70 方案C：记忆取代链 + 级联失效（M1/M2）+ 冷归档/purge（2026-08-30；独立可回滚）──
    # memory_supersede 开=superseded/stale 状态激活，双通道（SQLite+Chroma）过滤，读取点按状态分流；
    # 关=所有读取/注入/统计与现状逐字节一致（回归保护）。禁止默认 True（误取代比不取代更伤）。
    "memory_supersede": False,
    # ── 2026-09-17 批次一（任务2）：现状面 / 怀旧面拆口径（docs/feature-flags.md F 档）──
    # current_facts_active_only 开（默认）= 现状/事实注入面（current_facts_status_clause /
    #   _active_status_clause）恒「仅 active」——stale/superseded/expired 的旧现状不再与现行事实
    #   同分竞争（线上 DeepSeek/Dom 反复「你在长沙」的根因）；同时写侧向量查重只与现行向量比对。
    #   怀旧/复习面（_retrievable_status_clause / 向量 / BM25 默认路）仍保留 stale 可见，
    #   且 stale 在 rerank 恒降权 0.5（降权不再受 memory_supersede 门控）。
    # 关 = 一键回退旧行为（status 子句退回 memory_supersede 门控，关=永真）。
    "current_facts_active_only": True,
    "vector_user_scope": False,  # A1（2026-09-19）向量账号归属：开=读取按 metadata.user_id/角色 owner 过滤（写入始终带）；关=逐字节旧行为。本键必须登记，否则 DB 里开了也不生效
    # ── #70 附录 C 可选 M3：记忆写入回执（memory_write_receipt，2026-09-15 落地；默认关=零写入、零行为变化）──
    # memory_write_receipt 开=save_memory 写分支 / supersede_memory 异步写 memory_write_receipts
    #   （终态追踪「这条记忆为什么在/不在」）；关=完全跳过（不写不读，逐字节旧链路）。
    "memory_write_receipt": False,
    # Ariadne 模块F（2026-09-04）：Curated Knowledge 编纂知识层（world_facts 加 kind 分治 + 确定性注入）
    "curated_knowledge": False,
    # Ariadne 模块G（2026-09-04）：前瞻意图。enabled=写入（extractor 便车落表）；
    # trigger=触发（时间型 Scheduler 提起 + 线索型 context 注入）。两段灰度：先开 enabled 攒数据，再开 trigger。
    "prospective_intent_enabled": False,
    "prospective_intent_trigger": False,
    # ── §20 跨角色用户事实（2026-09-04，默认关=零行为变化；bool 可 runtime 热更）──
    # global_user_facts：用户级可变事实层总开关——开=GPS/跨角色事实写入 + [USER NOW] 注入分区；
    #   关=不写/不读 user_facts（抽取出原路径、注入空）。
    "global_user_facts": False,
    # ── 细粒度槽开关（2026-09-10，用户拍板；先只搭框架，【全部默认关，含 location】；
    #    真机观察 C2 新鲜窗 / 回家识别 / C3 锚点稳定后，再经 runtime flag 手动只开 location）──
    # 语义：总闸开=全槽启用；总闸关时按各槽 flag 独立决定（user_fact_slot_enabled）。
    "user_fact_location": False,      # 位置：最不敏感、最易过时；GPS/城市/聊天归槽写 location
    "user_fact_job": False,           # 工作/学业
    "user_fact_relationship": False,  # 感情状态（隐私，默认关）
    "user_fact_living": False,        # 居住状况（独居/和谁住）
    "user_fact_goal_state": False,    # 近期目标/状态
    "user_fact_health": False,        # 健康（隐私，默认关）
    # ── 2026-09-17 批次二（任务2）：位置类不吃细槽总闸（跨角色共享用户权威现状）──
    # user_current_location_share 开（默认）= 读取/注入侧独立放行 location 槽（共享读路径
    #   get_shared_user_facts / get_authoritative_user_location / 现状锚点 / 定时兑现锚点 /
    #   主动消息·朋友圈·生活生成器），不受 global_user_facts 与细槽 flag 门控——修复低活跃朋友
    #   角色拿不到权威位置、只靠各自旧「长沙」碎片并被 AI 朋友圈写回放大的回声腔。
    #   写侧细槽门控（user_fact_slot_enabled）保持不变；relationship/health 两槽仍 opt-in
    #   （红线：共享读路径只从 enabled_user_fact_slots() 取槽，永不旁路这两槽）。
    #   关 = 一键回退：共享读路径不再包含 location（逐字节回到仅启用槽）。
    "user_current_location_share": True,
    # cross_char_fact_sync：跨角色对齐——开=角色构建上下文前惰性对齐 + 每日 sweep，把同槽旧值
    #   per-char 记忆标 stale（复用 #70，不删可追溯）；关=不对齐。
    "cross_char_fact_sync": False,
    # cross_char_fact_projection：变化投影——开=对齐时按模板投影一条 global_sync 记忆进记忆本
    #   （零 LLM，skip_dedup）；关=只标 stale + 靠 [USER NOW] 注入（默认推荐关）。
    "cross_char_fact_projection": False,
    # ── 一机多主 / 渠道绑定 per-账号化（2026-09-05，交接拍板，2026-09-17 落地默认开；关=回落旧路径）──
    # channel_binding_v2 开=渠道绑定读 channel_bindings 新表（租户隔离，读写走 ChannelBindingService）；
    #   关=渠道插件/读取层回落旧全局 config allowed_character_ids 串（单主部署语义等价，零行为变化）。
    #   新表/新列迁移幂等常驻（alembic a7b8c9d0e1f2），关 flag 即全链路回退、无数据删除。
    "channel_binding_v2": True,  # 已转正（2026-09-17 落地默认行为）；回退＝置 False 或运行时关 flag
    # ── 3.10 chat/moment 事件流水（2026-09-08，方案路线 A：outbox-lite）──
    # domain_event_log_enabled 开=业务 commit 成功后以独立 session 追加一条 append-only 领域事件
    #   （domain_events 表；只写不读，主表仍是唯一权威读源，不是经典 Event Sourcing）；
    #   关=append_domain_event 首行即 return，全链路零写入、零行为变化（一键回退，无需回滚代码/迁移）。
    #   默认关（灰度）；开法：本 key 已登记进 AGENT_FLAGS，重启服务加载新代码后即可经
    #   flag_service.set_runtime_flag（写 runtime_flags 行 + 热更新内存）API 热切，无需再重启。
    "domain_event_log_enabled": False,
    # domain_event_retention_days：事件流水保留天数（P1，方案 §8.5）。0=永久保留（本地优先默认）；
    #   >0 时由定时清理任务（每 6h）删除超期 domain_events 行。runtime_flags 只支持 bool 覆盖，
    #   本项为硬编码默认值；误配非法值按 0 处理（宁可多留不误删）。
    "domain_event_retention_days": 0,
    # ── 工具轨迹治理 R1（2026-09-09，方案 §4.1）──
    # agent_trace_scheduler_only_executed 开=主动任务「本轮未触发」（_execute 正常 return False、
    #   未抛错）不再写 agent_task_logs（止血写放大），评估流水回归 proactive_trigger_logs
    #   （已有 5min 节流）；关=回到旧「每候选一条 blocked」。
    # agent_trace_scheduler_mark_exec_error 开=真正进入执行却失败（_execute 抛错）记 status=error
    #   （而非 blocked），保留「真失败」可观测性；关=回 blocked。
    "agent_trace_scheduler_only_executed": True,
    "agent_trace_scheduler_mark_exec_error": True,
    # ── 工具轨迹治理 R6（2026-09-09，方案 §4.6）──
    # chat_tools_list_real_only（已固化常开）：私聊气泡「调用能力」只列本轮真实用到的能力（中文、去重、
    #   上限、隐藏内部工具），不再把「全部启用中插件」的英文 id 拼成一坨（2026-09-17 固化常开，不再回退旧行为）。
    # 曾为灰度开关，2026-09-17 固化（用户拍板：功能常驻不下放）：chat_tools_list_real_only（私聊气泡只列本轮真实能力）
    # ── 工具轨迹治理 R3（2026-09-09，方案 §4.3.1）──
    # mcp_stream_declarations 开=流式会话也注入 MCP 工具声明（前提：#59 流尾 tool_result 通道已上线，
    # 见 application/chat/streaming.py run_stream_mcp_tool_stage + sink("tool_result", …)）；
    # 关=流式不注入（旧行为，零变化）。默认关（灰度验证后再全量）。
    "mcp_stream_declarations": False,
    # ── 工具轨迹治理 R5（2026-09-09，方案 §4.5）──
    # agent_tool_exec_trace 开=插件/内置工具每次执行（tool.executed 事件）落一条 agent_task_logs
    # （trigger=tool），让「工具轨迹」能看到真实工具成败；关=不写（默认，零写放大）。
    # MCP 工具不落（已有 mcp_call_logs，前端 MCP 分区读取，避免双记）。
    "agent_tool_exec_trace": False,
    # ── 主动复习「回忆化」+ 过期计划记忆治理（2026-09-09，L0-L4）──
    # 设计意图（用户定调）：复习=回忆/怀旧，把旧记忆当往事回味，不当"当前仍成立/即将发生"续写叮嘱。
    # review_exclude_expired_plan 开=复习选片/情境复习排除过期计划与瞬时状态（L1，默认开；关=旧选片）；
    # review_reinforce_event_cap 开=一次性事件经"主动复习成功"强化按 tense 分流收口（L2：过期计划
    #   S≤10/次数≤3，往事适度 S≤30/次数≤6，达上限退出复习轮转；检索/写入通道不受影响；默认开）；
    # review_reminisce_framework 开=复习 hint 改「回忆框架」+ 时态口吻 + 现状锚点 + 输出本地闸门
    #   （L3，默认开；关=逐字节回旧 hint）；
    # review_plan_expire_stale 开=每日维护把过期计划自动置 stale（L4，默认关灰度）；
    # review_plan_validity_extract 开=提取/写入侧给计划写 valid_to（L4，默认关灰度）。
    "review_exclude_expired_plan": True,
    "review_reinforce_event_cap": True,
    "review_reminisce_framework": True,
    "review_plan_expire_stale": False,
    "review_plan_validity_extract": False,
    # ── 记忆注入行时态标注（2026-09-10，第三轮 T3/C1）──
    # memory_line_tense_tag（已固化常开）：format_memory_line 在 [记录于] 之后插时态标签：plan 未过期=［计划］、
    #   已过期=［旧安排·已过期］、episodic=［往事］、transient=［当时状态］、enduring 不加；
    #   纯提示词标注，不动召回/排序/写库（2026-09-17 固化常开，不再回旧行）。
    # 曾为灰度开关，2026-09-17 固化（用户拍板：功能常驻不下放）：memory_line_tense_tag（format_memory_line 插时态标签）
    # ── AI 生活主动消息「主体归属 + 同主题复读 + 零上下文催促」治理（2026-09-09，L0-L5）──
    # 设计意图（用户定调）：AI 自己去做的事（吃饭/洗澡/开会）到点应**自述回来**，不该反过来
    #   招呼用户；同一生活主题在数小时内被 timer/state_trigger/memory_review/life_regression/
    #   storyline 五条通道各催一遍要收口；用户已回应/已离场就该停。全部零 LLM 优先、fail-open。
    # promise_self_side_split 开=创建侧按受益方分流（AI 自理→back 到点自述「我回来了」，
    #   为用户做才 ready）；**注意这是新增主动消息源**（AI 自理从「完全不建事件」变为「建 back」），
    #   不是纯 bug 修复，需与 timer_render_subject_fix 同批灰度；关=沿用 F1a 现状正则结果。
    # timer_render_subject_fix 开=到期渲染按 (owner, event_type) 三套话术 + 现状锚点 +
    #   __SKIP__ 闸门 + 删掉硬编码「粥好了」few-shot 例子；关=走 _build_timer_hint_legacy 逐字节等价。
    # proactive_topic_guard 开=主题熔断（timer 发送前判 + send_to_session 统一兜底 + ready
    #   闭环检查扩到 owner=ai 并纳入离场/婉拒词）；关=不做任何抑制。
    # life_event_no_replay 开=一次性生活动作（source=life 的 event）不进主动复习、不被
    #   life_regression 高频复读（与「回忆化」L1 同处一个筛选段，共用 flag 体系）；关=维持现选片。
    # life_memory_write_retry 开=life 写记忆加固（写前先提交释放自持锁 + 统一退避重试 +
    #   悬空 started 收尾）；**默认开（纯加固）**，关=回旧裸写路径。
    "promise_self_side_split": False,
    "timer_render_subject_fix": False,
    "proactive_topic_guard": False,
    "life_event_no_replay": False,
    "life_memory_write_retry": True,
    # ── #72 PR-C 群聊认知升级（group_cognition_v2，2026-09-15）──
    # group_cognition_v2 开=群聊认知能力（P1 纯数据层 + P2 共享记忆接线已合；P3 逐角色认知
    #   生成+私有注入+预算+仲裁、P4 观测+群级灰度列生效 待后续拆包）。总开关：本项默认关，
    #   app/memory/group_memory.group_cognition_on() 已读该键、异常即 False——关=零行为变化
    #   （PR-B 双轨均未接线状态原样保留）；开 + 群级 chat_groups.cognition_enabled 才走双轨。
    "group_cognition_v2": False,
    # ── #72 PR-C P5 群记忆日终合并收敛（2026-09-16，默认关=零行为变化）──
    # group_memory_compact 开=每日 23:00 后把 >7 天的群记忆按群合并成 1 条 system 摘要、
    #   旧行软删（is_archived=1，留痕不物理删）；关=完全跳过、逐字节现状。
    "group_memory_compact": False,
    # ── 批次二（2026-09-16）：M4 world_facts 写入准入闸门（补登记，2026-09-17 Codex 复核发现）──
    # 开＝world_facts 写入前做元信息拦截 / 同义查重合并 / 矛盾冲突改走裁决（app/events/facts.py）；
    #   关＝逐字节旧行为。**此前只在 facts.py 读取、未登记进本表**，导致 runtime_flags 写 1 也不生效
    #   （flag_service 只合并「已登记键」）——灰度会白开，故补登记。
    "memory_admission_gate": False,
    # ── 批次四（2026-09-16）：主动消息分块口径护栏（允许分块，禁止残句/空块；生成前校验现实约束）──
    # 开＝分块时过滤残句/空块并做现实校验；关＝逐字节现状。
    "proactive_segment_guard": False,
    # ── 小增量（2026-09-16）：召回后效用反馈（Slowave 式），用于调 salience/衰减 ──
    # 开＝召回后异步写轻量反馈并据此微调；关＝零行为变化。
    "memory_utility_feedback": False,
    # ── X6 主动内容策略包外放（2026-09-16，默认关=逐字节旧行为）──
    # proactive_strategy_plugins 开＝「内容策略」交给策略包：内核向 proactive_candidate hook
    #   下发 roster（选人结果），被接管的策略源（本轮仅 special）整体让位；关=内核各策略源
    #   照旧产出、hook ctx 不带 roster（策略包返回空），与现状逐字节一致。
    #   防双发两道闸：①让位（同一类别只留一个生产者）②内核按 (角色, message_type, 北京日界)
    #   去重（策略候选落库口径由内核校验，见 scheduling/sources/strategy.py）。
    #   回退：置回 False 即可（runtime_flags 热切，无需重启）。
    "proactive_strategy_plugins": False,
    # ── A2 M0-3（2026-09-20）：内核「已禁用插件」路由闸（插件归户批次）──
    # 开＝插件 bridge / chat / 页面托管端点在插件 enabled=False 时一律 404（判定口径统一取
    #   registry.get_plugin(name)["enabled"]，即 DB plugins.enabled 的内存缓存）；
    #   关＝**逐字节旧行为**（只判插件是否存在，不判 enabled，与现状一致）。默认关=灰度门控，
    #   本键必须登记，否则 DB/runtime_flags 里开了也不生效（flag_service 只合并已登记键）。
    "plugin_disabled_route_gate": False,
    # ── A2 M3（2026-09-20）：插件列表按账号收敛（插件归户批次）──
    # 开＝插件列表只显示「内置 + 调用者家庭安装 + 服务级（owner 为空）」的插件，市场 installed 标记
    #   随同一可见集重算；调用者家庭解析失败 → 只保留内置与服务级（最保守集合，绝不全放）；
    #   关＝**逐字节旧行为**（列表全量，不做任何归属过滤）。默认关=灰度门控。
    #   本键必须登记，否则 DB/runtime_flags 里开了也不生效（flag_service 只合并已登记键）。
    "plugin_user_scope": False,
    # ── A2 M4（2026-09-20）：插件运行面按账号过滤（插件归户批次）──
    # 开＝插件的每一条运行面通路都有归属闸：hook 分发 / 工具登记 / prompt 注入 / 桥调用 /
    #   页面托管都只对本调用者「可见」的插件生效（复用 M3 谓词 + 30s 缓存），sdk 原语
    #   （save_memory / send_message / get_persona / search_memory / get_relationship /
    #   get_life_state）断言自报 user_id/character_id 属于本 caller 家庭根，越权抛 PermissionError；
    #   proactive_candidate 做角色归属配对校验；未登记 CATEGORY_KERNEL_PREP 的类别走通用闸。
    #   拿不到 caller 的调用点一律 fail-closed（非内置插件不分发）。关＝**逐字节旧行为**。默认关=灰度门控。
    #   本键必须登记，否则 DB/runtime_flags 里开了也不生效（flag_service 只合并已登记键）。
    "plugin_runtime_scope": False,
    # ── two-pass POC（2026-09-23，雷达 §3「two-pass 重读」/ 方案书 AMBRACE_two-pass_POC方案）──
    # 开=主动消息生成**前**拼一块确定性「现状 trace」，前置到长历史/系统块之前（零 LLM、只读、
    #   trace 不落库不写记忆不上屏）；关=不构造、不注入、不多一次查询（逐字旧行为）。
    # 灰度双条件：本开关开 **且** 角色命中 scheduling/message_generator.py 的
    #   TWO_PASS_TRACE_GRAY_CHARS（沿用 char13 灰度先例，当前仅 char13）。
    # 回退：置回 False（runtime_flags 热切，无需重启）。默认关=零行为变化。
    "two_pass_trace": False,
    # ── P1 压缩存活项清单（2026-09-23，雷达 §2）──
    # 开=上下文装配时注入一块确定性「存活项清单」（当前目标 / 未决问题·计划 / 硬约束），并拼进
    #   日摘要生成 prompt 要求这些字段原文保留；清单块在 system 超预算裁剪时优先级 2，
    #   只有【系统指令】/【本轮提醒】比它高（＝最后才被动）。只读、零 LLM、不落库、不上屏。
    # 关=逐字旧行为（不多查库、不改 prompt、不注入块）。
    # 灰度双条件：本开关开 **且** 角色命中 agent/context_builder.py 的
    #   SURVIVAL_CHECKLIST_GRAY_CHARS（沿用 char13 灰度先例，当前仅 char13）。
    # 回退：置回 False（runtime_flags 热切，无需重启）。默认关=零行为变化。
    "survival_checklist": False,
    # ── P2a 上下文预算预留（2026-09-23，雷达 §3）──
    # 开=系统块总硬顶先减去两块预留（回复 REPLY_RESERVE_TOKENS=800 + 工具声明
    #   TOOL_DEFS_RESERVE_TOKENS=500，见 agent/context_builder.py）再判定超限裁剪，
    #   工具声明这类分区不再挤占回复额度；且真发生裁剪时，quota_clipped_sections 埋点
    #   detail 补齐 {budget, used, reserve_reply, reserve_tools, clipped_blocks, freed_chars}
    #   ——裁了什么不再静默。下限保护：预留吃掉全部额度时取保底预算，绝不出负数。
    # 关=**逐字旧行为**（有效预算仍是 9000，埋点字段与旧版一致；没裁剪就一条都不写）。
    # 回退：置回 False（runtime_flags 热切，无需重启）。默认关=零行为变化；
    #   开前沿用既有安全阀口径：确认 quota_clipped_sections 仍为 0，否则=预留挤掉了真实内容，回滚。
    "context_budget_reserve": False,
    # ── 控制台删号·回收站到期自动清除（第二期第二批，2026-09-24；高危默认关）──
    # 开=后台调度器在低峰窗口（默认北京时间 02:00–06:00）扫「宽限期已到」的回收站账号，
    #   逐个交给 account_purge.purge_account 物理清除（进程内串行、每轮限量、最小间隔节流）。
    #   关=**逐字零行为**：调度器一次都不查库、不产生任何差异（默认）。开前请确认备份策略到位
    #   （清除器已内置 fail-closed 前置备份）；「不能删最后一个 server_admin」等护栏在清除器内
    #   始终保留，命中只留 WARNING 不清。开法同其它灰度键：本键已登记进 AGENT_FLAGS，重启后可经
    #   flag_service 热改。
    "account_purge_scheduler": False,
    # ── X7 行动通道三条闸（C1a，2026-09-25：从「直接读 runtime_flags」改登记进常规开关体系）──
    # 缺省方向与原先各自更严的一侧逐字一致：两条总闸缺行＝关，强制干跑缺行＝开（裁决通过也不
    #   下发可执行凭据）。三条键默认由服务器锁定（flag_service.SERVER_LOCKED_DEFAULT_KEYS）：
    #   App 开关页可见但不可自助改，改写入口仍是服务器控制台（PUT /admin/server/device-actions/switches）。
    "device_actions_enabled": False,
    "device_actions_plugin_enabled": False,
    "device_actions_force_dry_run": True,
}

# 搜索结果注入模板（与旧文案唯一差异：第 3 点允许结果不足时补查 1 次）
_SEARCH_RESULT_TEMPLATE = (
    "【搜索结果】（你已经搜索完成，现在直接基于这些真实信息回复；不要说自己去'搜索了'）。\n"
    "{result}\n\n"
    "注意：1. 如果结果有用，自然引用回答用户；2. 如果结果与问题无关或质量差，说明没查到靠谱的并给出你自己的看法（例如'网上说法不太靠谱，我估计…'）；"
    "3. 你已经搜索完成，绝不要说'我去搜一下/等着我去查'这类话；如果这次结果仍不够或与问题无关，可以再输出一次 [SEARCH] 补充查询（最多再查 1 次），否则不要再输出 [SEARCH] 标记。"
    "4. 网络信息属未证实来源（Observation: UNVERIFIED），涉及事实/数字/做法请谨慎转述，不确定就说明是'网上说法'。"
)


async def _execute_search_tool(user_id: int, query: str, run_search: Callable[[str], Awaitable[str]]) -> dict:
    """经统一工具执行入口调用搜索（Phase E：权限三档 + 工具生命周期钩子 + 幂等重试 + 异常隔离）。

    - 单工具超时 30s 由本层 wait_for 保证（超时 → execute_tool 捕获为 error）；
    - forbid → blocked（搜索被权限拦截）；ask → search 为只读低风险自动放行（不挂起询问）；
    - run_search 返回空串不算异常，空结果重试由 run_search_loop 外层控制（SEARCH_RETRY）。
    """
    from app.agent import tools as _tools
    from app.agent.tool_runner import execute_tool
    _spec = _tools.get_tool("search")
    if _spec is None:
        return {"status": "error", "error": "search tool not registered"}
    _exec_spec = _tools.ToolSpec(
        name=_spec.name,
        description=_spec.description,
        action_type=_spec.action_type,
        risk_level=_spec.risk_level,
        rate_limit=_spec.rate_limit,
        idempotent=_spec.idempotent,
        scope=_spec.scope,
        ask_auto_allow=_spec.ask_auto_allow,
        epistemic_status=_spec.epistemic_status,
        provenance=_spec.provenance,
        execute=lambda payload: asyncio.wait_for(run_search(payload.get("query") or ""), timeout=TOOL_TIMEOUT_SEC),
    )
    return await execute_tool(_exec_spec, {"query": query}, user_id=user_id, character_id=None, session_id=None)


async def run_recall_loop(
    final_state: dict,
    *,
    user_id: int,
    character_id: int,
    gate: Callable[[], object] | None = None,
    tz_offset_min: int | None = None,
) -> tuple[dict, list[dict]]:
    """记忆二跳受控循环（Ariadne 模块 B，2026-09-04）：decide → [RECALL] → 本地记忆检索 → observe 注入 → 再决策 1 次。

    ``tz_offset_min``（F-3，2026-09-04）：用户本地时区分钟偏移，透传给
    ``parse_time_range``。「时间=YYYY-MM」走绝对自然月不受时区影响（二跳绝对月路径不改），
    透传仅与主检索相对时间口径保持一致；None 时回退 UTC（零行为变化）。

    - 镜像 run_search_loop（零新框架）；非流式专用——流式路径由调用方仅做标记剥离（与 SEARCH 同策略）；
    - flag ``memory_recall_second_hop`` 默认关：不检索、不注入、只剥离标记（零行为变化）；
    - 标记内轻量时间语法「时间=YYYY-MM；查询」→ parse_time_range 解析（失败回退纯语义）；
    - 查询失败/无命中/gate 不通过 → 剥离标记静默降级（不编造「想起来了」）；二跳触发/命中写 trace 步骤；
    - gate 支持 sync/async callable（角色 memory_v2_enabled 关闭时不开放二跳）。
    """
    import re as _re
    steps: list[dict] = []
    try:
        # 二跳最多 MAX_RECALL_ROUNDS(=1) 次：与 run_search_loop 的 rounds 语义一致（无 +1）；
        # 超限时残留的 [RECALL] 由循环后兜底剥离（幂等）
        for _ in range(MAX_RECALL_ROUNDS):
            clean, q = _actions.extract_recall(final_state.get("ai_response") or "")
            if not q:
                final_state["ai_response"] = clean
                break
            if not bool(AGENT_FLAGS.get("memory_recall_second_hop", False)):
                final_state["ai_response"] = clean
                break
            if gate is not None:
                _ok = gate()
                if asyncio.iscoroutine(_ok):
                    _ok = await _ok
                if not _ok:
                    final_state["ai_response"] = clean
                    break
            # 拆「时间=YYYY-MM；查询」轻量语法（解析失败回退纯语义，绝不猜）
            t_range = None
            qq = q
            tm = _re.match(r"\s*时间\s*[=:]\s*([0-9]{4}[-年/.][0-9]{1,2})[；;，,\s]+(.*)", q, _re.S)
            if tm:
                from app.memory.time_query import parse_time_range
                # F-3：透传用户时区偏移（「时间=YYYY-MM」绝对自然月不受影响，与主检索口径一致）
                t_range = parse_time_range(tm.group(1), tz_offset_min=tz_offset_min)
                qq = tm.group(2).strip() or q
            from app.memory import search_memories
            _hop_limit = 6  # 曾为 flag（memory_recall_hop_limit）；因热切通道只支持 bool，热切会静默把 6 变成 1，故固化为常量；要调参改这里
            hits = await search_memories(
                character_id=character_id,
                query=qq,
                limit=_hop_limit,
                time_range=t_range,
                user_id=user_id,  # A2 M0-4：透传调用者（memory_search hook ctx）
                trace_meta={"user_id": user_id, "trigger": "recall_second_hop"},
            )
            steps.append({"action": "RECALL", "query": qq[:80], "n": len(hits)})
            if not hits:
                # 没查到：不再二跳，用首轮正文（不编造「想起来了」）
                final_state["ai_response"] = clean
                break
            # 命中即复习（与第一跳一致，24h 防抖在 reinforce_memories 内）；失败不影响注入
            try:
                from app.memory.service import reinforce_memories
                from app.memory.constants import REINFORCE_FACTOR_RETRIEVE, REINFORCE_DEBOUNCE_HOURS
                await reinforce_memories(
                    [h["id"] for h in hits],
                    factor=REINFORCE_FACTOR_RETRIEVE,
                    debounce_hours=REINFORCE_DEBOUNCE_HOURS,
                )
            except Exception:
                pass
            from app.memory.format import format_memory_line
            block = "\n".join(format_memory_line(h, include_speaker=True) for h in hits)
            final_state["context_messages"] = final_state.get("context_messages") or []
            final_state["context_messages"] = final_state["context_messages"] + [{
                "role": "system",
                "content": _RECALL_RESULT_TEMPLATE.format(result=block),
            }]
            final_state["ai_response"] = ""
            from app.agent.nodes import generate_response as _regen
            final_state = await _regen(final_state)  # 主模型再生成 1 次（与 SEARCH 二跳同成本）
            # 下一轮循环开头 extract_recall 负责识别再次调取标记（超上限时兜底剥离，幂等）
        # 兜底：最后一次剥离（幂等）
        final_state["ai_response"] = _actions.extract_recall(final_state.get("ai_response") or "")[0]
    except Exception as e:
        _logger.warning("Agent recall loop failed char=%s: %s", character_id, e)
        try:
            final_state["ai_response"] = _actions.extract_recall(final_state.get("ai_response") or "")[0]
        except Exception:
            pass
    return final_state, steps


async def run_search_loop(
    final_state: dict,
    *,
    user_id: int,
    character_id: int,
    run_search: Callable[[str], Awaitable[str]],
    throttle: Callable[[int], bool],
    inject_enabled: Callable[[], bool],
    save_history: Callable[[int, str], Awaitable[None]],
    max_steps: int | None = None,
) -> tuple[dict, list[dict]]:
    """受控搜索循环：decide → 执行 SEARCH → observe（注入结果）→ 条件再决策。

    - final_state 已含首轮 LLM 输出（agent.ainvoke 结果）；本函数处理其后所有 [SEARCH] 动作；
    - 返回 (final_state, steps)：steps 为每轮搜索执行摘要（供 Task Trace）；
    - 节流/开关不通过、搜索失败 → 剥离标记静默降级（不编造成功）；
    - 超过搜索轮数上限 LLM 仍输出 [SEARCH] → 剥离标记直接返回。
    """
    steps: list[dict] = []
    rounds = MAX_SEARCH_ROUNDS
    # agent_loop_search：2026-09-17 固化为恒定受控多轮搜索（曾为灰度开关；关=退回旧单次二次生成，现恒定开）
    if max_steps is not None:
        rounds = max(1, min(max_steps - 1, MAX_SEARCH_ROUNDS))
    try:
        round_no = 1
        while round_no <= rounds:
            clean, query = _actions.extract_search(final_state.get("ai_response") or "")
            if not query:
                final_state["ai_response"] = clean
                break
            # 节流 / 搜索注入开关门禁（与旧行为一致）
            if not (throttle(user_id) and inject_enabled()):
                final_state["ai_response"] = clean
                break
            _logger.info("AI web search char=%d round=%d query=%s", character_id, round_no, query[:60])
            # 执行搜索（Phase E：统一工具执行入口 execute_tool——权限三档 + 生命周期钩子 + 异常隔离；
            # 空结果重试 1 次由本层控制，单工具超时 30s）
            result = ""
            blocked = False
            for attempt in range(SEARCH_RETRY + 1):
                _res = await _execute_search_tool(user_id, query, run_search)
                if _res.get("status") == "blocked":
                    blocked = True
                    _logger.info("AI web search blocked round=%d query=%s: %s", round_no, query[:60], _res.get("error"))
                    break
                result = (_res.get("result") or "") if _res.get("status") == "ok" else ""
                if result:
                    break
            steps.append({"action": "SEARCH", "query": query[:80], "ok": bool(result), "round": round_no})
            if blocked or not result:
                _logger.warning("AI web search %s round=%d query=%s: 降级为剥离标记", "blocked" if blocked else "failed", round_no, query[:60])
                final_state["ai_response"] = clean
                break
            # observe：落浏览记录 + 注入结果 → 再决策（允许补查）
            try:
                await save_history(character_id, query)
            except Exception as e:
                _logger.warning("AI search history save failed: %s", e)
            final_state["context_messages"] = final_state.get("context_messages") or []
            final_state["context_messages"] = final_state["context_messages"] + [{
                "role": "system",
                "content": _SEARCH_RESULT_TEMPLATE.format(result=result),
            }]
            final_state["ai_response"] = ""
            from app.agent.nodes import generate_response as _regen
            final_state = await _regen(final_state)
            # 不再立即剥离：下一轮循环开头的 extract_search 负责识别补查标记；
            # 无补查时下一轮以 clean 退出；超限时由循环后的兜底剥离（幂等）。
            round_no += 1
        # 超限/退出兜底：最后一次剥离（幂等）
        final_state["ai_response"] = _actions.extract_search(final_state.get("ai_response") or "")[0]
    except Exception as e:
        _logger.warning("Agent search loop failed: %s", e)
        try:
            final_state["ai_response"] = _actions.extract_search(final_state.get("ai_response") or "")[0]
        except Exception:
            pass
    return final_state, steps
