"""AGENT_FLAGS 单一事实源（断点 #8 重构：从 app/agent/loop.py 下沉到中立模块）

本模块不得 import 任何业务模块（agent/memory/application/...），否则 memory→agent 的循环依赖会重新长回来。
值由 app.application.flag_service 在启动时按 runtime_flags 表就地覆盖（同一个 dict 对象）。
"""

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
    "memory_temporal_recall": True,  # 已转正（2026-10-02）：生产自 2026-09-03 起已开、非灰度；默认改为开（行为与现网一致），回退＝置 False
    "memory_recall_second_hop": True,  # 已转正（2026-10-02）：生产自 2026-09-05 起已开、非灰度；默认改为开（行为与现网一致），回退＝置 False
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
    # ── 批 0-7（2026-09-28，雷达 08）召回排序显式 recency ＋ 命中记忆的相邻块 ──
    # 两个独立开关（可只开其一：①纯打分、②多一条只读查询），**均默认 False＝逐字节旧行为**。
    # 为什么不用既有 flag 承载：memory_temporal_recall / memory_recall_second_hop / memory_peak_cutoff /
    #   memory_chain_expand 生产 runtime_flags **已拨开**，复用任一把闸＝上线即改行为，违背「默认关」硬要求；
    #   且四条语义各不相干（用户点时间才补路 / 模型主动补查 / 弱相关弃权 / 沿链而非时间邻域）。
    "recall_recency_bonus": False,  # ①rerank 加显式时效档位（≤24h +20 / ≤7d +15 / ≤30d +10，分档不叠加，与既有 +20/+15/+10 同量级）；开=新条更易靠前（截断场景可挤掉低分旧条），条数/预算不变
    "recall_neighbor_block": False,  # ②命中条带出同角色（群记忆同群）±30 分钟邻居——**只补本轮不足 limit 的空缺槽位**，不挤占已有结果、不新增条数；memory_peak_cutoff 开时不生效（不破坏弃权语义）
    # ── 批 0-11（2026-09-28，雷达 44）专名匹配「第三路」──
    # 默认 False＝那条 LIKE 粗筛查询一次都不发、RRF 仍是两路、排序与 trace 逐字节旧行为。
    # 开＝在向量（bge-m3）+ 关键词（BM25）之外补一路**确定性**专名匹配（人名/昵称/关系称谓，
    #   纯字符串+正则+字典，零模型零外网零新依赖，见 memory/entity_match.py），命中的 id 并入
    #   既有 RRF 与 _rerank：只多一路证据（重叠即 +5），不插队、不剔除、条数与预算不变。
    "recall_entity_match": False,
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
    "memory_peak_cutoff": False,  # 默认关（用例钉死：test_memory_lookup_endpoint::test_条数硬顶_limit超20夹到20）；生产已由 runtime_flags 拨开（2026-09-05）
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
    # A1（2026-09-19）向量账号归属：开=读取按 metadata.user_id/角色 owner 过滤（写入始终带）；关=逐字节旧行为。本键必须登记，否则 DB 里开了也不生效
    "vector_user_scope": True,  # 已转正（2026-10-02）：生产自 2026-09-19 起已开、非灰度；默认改为开（行为与现网一致），回退＝置 False
    # ── #70 附录 C 可选 M3：记忆写入回执（memory_write_receipt，2026-09-15 落地；默认关=零写入、零行为变化）──
    # memory_write_receipt 开=save_memory 写分支 / supersede_memory 异步写 memory_write_receipts
    #   （终态追踪「这条记忆为什么在/不在」）；关=完全跳过（不写不读，逐字节旧链路）。
    "memory_write_receipt": False,
    # Ariadne 模块F（2026-09-04）：Curated Knowledge 编纂知识层（world_facts 加 kind 分治 + 确定性注入）
    "curated_knowledge": True,  # 已转正（2026-10-02）：生产自 2026-09-05 起已开、非灰度；默认改为开（行为与现网一致），回退＝置 False
    # Ariadne 模块G（2026-09-04）：前瞻意图。enabled=写入（extractor 便车落表）；
    # trigger=触发（时间型 Scheduler 提起 + 线索型 context 注入）。两段灰度：先开 enabled 攒数据，再开 trigger。
    "prospective_intent_enabled": False,
    "prospective_intent_trigger": False,
    # ── §20 跨角色用户事实（2026-09-04，默认关=零行为变化；bool 可 runtime 热更）──
    # global_user_facts：用户级可变事实层总开关——开=GPS/跨角色事实写入 + [USER NOW] 注入分区；
    #   关=不写/不读 user_facts（抽取出原路径、注入空）。
    "global_user_facts": True,  # 已转正（2026-10-02）：生产自 2026-09-03 起已开、非灰度；默认改为开（行为与现网一致），回退＝置 False
    # ── 细粒度槽开关（2026-09-10，用户拍板；先只搭框架，【全部默认关，含 location】；
    #    真机观察 C2 新鲜窗 / 回家识别 / C3 锚点稳定后，再经 runtime flag 手动只开 location）──
    # 语义：总闸开=全槽启用；总闸关时按各槽 flag 独立决定（user_fact_slot_enabled）。
    "user_fact_location": False,      # 位置：最不敏感、最易过时；GPS/城市/聊天归槽写 location
    "user_fact_job": False,           # 工作/学业
    "user_fact_relationship": False,  # 感情状态（隐私，默认关）
    "user_fact_living": False,        # 居住状况（独居/和谁住）
    "user_fact_goal_state": False,    # 近期目标/状态
    "user_fact_health": False,        # 健康（隐私，默认关）—— 2026-10-02 复核：属隐私槽，且块注释记「09-10 用户拍板：细粒度槽全部默认关」，故不转正（生产用 runtime_flags 单独开）
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
    "domain_event_log_enabled": False,  # 默认关（设计硬要求 + 用例钉死：新装不得默认写放大）；生产已由 runtime_flags 拨开（2026-09-08）
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
    "mcp_stream_declarations": False,  # 默认关（用例钉死：test_mcp_phase2::test_mcp_declarations_stream_empty）；生产已由 runtime_flags 拨开（2026-09-08）
    # ── 工具轨迹治理 R5（2026-09-09，方案 §4.5）──
    # agent_tool_exec_trace 开=插件/内置工具每次执行（tool.executed 事件）落一条 agent_task_logs
    # （trigger=tool），让「工具轨迹」能看到真实工具成败；关=不写（默认，零写放大）。
    # MCP 工具不落（已有 mcp_call_logs，前端 MCP 分区读取，避免双记）。
    "agent_tool_exec_trace": True,  # 已转正（2026-10-02）：生产自 2026-09-08 起已开、非灰度；默认改为开（行为与现网一致），回退＝置 False
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
    # ── A4 批 1 / T3：事实生命周期策略表（2026-09-26 落 P0–P2；2026-09-29 B9 判读后进 P4）──
    # 本键是**档位**（归一口径唯一真源＝app/memory/lifecycle_policy.py::gear_of），三档：
    #   False（默认）＝ off      ：连扫描都不跑，逐字节旧行为；
    #   True          ＝ dry_run ：随 6 小时维护拍子抽样统计每类事实分布与「按 TTL / valid_to 判失效」条数，
    #                              打一条 INFO「Lifecycle policy dry-run: …」；**只读、不改状态、不写库**
    #                              （＝本批之前的全部行为，线上 DB 行 enabled=1 就落在这一档）；
    #   "apply_plan"  ＝ 生效档  ：观测照打，并**只让 plan 一类的 TTL 落地**——把已过期计划交给
    #                              memory/maintain_plan_expiry.expire_stale_plans 置 stale（现状面退出、
    #                              检索/复习面保留、不物理删除）。其它 fact_kind 与用户属性槽层**继续干跑**。
    # 为什么用字符串档而不是再登记一把 bool（原占位名 fact_lifecycle_policy_apply 已作废）：
    #   复用既有开关于单一真源；新增第二把闸会让「观测/作用」两处判断漂移。
    # 拨档方式（重要）：runtime_flags 只覆盖 bool 键（flag_service 有类型防护），所以
    #   ① 保持本行默认 False 时，DB 行 enabled=1 会把键合并成 True＝干跑档（现状，不变）；
    #   ② 要进生效档：把本行默认值改成 "apply_plan" 后重启（此时该键非 bool ⇒ DB 行不再覆盖），
    #      或进程内热改 AGENT_FLAGS["fact_lifecycle_policy"] = "apply_plan"（重启即失效）。
    # 回退：把本键置回 True（干跑档）或 False（关）⇒ 从下一拍起不再动作，逐字节恢复本批之前行为；
    #   失效动作的另一条授权通道（L4 的 review_plan_expire_stale，日终维护）不受本键影响、语义不变。
    # 2026-09-29 深夜：用户拍板「B9 进 P4」⇒ 默认值由 False 改为 "apply_plan"（生效档，只让 plan 的 TTL 落地）。
    #   注意：本键因此从 bool 变成 str，启动加载器对非 bool 键**跳过 DB 覆盖**（旧行 fact_lifecycle_policy=1 不再生效），
    #   所以这一行就是「开/关/档位」的唯一开关；回退＝把本行改回 True（干跑）或 False（关）后重启。
    "fact_lifecycle_policy": "apply_plan",
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
    "promise_self_side_split": True,
    "timer_render_subject_fix": True,
    "proactive_topic_guard": False,
    "life_event_no_replay": False,
    "life_memory_write_retry": True,
    # ── #72 PR-C 群聊认知升级（group_cognition_v2，2026-09-15）──
    # group_cognition_v2 开=群聊认知能力（P1 纯数据层 + P2 共享记忆接线已合；P3 逐角色认知
    #   生成+私有注入+预算+仲裁、P4 观测+群级灰度列生效 待后续拆包）。总开关：本项默认关，
    #   app/memory/group_memory.group_cognition_on() 已读该键、异常即 False——关=零行为变化
    #   （PR-B 双轨均未接线状态原样保留）；开 + 群级 chat_groups.cognition_enabled 才走双轨。
    "group_cognition_v2": False,
    # ── #72 PR-C P5 群记忆日终合并收敛（2026-09-16 落地）──
    # group_memory_compact 开=每日 23:00 后把 >7 天的群记忆按群合并成 1 条 system 摘要、
    #   旧行软删（is_archived=1，留痕不物理删）；关=完全跳过、逐字节现状。
    "group_memory_compact": True,  # 已转正（2026-10-02）：生产自 2026-09-16 起已开、非灰度；默认改为开（行为与现网一致），回退＝置 False
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
    # ── X6 主动内容策略包外放（2026-09-16 落地）──
    # proactive_strategy_plugins 开＝「内容策略」交给策略包：内核向 proactive_candidate hook
    #   下发 roster（选人结果），被接管的策略源（本轮仅 special）整体让位；关=内核各策略源
    #   照旧产出、hook ctx 不带 roster（策略包返回空），与现状逐字节一致。
    #   防双发两道闸：①让位（同一类别只留一个生产者）②内核按 (角色, message_type, 北京日界)
    #   去重（策略候选落库口径由内核校验，见 scheduling/sources/strategy.py）。
    #   回退：置回 False 即可（runtime_flags 热切，无需重启）。
    "proactive_strategy_plugins": True,  # 已转正（2026-10-02）：生产自 2026-09-16 起已开、非灰度；默认改为开（行为与现网一致），回退＝置 False
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
    # ── C12b（2026-09-26）：two-pass 白名单放开总开关（默认关）──
    # 开=两遍重读不再看灰度白名单 TWO_PASS_TRACE_GRAY_CHARS（对所有角色生效）；
    #   关=**逐字旧行为**（仍须角色命中白名单，当前仅 char13）。默认关=零行为变化。
    # 前置：主开关 two_pass_trace 必须同时为开，本键单独开不产生任何行为；
    #   判定口径见 scheduling/message_generator.py: two_pass_trace_allowed()。
    # 回退：置回 False（runtime_flags 热切，无需重启）。
    "two_pass_trace_all_chars": False,
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
    # ── 决策层阶段 0：影子留痕（A3，2026-09-25；默认关）──
    # 开=在已接线的决策点（当前＝记忆评星、记忆时态）把「输入/输出/耗时/来源=legacy」记进
    #   agent_task_logs（route=decision_layer_shadow），**判定结果仍由原算法给出、逐字不变**；
    #   攒够这些留痕才谈阶段 1 的候选后端与校准（docs/decision-layer-research.md §8.6、§10）。
    #   关=零行为：三原语只做一次透传调用，不建记录、不起计时器、不碰 IO，与没接这层完全一致。
    "decision_layer_shadow": False,
    # ── A4 批 2 / T4 P1：召回门影子留痕（2026-09-27；默认关）──
    # 开=每轮检索后多算一次「这轮该不该检索」的纯规则判定，并把判定与实际命中数一起记进
    #   agent_task_logs（route=recall_gate_shadow），**是否检索仍完全按原逻辑执行、逐字不变**；
    #   攒够留痕才谈 P2 生效（新开关 recall_gate）与判效（漏召回率 / 无效检索率）。
    #   关=零行为零开销：调用点首行即返回，不算判定、不建记录、不碰 IO。
    "recall_gate_shadow": False,
    # ── A4 批 2 / T4 P2：召回门**生效**（2026-10-01；默认关；用户 10-01 拍板后拨开）──
    # 开=在检索前先做一次纯规则判定（memory/recall_gate.decide_retrieval）；判为「纯寒暄 / 纯符号」
    #   的轮次**跳过检索与记忆注入**（当作检索为空、不写记忆），其余一律照旧检索；
    #   关=**逐字节旧行为**（调用点不 import、不计算，连一次判定都不做）。
    # 判错默认放行：判定/导入异常一律按旧行为继续检索（宁多不漏）。回退＝置回 False（热切）。
    "recall_gate": False,
    # ── A4 批 3 / T1「M2a 两档释放」：**拆两把键**（2026-10-01；默认关；用户当日拍板 P5）──
    # 开口档：主动消息**发送确认**（seq==0 且有 intent）后按比例做部分释放（只写水位，不参与投放决策）；
    # 全额档：用户发言后按「最近一条未接住的主动消息」把该驱力清零。
    # 两把键都要叠加 relational_drive_shadow（release_* 在影子关时会静默早退；只开本键会打 WARNING）。
    # 关＝**逐字节旧行为**（不 settle、不释放、一次 SELECT 都不发）。回退＝置回 False（热切）。
    "relational_drive_open_v1": False,
    "relational_drive_full_v1": False,
    # ── A14（2026-10-01）：AI 评星「多轮共识写回」· **M0 干跑**（默认关）──
    # 开＝只对灰度角色（char13）**多跑 N-1 轮评星并把各轮星分 + 共识写进留痕**，
    #   **写回仍用第 1 轮值** ⇒ 零行为、可与现行口径直接对照（判效见小方案 §5）。
    # 关＝**逐字节旧行为**（一次都不多调）。回退＝置回 False（热切）。
    "ai_rating_vote3": False,
    # ── A4 批 6 / T5 M0 项1：注入视图分离·现状面子句（2026-09-27；默认关）──
    # 开=注册表版「AI 生活」注入（agent/context/section_overlay.py life_share）补上现状面状态子句
    #   current_facts_status_clause()，与 legacy 版（context/legacy.py:871）口径对齐；
    #   关=**逐字节旧行为**（该 select 的 where 不附加任何子句，SQL 与改动前一致）。默认关=零行为变化。
    # 注意：现状面开关 current_facts_active_only（线上默认 True）开时子句才是「恒 active」，
    #   本键单独开、current_facts_active_only 关时子句退化为旧口径（memory_supersede 门控）。
    # 回退：置回 False（runtime_flags 热切，无需重启）。
    "current_view_filter": True,
    # ── S1 第二步（2026-09-27）：主动消息链「角色自主搜索」（默认关=逐字节旧行为）──
    # 开=主动消息生成首轮若输出 [SEARCH]，走一次受控自主搜索（复用 run_search_loop 的 self 分支语义：
    #   结果只作参考、模型可「什么都不说」→ 本轮不产出消息；搜索失败/被节流 → 仍发原候选），
    #   regen 走 scheduling/message_generator.py 的 _gen_with_reasoning（不走 nodes.generate_response，
    #   保持主动链 task="message"/按角色思考挡位）；本批不落小手机浏览记录。
    # 关=不解析 [SEARCH]、不搜索、不加时延，与现状逐字节一致。
    # 本键必须登记，否则 runtime_flags 里开了也不生效（flag_service 只合并已登记键）。默认关=零行为变化。
    "proactive_self_search": False,
    # ── A4 批 3 / M1b1：关系驱力影子态（2026-09-27；默认关=零行为变化）──
    # 开＝(角色 × 用户) 的六驱力水位进入**影子态**：只 settle 落库（relational_drives 表）
    #   + 写留痕 + 与现行意图做「影子改判」对比；**不改实际意图、不注入任何 section、
    #   不改发送**——定调仍由现有加权随机给出，逐字节不变（钩子与改判留痕属下一单 M1b2）。
    # 关＝**逐字节旧行为**：水位读写口（app/application/relational_drive_service.py）每个入口
    #   首行即返回，连一次 SELECT 都不发。默认关＝零行为、零开销，一键回退。
    # 本键必须登记，否则 runtime_flags 里开了也不生效（flag_service 只合并已登记键）。
    "relational_drive_shadow": False,
    # ── A4 批 8 块 B（2026-10-01 M0 → M1）：插件能力自描述（capability_notes）校验总闸 ──
    # 关 ⇒ 整段不解析、不报错（旧 manifest 判定逐字节等价）；开 ⇒ 校验可选字段 capability_notes。
    # M1（默认开）：本闸同时是 fail-open 分级第 ④ 条的闸（tool_runner：插件自述 risk=high ⇒ 权限
    #   校验异常时 fail-closed，只收紧）。关 ⇒ 校验与收紧一并不生效＝回 M0 行为。
    "plugin_capability_notes": True,
    # ── A4 批 7 M1（2026-09-30）：情绪→驱力单向调制的**三态**档位 ──
    # off=不取快照（逐字节旧行为）/ shadow=算乘子只留痕、落库与 off 相同 / on=真生效（白名单∧稳定桶）。
    # ⚠️ 非 bool 键 ⇒ 启动加载器**跳过 DB 覆盖**（先例 fact_lifecycle_policy），档位＝代码默认值，改档后重启生效。
    # 2026-09-30 凌晨用户拍板：拨到 shadow 档开始攒影子数据（照算乘子与偏置、只写 trace；
    #   settle_level 仍不传快照 ⇒ 落库与 off 逐字节相同）。判效窗 7 天（约 10-07）：看乘子分布、
    #   性格偏置是否触顶、异常次数；再决定是否升 on（白名单 ∧ 稳定比例桶才真生效）。
    "emotion_drive_modulation": "shadow",
    # P1-2（2026-09-28）：写路径查重只认现行（active）——关＝逐字节旧行为（默认关）。
    "write_dedup_active_only": False,
    # 批 0-2 M1a（2026-09-28）：感知来源打标 —— 关＝逐字节旧行为（默认关）。
    # 开＝①新写入记忆若正文与「本轮感知语料」（最近 30 分钟 / 8 条手机快照）重合，
    #   落库前把来源记为 perception、认知状态降为 INFERRED（只标注、不拒收，异常 fail-open）；
    #   ②跨来源禁合并：感知条与非感知条不得互相并条（写路径三处查重 + curated 近似合并）；
    #   ③召回输出补 source/sub_type 字段（只加字段，不改排序/条数/阈值）。
    # 隔离四禁令（晋升/摘要/剔除）属 M2，另键另批，不在本 flag 范围内。
    "perception_source_tag": False,
    # 批 0-2 M2（2026-09-28）：感知隔离生效 —— 关＝逐字节旧行为（默认关）。前置＝perception_source_tag 已开
    # （没有打标就没有 perception 条，本键开了也无料可隔离）。
    # 开＝对「被隔离条」（perception_tier.is_quarantined：来源 perception 且未被用户认可为 FACT）执行隔离禁令：
    #   ①晋升闸：不参与 is_core 晋升（其它晋升条件不变）；
    #   ②摘要原料：置顶摘要 / 身份画像的取料查询排除被隔离条（专治「污染记忆再凝成画像注回 prompt」的二阶放大）；
    #   ③召回降权：被隔离感知条仍召回、不剔除，仅在既有 rerank 加分体系里吃一个负向偏置（见 retrieve.py）。
    # 用户点「这是真的」认可后（epistemic_status=FACT）自动脱隔，三条禁令一并解除，不引入第二套晋升机制。
    "perception_isolate": False,
    # ── 模型自写记忆「依据校验」影子档（2026-09-29，方案《小方案_模型自写记忆FACT口径_v1》方案 B）──
    # 背景：标记路径（模型自写【记忆：…】）的归属由 memory/speaker.py 按措辞推断，「无主语 + 本轮有用户
    #   消息」判 user/FACT ⇒ 模型自己的推断、复述感知甚至编造都会以「用户说过的事实」进长期记忆（可竞争
    #   核心晋升、进置顶摘要与身份画像、参与跨角色同步）。这不是 memory_admission_gate 没开：闸门按
    #   sender_type 裁决，误判在它之前完成，拨开它也堵不住（本键不动它、也不动 speaker 的 5 条规则）。
    # ON ＝ **影子**：agent/nodes.py 标记写入循环里对每条 mem 跑一次纯字面判据
    #   （memory/marker_evidence.py，零 LLM 零 IO）；「本轮用户消息里找不到依据」时只写一条回执
    #   （reason 带 marker_evidence=absent）+ 一条 INFO 日志——**不改 epistemic_status、不改 speaker、
    #   不拒收、不删条**；回执另受 memory_write_receipt 闸控，那闸关时以 INFO 日志为准。判据异常 fail-open。
    # OFF（默认）＝逐字节旧行为：不 import 判据、不做一次字符串比对、不留痕。
    # 强约束档（无依据降级为 character/INFERRED）是**下一批**——届时另键或同键升级并另行公告；
    #   影子期先取「标记路径总量 / absent 占比 / 人工抽查误降率」三个指标再定。
    "marker_requires_user_evidence": False,
    # ── 断点 #8′E12（2026-09-29）：手机通知提及通道可关（默认开＝保持现状）──
    # 背景：application/phone_auto_notify_service.py 的 notification_mention 是 HTTP 驱动的
    #   直发点（手机后台上报 → 服务器判定 → 直接 send_to_session 并弹推送），此前无任何总开关
    #   可停（runtime_flags 表里没有该通道相关键）；本批补闸的同时把通道登记进开关体系。
    # ON（默认）＝逐字节保持现状：合格角色 + 过完内核闸才发。
    # OFF＝整条通道不发（基线维护与快照采集照常，零 LLM 调用）；提供的是「能关掉」的能力，
    #   不是行为变更 ⇒ 缺键也按开处理（默认关会静默停掉一个已上线功能，属行为回归）。
    "phone_auto_notify_mention": True,
    # ── P0 语义统一 · 第 1 步：actor 归属影子留痕（2026-09-29；默认关＝逐字节旧行为）──
    # 背景（S2 架构数据流地图 §4.3）：「这条内容是谁说的」在多处各写各的字面量，且有三个已知
    #   丢失点——① 调用方没给 speaker 时缺省记成 user（连 diary/bio 这类模型自述也算用户说的）；
    #   ② 感知派生条在打标闸关时一路掉进兜底 user；③ 并条（merge）时进来那句话的归属整体消失。
    # 本批只做**地基 + 观测**，不改任何写入语义：新增常量与归一化的单一来源 app/actors.py、
    #   新增纯判定 app/memory/actor_shadow.py，并在 memory/write.py 的两个现场（新行落库前 / 并入时）
    #   各挂一处埋点——开＝多算一次纯判定 + 多打一条 INFO（统一归属 vs 实际归属、是否不一致），
    #   关＝不 import 判定模块、不打日志、不查库、不写库（逐字节旧行为）。
    # 刻意不调 obs_event / 不写回执：那会新增 DB 调用到主链路，违背第 1 步「零写入」硬约束。
    # 第 2 步（把归一化接进 write/speaker/events 各落法）需另批拍板；本键留作观测期开关，
    #   攒够「unified vs actual 不一致占比」三个指标后再决定是否升级成生效语义。
    "actor_semantics_shadow": False,
    # ── 断点 #9 收口（2026-09-29 用户拍板：两处「状态更新」过期口径统一到 12 小时）──
    # 背景：同一条现状更新双写两个面，改动前数值不一致 —— WorldFact 侧 = events/facts.py
    #   STATUS_FRESH_HOURS（12 小时，写入 TTL 与读取新鲜窗同源）；Memory 侧 = 通用艾宾浩斯衰减
    #   （constants.S_BY_TYPE["insight"]=7 天 × DECAY_THRESHOLD_PCT=20 ⇒ ≈2.6 天才落下去），
    #   于是「记忆还在说这个状态、世界事实早已过期」。
    # ON（默认）＝memory/retrieve.py::_rerank 召回出口剔除超窗的「状态派生记忆条」
    #   （判据与数值单一来源 events/facts.status_memory_expired，12h 只在 facts.py 定义一处）；
    #   行上已有同源 valid_to 用它（与 WorldFact.expires_at 同一瞬间），存量行按 created_at 补算。
    # OFF＝逐字节旧行为（过期状态条照旧可被召回注入）。
    # 严格限定身份：sub_type='status' **且** source='status'（写侧唯一来源 chat_service
    #   ::_save_status_update）；生产库实测 sub_type='status' 共 873 行，其中 778 行来自抽取
    #   （source='chat'）——只看 sub_type 会误伤抽取条，故必须两条同时成立。
    # 不动通用衰减档 S_BY_TYPE["insight"]（改它牵连全部 insight），也不动 top-k/阈值/排序。
    # 回退：置回 False（runtime_flags 热切，无需重启）。
    "status_memory_ttl": True,
    # ── P0 语义统一 · 第 3 步：工具结果注入行补认知标注（2026-09-29；默认关＝逐字节旧文本）──
    # 背景（S2 地图 §1.2 丢失点 5 / 差距表 G6）：tool_runner 早已产出 {epistemic_status, provenance,
    #   summary} 三元组，但注入上下文那一跳只取 summary——「这条观察是什么身份、来自哪里」在进
    #   上下文的瞬间蒸发，模型只看到「工具 X 已执行完成：<摘要>」。
    # ON ＝ 注入行前缀补上认知标签：【工具结果·FACT·web_search】工具 search 已执行完成：…
    #   （三处注入点：agent/runtime.py 的工具分支与小手机分支、agent/mcp_tools.py 的 MCP 分支；
    #    MCP 的来源形如 mcp:{服务器名}；标注值缺失时兜底 UNVERIFIED / tool）。
    # OFF（默认）＝**逐字节旧文本**：拼接片段为空串，注入内容与改前完全一致（仅多一次进程内计数）。
    # 同批附带（与本键无关、不受本键门控）：provenance 词表收口进 app/actors.py OBS_PROVENANCE_*
    #   （纯机械替换、值逐字不变）；events/store.py 的 actor/origin 只判定+只计数（不改落库值）。
    # 回退：置回 False（runtime_flags 热切，无需重启）；风险面＝注入文本变长，牵动前端块切分展示。
    "observation_label_v1": False,
    # ── A4 批 4 / T2 M1（2026-09-30）：念头池影子供给总闸（默认关＝逐字节旧行为）──
    # 开＝抽取 → 源侧配额（按面每日硬闸 + 入池准入门槛，app/domain/thought/quota.py）→ 幂等
    #   去重 → 写 thought_pool 表 + 一条 trace（route=thought_pool_shadow），只攒料不使用；
    #   判效窗内看「日均可落池条数、各闸丢弃数」，验证配额把入流压到 ≤420 条/30 天的目标。
    # 关＝**逐字节旧行为**：读写口 app/application/thought_pool_service.py 每个入口首行即返回，
    #   连一次 SELECT 都不发。默认关＝零行为、零开销，一键回退。
    # 本批**没有发送权**：不开 prompt 注入、不碰 arbiter / message_generator / 任何发送链路，
    #   念头进上下文属 M2-a（前置＝本表已攒到料 + thought_id 归因位）。
    # 本键必须登记，否则 runtime_flags 里开了也不生效（flag_service 只合并已登记键）。
    "thought_pool_shadow": False,
    # ── A4 批 4 / T2 M2-b1（2026-10-01）：念头池素材真进 prompt 总闸（默认关＝逐字节旧行为）──
    # 开＝①主动侧：arbiter._annotate_outreach_plan 内按 outreach 类型从池取一条（白名单 2 角色），
    #   经 generate_proactive_event 的新入参 thought 作为**素材**拼进 prompt（与 outreach_intent/plan
    #   同性质，不是规则）；②聊天侧：新增 append 分区 thought_pool 注入一条；③三档释放结算
    #   （spent/told_flat/never_told，用 M2-a 的 domain/thought/settle.apply_release 写回）。
    # 关＝**逐字节旧行为**：取一条/释放/注入每个入口首行即返回，连一次 SELECT 都不发
    #   （照 arbiter._pacing_gate 与 thought_pool_service.shadow_enabled 的早退写法）。
    # 开它时**隐含 thought_pool_shadow 语义在跑**（不抽池就没有念头可取，设计 §4 两键关系）；
    #   本键只控制「取用 + 释放 + 注入」，抽池落库仍由 thought_pool_shadow 控制，别自造第三态。
    # 灰度＝白名单 2 角色（THOUGHT_POOL_GRAY_CHARS，thought_pool_service.py）∧ 比例桶。
    # 本键必须登记，否则 runtime_flags 里开了也不生效（flag_service 只合并已登记键）。
    "thought_pool_v1": False,
    # ── A4 批 8 块 C / M1（2026-09-30）：短句形态约束（通知载体）总闸（默认关＝逐字节旧行为）──
    # 开＝①通知正文按「notify 载体」上限下发（domain/message_shape.py::fit_to_form：先取首句，
    #   首句仍超限才硬截加省略号）；②本轮判定将以通知面送达（沿用 send_to_session 已有的
    #   pushed 判定，离线即通知面）时，往 LLM 上下文拼一句「这条会以通知形式提醒对方，
    #   请压缩成一句（≤N 字）」——照 WECHAT_CHANNEL_HINT 三条纪律：只进上下文、不落库不进记忆、
    #   常量集中一处（agent/nodes.py::NOTIFY_SHAPE_HINT）。
    # 关＝**逐字节旧行为**：通知正文仍是原 [:50]+"…" 预览（收口前后等价），prompt 一字不多。
    # 私聊气泡正文与 WS 原文一律不裁（点开永远能看到完整消息），落库/记忆不受本键影响。
    # 上限数值须由 GET /api/v1/scheduler/stats/notify-shape 的读数＋真机复核后再调，不照抄设计稿。
    # 本键必须登记，否则 runtime_flags 里开了也不生效（flag_service 只合并已登记键）。
    "message_shape_notify_limit": False,
    # ── A4 批 8 块 A / M1（2026-10-01）：角色级 OpenAI 兼容端点总闸（默认关＝两个路由 404）──
    # 开＝注册并放行 POST /v1/chat/completions + GET /v1/models（标准 OpenAI 形状出入参，
    #   内核复用 application/character_chat_api.chat_with_character 旁路：不落库/不建会话/
    #   不写记忆/不触发 hook）；归属口径＝A 严格 owner（与 /api/v1/ai/* 同：角色不存在 404、
    #   非本人 403），models 与 completions 必须同口径。凭据本阶段仍用 JWT（API key 属 M2）。
    # 关＝**两个路由一律 404**（端点入口首行判 flag，未开即 raise 404，不查库、不进内核）。
    # 显式拒绝（400，复用 M0 domain/compat_shape.validate_compat_request 文案）：stream=true /
    #   tools / response_format / n>1 / messages[role=system]——静默忽略会让接错的人以为成功。
    # 渠道归因：入口 set_channel(openai_compat)（utils/llm_channel.py 词表），task 沿用 plugin_ai。
    # 本键必须登记，否则 runtime_flags 里开了也不生效（flag_service 只合并已登记键）。
    "openai_compat_endpoint": False,
}
