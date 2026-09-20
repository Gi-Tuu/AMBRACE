# -*- coding: utf-8 -*-
"""开关目录元数据（A4，2026-09-20）：把 App 侧硬编码的「高级开关目录」改由后端下发。

设计要点：
- 键的唯一真源仍是 ``app/agent/loop.py`` 的 AGENT_FLAGS；本模块**只**提供展示元数据
  （标题/说明/分组/顺序/是否常用），不参与开关的读写与生效判定；
- 文案面向用户（一句话，可读），不出现 flag/DB/prompt 等实现术语；zh / en 成对提供；
- ``visible=True`` = App「常用开关」直显（与 App 侧 _visibleKeys 的 10 键一致），
  其余键收进折叠的高级区；
- ``scope`` 不在这里重复定义，一律由 ``flag_service.USER_SCOPED_FLAG_KEYS`` 推导
  （user=按账号生效 / server=服务器级），避免两套真源漂移；
- 纯内存字典，无 IO：``AGENT_FLAGS`` 有 70+ 键，meta 拼接是 O(n) 纯内存。

新增键时：AGENT_FLAGS 加键后**必须**在本表补一行（缺项由
tests/test_flag_catalog_metadata.py 兜底失败）；未登记的键会落进「其他」组并以键名当标题，
不会丢失，但说明会是通用兜底文案。
"""
# 分组：顺序即界面从上到下顺序（对齐 App 侧 feature_flag_catalog.dart 的 _groupOrder）
CATALOG_GROUPS: list[dict] = [
    {'id': 'agent', 'order': 1, 'label_zh': '智能体运行与认知', 'label_en': 'Agent Runtime & Cognition'},
    {'id': 'proactive', 'order': 2, 'label_zh': '主动消息', 'label_en': 'Proactive Messages'},
    {'id': 'games', 'order': 3, 'label_zh': '群聊小游戏', 'label_en': 'Group Chat Games'},
    {'id': 'life', 'order': 4, 'label_zh': 'AI 自主生活', 'label_en': 'AI Autonomous Life'},
    {'id': 'lifesense', 'order': 5, 'label_zh': '生命感增强', 'label_en': 'Life-like Enhancements'},
    {'id': 'outreach_natural', 'order': 6, 'label_zh': '主动消息自然化（B1）',
     'label_en': 'Natural Proactive Outreach (B1)'},
    {'id': 'memory', 'order': 7, 'label_zh': '记忆检索与注入（实验灰度）',
     'label_en': 'Memory Retrieval & Injection (Beta)'},
    {'id': 'curated', 'order': 8, 'label_zh': '编纂知识与前瞻意图', 'label_en': 'Curated Knowledge & Foresight'},
    {'id': 'cross_char', 'order': 9, 'label_zh': '跨角色用户事实（B1）',
     'label_en': 'Cross-character User Facts (B1)'},
    {'id': 'working', 'order': 10, 'label_zh': '工作记忆（M3）', 'label_en': 'Working Memory (M3)'},
    {'id': 'provider', 'order': 11, 'label_zh': '插件与提供商', 'label_en': 'Plugins & Providers'},
    {'id': 'pacing', 'order': 12, 'label_zh': '主动投放节制（B1）', 'label_en': 'Proactive pacing (B1)'},
    {'id': 'review', 'order': 13, 'label_zh': '主动复习与回忆化（H）', 'label_en': 'Review & reminiscence (H)'},
    {'id': 'channel', 'order': 14, 'label_zh': '渠道绑定与群认知', 'label_en': 'Channel binding & group cognition'},
    {'id': 'tool_trace', 'order': 15, 'label_zh': '工具轨迹治理', 'label_en': 'Tool trace governance'},
    # 兜底组：未登记键落这里（App 侧同名「其他高级开关」，排在最后）
    {'id': 'other', 'order': 99, 'label_zh': '其他高级开关', 'label_en': 'Other Advanced Flags'},
]

OTHER_GROUP = 'other'
_GROUP_ORDER: dict[str, int] = {g['id']: g['order'] for g in CATALOG_GROUPS}
_UNKNOWN_ORDER = 9999          # 未登记键的组内顺序（排在同组最后）
_UNKNOWN_DESC_ZH = '高级运行时开关，普通使用保持默认即可，无需调整。'
_UNKNOWN_DESC_EN = 'Advanced runtime toggle; the default is fine for everyday use.'

# ── 每键一行：(key, group, order, visible, title_zh, desc_zh, title_en, desc_en) ──
# order 口径：visible=True 的键取 1..10（= App 常用开关的既有排列），其余键取
# 「组号×100 + 组内序号」，保证按 order 排序即可还原既有界面顺序。
_FLAG_ROWS: list[tuple] = [
    # ── 智能体运行与认知 ──
    ('weave_3d', 'agent', 1, True, '织库 3D 视图', '在织库里用 3D 方式查看记忆关系网。',
     'Weave 3D view', 'View the memory web in 3D inside Weave.'),
    ('agent_social_light_context', 'agent', 2, True, '社交精简背景',
     '社交短回复只带精简背景，响应更快、更省资源。',
     'Lightweight social context', 'Short social replies carry a compact context — faster and cheaper.'),
    ('agent_loop_group_chat', 'agent', 3, True, '群聊统一回复流程',
     '群聊回复接入各自记忆与背景，各角色互不串线。',
     'Unified group reply flow', 'Group replies use each character own memory, never mixing them up.'),
    ('agent_loop_social', 'agent', 4, True, '社交互动统一流程',
     '朋友圈、评论等互动走同一套流程，表现更自然。',
     'Unified social flow', 'Moments, comments and similar interactions share one flow, behaving more naturally.'),
    ('agent_loop_chat', 'agent', 101, False, '聊天主循环',
     '每条回复都先回忆、再判断，最后组织语言，回答更连贯。',
     'Chat main loop', 'Every reply recalls, reasons, then composes, giving more coherent answers.'),
    ('agent_loop_scheduler', 'agent', 102, False, '主动任务走统一流程',
     '到点提醒、主动关心等任务走同一套流程，并记录过程。',
     'Unified proactive task flow', 'Timed reminders and check-ins run through the same flow and are logged.'),
    ('agent_context_trim', 'agent', 103, False, '上下文按热度裁剪',
     '常聊的角色带入更多背景，少聊的角色精简带入，更省资源。',
     'Trim context by activity', 'Characters you talk to often get richer context; rarely used ones get a lighter version.'),
    ('agent_trace_group', 'agent', 104, False, '群聊过程记录',
     '记录群聊里每次回应的判断过程，只用于排查问题，不影响回复内容。',
     'Group chat trace', 'Records how each group reply was decided; troubleshooting only, replies unaffected.'),

    # ── 主动消息 ──
    ('proactive_naturalness_score', 'proactive', 201, False, '主动消息自然度评分',
     '发出前先打分，太生硬就重写一次，仍然别扭就不发。',
     'Naturalness scoring', 'Messages are scored before sending; awkward ones are rewritten once, then dropped.'),
    ('proactive_user_rhythm', 'proactive', 202, False, '作息学习',
     '学习你的活跃时段，把不重要的消息留到你在线时再发。',
     'Learn your routine', 'Learns when you are active and holds low-priority messages until then.'),
    ('proactive_inactive_char_skip', 'proactive', 203, False, '久未互动的角色免打扰',
     '近一天没有互动的角色不再主动搭话，把额度留给常聊的角色。',
     'Skip quiet characters', 'Characters with no interaction for a day stop reaching out, leaving room for the ones you use.'),
    ('proactive_strategy_plugins', 'proactive', 204, False, '内容策略交给插件',
     '改由策略插件决定主动内容，内核不再重复产出同类内容。',
     'Strategy plugins', 'Strategy plugins decide proactive content; the core stops producing the same kind.'),
    ('proactive_segment_guard', 'proactive', 205, False, '分块护栏',
     '发送前过滤残缺句子与空段落，并做常识校验，避免发出半句话。',
     'Segment guard', 'Filters broken sentences and empty blocks, plus a sanity check before sending.'),
    ('proactive_topic_guard', 'proactive', 206, False, '主题熔断',
     '同一话题短时间内被反复提及时自动收口，不再重复催促。',
     'Topic circuit breaker', 'Stops repeating a topic that keeps coming up within a short window.'),

    # ── 群聊小游戏 ──
    ('group_chat_games', 'games', 301, False, '群聊小游戏',
     '群聊里小游戏的总开关，关闭后游戏入口与相关功能一并隐藏。',
     'Group chat games', 'Master switch for group games; turning it off hides the entry and related features.'),

    # ── AI 自主生活 ──
    ('life_loop_enabled', 'life', 401, False, '自主生活循环',
     '每隔一段时间让角色自主决定下一步做什么，形成自己的生活节奏。',
     'Autonomous life loop', 'Periodically lets a character decide what to do next, forming its own daily rhythm.'),
    ('life_loop_llm', 'life', 402, False, '生活文案生成',
     '允许用大模型写生活动态，每个角色每天最多两次，更生动也更费资源。',
     'Life copy generation', 'Lets the model write life updates, at most twice per character per day.'),
    ('life_chat_driven_enabled', 'life', 403, False, '聊天联动生活',
     '聊天里提到的事会影响角色接下来的安排与生活。',
     'Chat-driven life', 'What you mention in chat shapes the character upcoming plans.'),
    ('life_home_worldmap_enabled', 'life', 404, False, '小家大地图',
     '在小家里展示世界大地图及相关的自主行为。',
     'Home world map', 'Shows the world map in Home along with related autonomous behaviour.'),
    ('promise_self_side_split', 'life', 405, False, '承诺按受益方分流',
     '角色自己办的事到点自己回来说明，不再反过来叫你。',
     'Split promises by beneficiary', 'Things a character does for itself are reported back by itself, not pushed to you.'),
    ('timer_render_subject_fix', 'life', 406, False, '到期提醒话术优化',
     '到点提醒按情境换不同说法，并跳过已经不合适的提醒。',
     'Better reminder wording', 'Timed reminders use situational wording and skip ones that no longer fit.'),
    ('life_event_no_replay', 'life', 407, False, '生活动作不回放',
     '已经发生的一次性生活动作不再被反复提起或复习。',
     'No replay of life events', 'One-off life actions are not brought up again or reviewed repeatedly.'),
    ('life_memory_write_retry', 'life', 408, False, '生活记忆写入加固',
     '生活相关记忆写入失败会自动重试，纯加固，不改变内容。',
     'Robust life memory writes', 'Life memory writes retry automatically on failure; content is unchanged.'),

    # ── 生命感增强 ──
    ('reply_delay_enabled', 'lifesense', 501, False, '动态回复延迟',
     '根据情境给回复加一点自然的等待，更像真人在打字。',
     'Dynamic reply delay', 'Adds a natural pause before replying, like a real person typing.'),
    ('spring_emotion_enabled', 'lifesense', 502, False, '弹簧式情绪',
     '情绪会随对话起伏，之后慢慢回到角色本来的性格基线。',
     'Spring-damper emotions', 'Emotions swing with the conversation, then settle back to the character baseline.'),
    ('life_share_enabled', 'lifesense', 503, False, '活动自然分享',
     '做完一件事后，角色会在合适的时机自然地跟你提一句。',
     'Natural activity sharing', 'After finishing something, the character mentions it naturally at a good moment.'),
    ('preoccupation_enabled', 'lifesense', 504, False, '心事微澜',
     '角色偶尔会带着一点没说出口的小心思，显得更有牵挂。',
     'Lingering thoughts', 'Characters occasionally carry an unspoken worry, feeling more attached.'),

    # ── 主动消息自然化（B1）──
    ('proactive_outreach_v2', 'outreach_natural', 10, True, '主动消息自然化',
     '按闲置时长和手头素材挑一个自然的由头再找你，少硬接旧话题。',
     'Natural outreach', 'Picks a natural reason to reach out based on idle time and available material.'),

    # ── 记忆检索与注入（实验灰度）──
    ('memory_temporal_recall', 'memory', 701, False, '时间线索记忆检索',
     '你提到具体时间时，额外按那个时间段再找一遍记忆。',
     'Time-aware recall', 'When you name a specific time, memories from that period are searched too.'),
    ('memory_recall_second_hop', 'memory', 702, False, '主动补查记忆',
     '允许角色在回答前再补查一次记忆，答案更完整。',
     'Second memory lookup', 'Lets the character look up memories once more before answering.'),
    ('memory_story_assemble', 'memory', 703, False, '记忆成段叙述',
     '把相关的记忆串成一小段有前因后果的叙述再使用。',
     'Story-like assembly', 'Related memories are stitched into a short cause-and-effect narrative.'),
    ('memory_peak_cutoff', 'memory', 704, False, '记忆自然收敛',
     '相关度整体偏低时自动减少条数，不再硬凑。',
     'Natural cutoff', 'When overall relevance is low, fewer memories are used instead of padding the list.'),
    ('memory_chain_builder', 'memory', 705, False, '记忆自动挂链',
     '新记忆会自动挂到相近的旧记忆上，形成可追溯的记忆链。',
     'Memory chaining', 'New memories attach to related older ones, forming a traceable chain.'),
    ('memory_chain_expand', 'memory', 706, False, '沿链补充上下文',
     '命中记忆时顺带补上相邻片段，前因后果更完整。',
     'Expand along the chain', 'Adds neighbouring pieces when a memory is hit, for fuller context.'),
    ('memory_tiered_decay', 'memory', 707, False, '记忆分层衰减',
     '可靠的记忆记得久，把握不大的忘得快，太旧自动归档。',
     'Tiered memory decay', 'Confident memories last longer, uncertain ones fade faster and archive when too old.'),
    ('memory_tiered_inject', 'memory', 708, False, '记忆分层注入',
     '最重要的记忆完整带入，其余精简带入，更省资源。',
     'Tiered memory injection', 'The most important memory is included in full, the rest condensed.'),
    ('memory_trace_debug', 'memory', 709, False, '记忆检索轨迹',
     '把检索过程与打分写入日志，方便排查，不影响回复。',
     'Retrieval trace', 'Writes retrieval steps and scores to logs for troubleshooting; replies unaffected.'),
    ('memory_supersede', 'memory', 710, False, '记忆取代',
     '出现新情况后，被取代的旧记忆不再当作当前事实使用。',
     'Memory supersede', 'Once something changes, outdated memories stop counting as current facts.'),
    ('current_facts_active_only', 'memory', 711, False, '近况只用最新信息',
     '描述你的近况时只采用最新信息，旧信息不再冒充现状。',
     'Current facts only', 'Descriptions of your situation use only the latest information.'),
    ('marker_recovery', 'memory', 712, False, '标记截断补救',
     '提取标记被截断时立刻走备用方式补一次，避免漏记。',
     'Marker recovery', 'If extraction markers get truncated, a fallback pass recovers the content.'),
    ('review_daily_plus', 'memory', 713, False, '主动复习扩容',
     '每天主动回忆的额度从 3 条提高到 4 条。',
     'More daily reviews', 'Daily recall quota raised from three items to four.'),
    ('memory_write_receipt', 'memory', 714, False, '记忆写入回执',
     '每条记忆写入时留一条回执，方便确认与排查。',
     'Write receipt', 'Each memory write leaves a receipt for confirmation and troubleshooting.'),
    ('memory_admission_gate', 'memory', 715, False, '长期事实准入把关',
     '写入长期事实前先查重、判矛盾，避免记下互相冲突的内容。',
     'Admission gate for facts', 'Long-term facts are de-duplicated and checked for conflicts before being stored.'),
    ('memory_utility_feedback', 'memory', 716, False, '记忆效用微调',
     '根据记忆被用到的实际情况微调它的重要程度。',
     'Usefulness tuning', "Adjusts a memory's importance based on how useful it actually turned out."),
    ('vector_user_scope', 'memory', 717, False, '向量按账号隔离',
     '向量记忆按账号分开读取，不同账号之间互不可见。',
     'Per-account vectors', 'Vector memories are read per account so accounts never see each other content.'),

    # ── 编纂知识与前瞻意图 ──
    ('curated_knowledge', 'curated', 801, False, '长期知识层',
     '角色设定、你的硬档案等长期知识始终在场，不随记忆淡忘。',
     'Curated knowledge layer', 'Character rules and your core profile stay present instead of fading.'),
    ('prospective_intent_enabled', 'curated', 802, False, '未来约定记录',
     '对话里出现「以后要做的事」时记下来，留作以后提起。',
     'Record future intentions', 'Captures things to do later mentioned in chat, for future follow-up.'),
    ('prospective_intent_trigger', 'curated', 803, False, '未来约定提醒',
     '到了约定时间或聊到相关线索时，角色会自然地提起。',
     'Trigger future intentions', 'The character brings things up naturally when the time comes or a cue appears.'),

    # ── 跨角色用户事实（B1）──
    ('global_user_facts', 'cross_char', 5, True, '跨角色共享你的近况',
     '开启后你的近况会在所有角色之间共享，避免角色停留在旧印象。',
     'Share your facts across characters',
     'Your current situation is shared with all characters, so none stays stuck on an outdated impression.'),
    ('user_fact_location', 'cross_char', 6, True, '位置与城市',
     '记录你所在的城市或位置，用于日常寒暄与主动关心。',
     'Location & city', 'Records your city or location for everyday small talk and check-ins.'),
    ('user_current_location_share', 'cross_char', 7, True, '位置跨角色共享',
     '只把「你在哪」这一类信息共享给所有角色，敏感类别仍需单独开启。',
     'Share location across characters',
     'Only your location is shared with every character; sensitive categories need their own switch.'),
    ('user_fact_relationship', 'cross_char', 8, True, '感情状态（隐私）',
     '记录你的感情状态，属敏感信息，需你单独开启。',
     'Relationship status (private)', 'Records your relationship status; sensitive and off until you enable it.'),
    ('user_fact_health', 'cross_char', 9, True, '健康状况（隐私）',
     '记录你的健康近况，属敏感信息，需你单独开启。',
     'Health (private)', 'Records your health; sensitive and off until you enable it.'),
    ('cross_char_fact_sync', 'cross_char', 901, False, '跨角色对齐',
     '发现不同角色记的同一件事不一致时，把旧的标记为已过时。',
     'Cross-character sync', 'When characters hold conflicting versions of a fact, the older one is marked outdated.'),
    ('cross_char_fact_projection', 'cross_char', 902, False, '变化留痕',
     '你的情况发生变化时，在记忆本里留一条同步记录。',
     'Change projection', 'When something about you changes, a sync note is written to the memory book.'),
    ('user_fact_job', 'cross_char', 903, False, '工作与学业', '记录你的工作或学业近况。',
     'Work & study', 'Records what you are working on or studying.'),
    ('user_fact_living', 'cross_char', 904, False, '居住状况', '记录你是独居还是和谁一起住。',
     'Living situation', 'Records whether you live alone and with whom.'),
    ('user_fact_goal_state', 'cross_char', 905, False, '近期目标与状态',
     '记录你最近在忙什么、状态如何。',
     'Recent goals & state', 'Records what you have been working on lately and how you are doing.'),

    # ── 工作记忆（M3）──
    ('working_state_enabled', 'working', 1001, False, '工作记忆积累',
     '每轮对话后记录当下的认知状态，为后续回复做准备。',
     'Working memory capture', 'Records the current state after each turn to prepare later replies.'),
    ('working_state_inject', 'working', 1002, False, '工作记忆注入',
     '把记录下来的状态带进回复，让对话更连贯。',
     'Working memory injection', 'Feeds the captured state into replies for better continuity.'),

    # ── 插件与提供商 ──
    ('provider_registry', 'provider', 1101, False, '模型服务注册口',
     '允许通过插件注册新的模型或语音服务，并按配置选用。',
     'Provider registry', 'Lets plugins register model or voice services, selected by configuration.'),
    ('plugin_disabled_route_gate', 'provider', 1102, False, '插件停用后彻底不可访问',
     '插件被停用后，它的接口与页面也一并停止响应（默认保持关闭，仅在需要严格停用插件时开启）。',
     'Stopped plugins fully unreachable',
     'When a plugin is stopped, its endpoints and pages stop responding as well.'),
    ('plugin_user_scope', 'provider', 1103, False, '插件列表按账号收敛',
     '插件列表只看得到内置插件与自己家庭安装的插件，别的家庭装的插件不再出现。',
     'Per-account plugin list',
     'The plugin list shows only built-in plugins and those your own family installed.'),
    ('plugin_runtime_scope', 'provider', 1104, False, '插件功能按账号隔离',
     '别人家庭安装的插件不再参与你的对话、工具与页面，插件也不能替你操作别人的角色数据。',
     'Per-account plugin runtime',
     'Plugins installed by other families no longer take part in your chats, tools or pages, '
     'and plugins cannot touch another account\'s character data.'),

    # ── 主动投放节制（B1）──
    ('outreach_hour_window_v1', 'pacing', 1201, False, '主动消息投放时段',
     '把主动性较低的消息限制在白天到晚上的时段内发送。',
     'Outreach time window', 'Lower-value messages are only sent during daytime and evening hours.'),
    ('outreach_type_mix_v1', 'pacing', 1202, False, '主动消息类型配额',
     '限制每类消息的每日条数，避免某一类刷屏。',
     'Outreach type quota', 'Caps each message type per day so one kind cannot flood you.'),
    ('outreach_session_rate_v1', 'pacing', 1203, False, '会话级节流',
     '同一会话的消息限制条数与最小间隔，避免连环催促。',
     'Per-session rate limit', 'Caps messages per session with a minimum gap, avoiding back-to-back pings.'),

    # ── 主动复习与回忆化（H）──
    ('review_exclude_expired_plan', 'review', 1301, False, '复习跳过过期安排',
     '回忆时不再挑入已经过期或只适用一时的安排。',
     'Skip expired plans in review', 'Expired or momentary plans are excluded from recall.'),
    ('review_reinforce_event_cap', 'review', 1302, False, '一次性事件强化上限',
     '一次性事件被反复回忆的次数设上限，避免过度强化。',
     'Cap one-off event reinforcement', 'Limits how often a one-off event can be reinforced.'),
    ('review_reminisce_framework', 'review', 1303, False, '回忆化口吻',
     '回忆往事时用更自然的怀旧口吻，而不是当成正在发生的事。',
     'Reminiscence tone', 'Recalls the past in a nostalgic voice instead of presenting it as ongoing.'),
    ('review_plan_expire_stale', 'review', 1304, False, '过期安排自动标记',
     '每日维护时把过期的安排自动标为已过时。',
     'Auto-expire stale plans', 'Daily maintenance marks expired plans as outdated.'),
    ('review_plan_validity_extract', 'review', 1305, False, '记录安排有效期',
     '记录安排时附带有效期，便于以后判断是否已过期。',
     'Record plan validity', 'Plans are stored with a validity window so expiry can be judged later.'),

    # ── 渠道绑定与群认知 ──
    ('channel_binding_v2', 'channel', 1401, False, '渠道绑定 v2',
     '外部渠道按账号独立绑定，换设备或重建后需重新确认。',
     'Channel binding v2', 'External channels bind per account; re-confirm after switching devices or rebuilding.'),
    ('domain_event_log_enabled', 'channel', 1402, False, '事件流水记录',
     '把系统里的关键事件记成流水，方便审计与排查。',
     'Domain event log', 'Records key system events as a stream for auditing and troubleshooting.'),
    ('domain_event_retention_days', 'channel', 1403, False, '事件流水保留天数',
     '事件流水保留多久，0 表示永久保留；数值型，无法在 App 内热改。',
     'Event log retention days', 'How long the event log is kept; 0 means forever. Numeric, not changeable in the app.'),
    ('group_cognition_v2', 'channel', 1404, False, '群聊认知升级',
     '让群里形成共享记忆与群体认知，还需逐个群单独开启。',
     'Group cognition v2', 'Builds shared memory and group awareness; still needs enabling per group.'),
    ('group_memory_compact', 'channel', 1405, False, '群记忆夜间合并',
     '超过一周的群记忆每晚合并成一条摘要，旧记录软删除。',
     'Nightly group memory compaction',
     'Group memories older than a week merge into one summary nightly, and old rows are soft-deleted.'),

    # ── 工具轨迹治理 ──
    ('agent_trace_scheduler_only_executed', 'tool_trace', 1501, False, '只记录真正执行的任务',
     '没有触发的定时任务不再写日志，减少无用记录。',
     'Log only executed tasks', 'Scheduled tasks that did not fire are no longer logged, cutting noise.'),
    ('agent_trace_scheduler_mark_exec_error', 'tool_trace', 1502, False, '执行失败标记为错误',
     '真正执行失败的任务记为失败，而不是记为未触发。',
     'Mark execution failures as errors', 'Tasks that actually failed are marked failed instead of blocked.'),
    ('mcp_stream_declarations', 'tool_trace', 1503, False, '流式会话工具声明',
     '流式回复也能带上可用工具的说明。',
     'Tool declarations in streaming', 'Streaming replies also carry the list of available tools.'),
    ('agent_tool_exec_trace', 'tool_trace', 1504, False, '工具执行留痕',
     '每次工具执行都留一条记录，方便看真实成败。',
     'Tool execution trace', 'Each tool execution is logged so real outcomes are visible.'),
]

FLAG_CATALOG: dict[str, dict] = {
    row[0]: {
        'title_zh': row[4], 'title_en': row[6],
        'desc_zh': row[5], 'desc_en': row[7],
        'group': row[1], 'order': row[2], 'visible': bool(row[3]),
    }
    for row in _FLAG_ROWS
}


def _is_zh(lang: str) -> bool:
    '''语言判定：zh*（含 zh-CN / zh-Hans）走中文，其余走英文；缺省 zh。'''
    return str(lang or 'zh').lower().startswith('zh')


def _user_scoped_keys() -> frozenset:
    '''按账号生效的键集合（唯一真源 = flag_service，本模块不复制一份）。'''
    from app.application.flag_service import USER_SCOPED_FLAG_KEYS
    return USER_SCOPED_FLAG_KEYS


def meta_for(key: str, lang: str = 'zh') -> dict:
    '''单键展示元数据（O(1) 纯内存；未登记键落「其他」组、标题用键名）。

    返回 {title, desc, group, group_order, order, visible} —— App 端 meta 字段契约。
    '''
    zh = _is_zh(lang)
    m = FLAG_CATALOG.get(key)
    if m is None:
        return {'title': key or '', 'desc': _UNKNOWN_DESC_ZH if zh else _UNKNOWN_DESC_EN,
                'group': OTHER_GROUP, 'group_order': _GROUP_ORDER[OTHER_GROUP],
                'order': _UNKNOWN_ORDER, 'visible': False}
    group = m['group'] if m['group'] in _GROUP_ORDER else OTHER_GROUP
    return {'title': m['title_zh'] if zh else m['title_en'],
            'desc': m['desc_zh'] if zh else m['desc_en'],
            'group': group, 'group_order': _GROUP_ORDER[group],
            'order': m['order'], 'visible': bool(m['visible'])}


def catalog_for(keys, lang: str = 'zh') -> list[dict]:
    '''按分组产出目录：[{group, group_order, items:[{key, title, desc, order, visible, scope}]}]。

    纯内存 O(n)：n = 传入键数（AGENT_FLAGS 全量约 78）；分组按 CATALOG_GROUPS 顺序、
    组内按 order 排序；未登记键进「其他」组。
    '''
    zh = _is_zh(lang)
    user_scoped = _user_scoped_keys()  # 一次取齐，避免逐键导入（O(n) 纯内存）
    buckets: dict[str, list[dict]] = {}
    for k in keys or []:
        m = FLAG_CATALOG.get(k)
        if m is None:
            item = {'key': k, 'title': k or '',
                    'desc': _UNKNOWN_DESC_ZH if zh else _UNKNOWN_DESC_EN,
                    'order': _UNKNOWN_ORDER, 'visible': False}
            group = OTHER_GROUP
        else:
            group = m['group'] if m['group'] in _GROUP_ORDER else OTHER_GROUP
            item = {'key': k, 'title': m['title_zh'] if zh else m['title_en'],
                    'desc': m['desc_zh'] if zh else m['desc_en'],
                    'order': m['order'], 'visible': bool(m['visible'])}
        item['scope'] = 'user' if k in user_scoped else 'server'
        buckets.setdefault(group, []).append(item)
    out = []
    for g in CATALOG_GROUPS:  # 已按 order 升序
        items = buckets.get(g['id'])
        if not items:
            continue
        items.sort(key=lambda i: (i['order'], i['key']))
        out.append({'group': g['id'], 'group_order': g['order'],
                    'label_zh': g['label_zh'], 'label_en': g['label_en'],
                    'label': g['label_zh'] if zh else g['label_en'],
                    'items': items})
    return out
