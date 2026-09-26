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
    {'id': 'device_action', 'order': 16, 'label_zh': '行动通道', 'label_en': 'Device Action Channel'},
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
    ('agent_loop_social', 'agent', 4, True, '外部动态主动搭话',
     'AI 主动提它在外部看到的新动态、到点的节日提醒时，会带上自己的记忆和你们的背景，也不会把外来的内容当成你俩的事记下来。',
     'Chatter about external updates', 'When it brings up something new it saw outside, or a reminder that is due, it draws on its own memory of you, and outside content is not filed as something the two of you share.'),
    ('agent_loop_scheduler', 'agent', 102, False, '主动任务过程留痕',
     '把到点提醒、主动关心这些任务的处理过程记下来，只用于排查问题；关掉也不影响这些提醒本身。',
     'Log proactive task handling', 'Keeps a record of how reminders and check-ins were handled, for troubleshooting only; turning it off does not affect those messages themselves.'),
    ('agent_loop_chat', 'agent', 101, False, '聊天工具统一入口',
     '只管聊天里日历备注、备忘录这类本地工具的记录方式；关掉也照常记录，只是换旧的保存路径。',
     'Unified chat tool entry', 'Affects only how chat tools such as calendar notes and memos are recorded; when off they are still saved, just via the older path.'),
    ('agent_context_trim', 'agent', 103, False, '上下文按热度裁剪',
     '常聊的角色带入更多背景，少聊的角色精简带入，更省资源。',
     'Trim context by activity', 'Characters you talk to often get richer context; rarely used ones get a lighter version.'),
    ('agent_trace_group', 'agent', 104, False, '群聊过程记录',
     '记录群聊里每次回应的判断过程，只用于排查问题，不影响回复内容。',
     'Group chat trace', 'Records how each group reply was decided; troubleshooting only, replies unaffected.'),
    ('context_budget_reserve', 'agent', 105, False, '长对话预算预留与裁剪留痕',
     '对话很长时，先给回复和工具各留出一块空间，再对超出的背景内容做取舍，并记下这次裁掉了什么，'
     '避免要紧的部分被悄悄丢掉却查不到。',
     'Reserve headroom in long conversations', 'In very long conversations it first sets aside room for the reply '
     'and for tools, then trims the overflow and records what was trimmed, so nothing goes missing untraceably.'),

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
    ('proactive_topic_guard', 'proactive', 206, False, '同一话题不重复催',
     '你已经回应过、说过不用了，或同一件生活小事几小时内已被提过两次时，它就先不在这件事上主动念叨你。',
     'No nagging on one topic', 'Once you have answered, waved it off, or the same little thing has already come up twice within a few hours, it drops it for now.'),
    ('two_pass_trace', 'proactive', 207, False, '开口前先读一遍现状',
     '主动发消息前，先把它已知的当前状况（正在进行的事、你的近况、还没完成的约定）摆在最前面，'
     '减少拿已经过时的情况当作此刻继续说。只影响它怎么组织措辞，不会因此多叫一次模型。',
     'Re-read the current state before reaching out', 'Before it writes, it re-reads what is currently known — what is going on, your recent situation, '
     'open plans — so stale snapshots are less likely to be described as if they were happening now.'),
    ('two_pass_trace_all_chars', 'proactive', 208, False, '两遍重读全量放开',
     '两遍重读：对所有角色放开（默认关；开=不再看灰度白名单）',
     'Re-read the current state for every character', 'Lets the re-read cover all characters rather than only the few in the grey-release list; off by default keeps things as they are.'),

    # ── 群聊小游戏 ──
    ('group_chat_games', 'games', 301, False, '小游戏总开关',
     '小游戏功能的总开关：关闭后玩法列表清空、也不能从面板开局，卡住的对局不再自动推进；已经开始的对局仍可查看。',
     'Games master switch', 'Master switch for games: when off, no games are offered and stalled rounds are no longer nudged forward; rounds already running stay visible.'),

    # ── AI 自主生活 ──
    ('life_loop_enabled', 'life', 401, False, '自主生活循环',
     '每隔一段时间让角色自主决定下一步做什么，形成自己的生活节奏。',
     'Autonomous life loop', 'Periodically lets a character decide what to do next, forming its own daily rhythm.'),
    ('life_loop_llm', 'life', 402, False, '生活文案生成',
     '允许用大模型写生活动态，每个角色每天最多两次，更生动也更费资源。',
     'Life copy generation', 'Lets the model write life updates, at most twice per character per day.'),
    ('life_chat_driven_enabled', 'life', 403, False, '照你交代的事去安排',
     '你在聊天里明确让它去做的事（比如去休息、帮忙喂宠物）会排进它自己的日程并去做；随口提到的内容不会改变它的安排。',
     'Follow through on what you ask', 'Things you explicitly ask it to do, such as taking a break or feeding the pet, go into its own schedule; passing mentions do not change its plans.'),
    ('life_home_worldmap_enabled', 'life', 404, False, '小家大地图',
     '小家里用一整张地图展示各个房间和角色当前所在位置；关掉则回到原来的单个房间视图。',
     'Home world map', 'Home shows every room and where the character currently is on one map; turning it off falls back to the old single-room view.'),
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
     '闹别扭、吃醋或情绪低落之后，它会把这份心思压在心底几天，情绪也跟着偏低，直到你哄一句或时间慢慢冲淡。',
     'Lingering thoughts', 'After a quarrel, jealousy or a low mood it keeps that thought to itself for days and stays slightly out of sorts, until you soothe it or time passes.'),

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
     '把同一件事的前后记忆串成一小段有前因后果的叙述再使用；需先开启记忆自动挂链，否则没有变化。',
     'Story-like assembly', 'Stitches the earlier and later parts of the same thing into a short narrative. Needs memory chaining enabled first, otherwise nothing changes.'),
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
     '有把握的事记得更久，没把握的推测忘得更快；重要的事到点后先收存起来而不是直接删掉，之后还能找回。',
     'Tiered memory decay', 'Confident memories last longer while shaky guesses fade faster; important ones are set aside instead of deleted outright when they expire, and can come back.'),
    ('memory_tiered_inject', 'memory', 708, False, '记忆分层注入',
     '最重要的那条完整带入，其余只带一句要点，再补一行那天的背景，让它先抓重点又不丢上下文。',
     'Tiered memory injection', 'The most important memory comes in full, the rest as a single key line, plus one line of background from that day so it keeps the highlights without losing context.'),
    ('memory_trace_debug', 'memory', 709, False, '记忆运行留痕',
     '把找记忆、淡忘、合并、降级这些过程的详细留痕记下来，只用于排查问题，不改变任何回复内容。',
     'Memory diagnostics trace', 'Records detailed steps for retrieval, forgetting, merging and downgrading; troubleshooting only, nothing about replies changes.'),
    ('memory_supersede', 'memory', 710, False, '改口后收起旧记忆',
     '你明确纠正过的事，被推翻的旧记忆会正式收起来，连怀旧、复习也不再翻出；关掉则它们仍可能被想起。',
     'Retire superseded memories', 'After you clearly correct something, the overturned memory is put away and no longer surfaces in reminiscing or review; when off it may still come up.'),
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
    ('memory_admission_gate', 'memory', 715, False, '记录准入把关',
     '记东西前先过一道规则：它自己推测的会标为待核实、不冒充你说过的，跟你无关的技术杂讯也不会入库，长期事实另外查重、判矛盾。',
     'Admission gate for memories', 'A rule pass before anything is stored: its own guesses are marked unverified instead of passing as your words, unrelated technical noise is never recorded, and long-term facts are de-duplicated and checked for conflicts.'),
    ('memory_utility_feedback', 'memory', 716, False, '记忆效用微调',
     '根据记忆被用到的实际情况微调它的重要程度。',
     'Usefulness tuning', "Adjusts a memory's importance based on how useful it actually turned out."),
    ('vector_user_scope', 'memory', 717, False, '向量按账号隔离',
     '向量记忆按账号分开读取，不同账号之间互不可见。',
     'Per-account vectors', 'Vector memories are read per account so accounts never see each other content.'),
    ('survival_checklist', 'memory', 718, False, '长对话压缩后仍记住要紧事',
     '对话很长时，较早的内容会被收拢压缩；这条把「你正在推进的目标、还没办完的约定、你说过的硬性要求」单独保住，'
     '压缩后也不丢，减少它拿已经过时的情况当作此刻继续说。',
     'Keep the essentials through compaction', 'Long conversations get condensed; this keeps your current goal, '
     'unfinished plans, and the hard requirements you stated intact, so stale situations are less likely to be described as happening now.'),
    ('decision_layer_shadow', 'memory', 719, False, '重要程度与归类判断留痕',
     '在“这条记忆有多重要”“这是往事还是安排”这类自动判断上，额外记一条过程留痕，便于事后核对判断得准不准；'
     '判断结果与现在完全一样，不会因此改任何东西，关掉则不留这条记录。',
     'Judgement diagnostics note',
     'Automatic calls such as how important a memory is, or whether something is a past event or an '
     'arrangement, also leave a note so the judgement can be cross-checked later. The outcomes stay exactly '
     'as they are now; turning this off simply stops leaving the note.'),
    ('fact_lifecycle_policy', 'memory', 720, False, '事实过期口径留痕',
     '按一张统一的策略表统计每类记忆的留存与过期情况，只在后台记录、不改动任何内容。',
     'Fact lifetime policy (observation)',
     'Counts how each kind of memory ages against one shared policy table. '
     'Observation only — nothing is changed and no content is filtered.'),
    ('recall_gate_shadow', 'memory', 721, False, '检索时机判断留痕',
     '每一轮回复前额外记一条「这一轮要不要翻记忆」的判断过程，便于事后核对该判断准不准；'
     '是否会翻记忆与现在完全一样，不会因此改任何东西，关掉则不留这条记录。',
     'Retrieval timing note',
     'A short note of the before-reply call on whether to look through memories, so it can be '
     'cross-checked later. Whether memories are looked up stays exactly as it is now; turning this '
     'off simply stops leaving the note.'),
    ('current_view_filter', 'memory', 722, False, '生活分享只认当下有效的记录',
     '它跟你提自己的近况时，只用仍然有效的条目，已经被取代、收起来的旧近况不再冒充此刻；'
     '默认关闭，关掉时与现在完全一样。',
     'Life updates only from what still holds',
     'When it shares how its own days have been going, only entries that still hold are used, so '
     'superseded or archived ones no longer pass as the present. Off by default, and turning it off '
     'keeps things exactly as they are now.'),

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
     '你的某项近况有了新情况后，各角色记忆里还停留在旧情况的条目会被标记为已过时，不再当作当下事实。',
     'Cross-character sync', 'Once one of your facts is updated, entries still holding the old value in a character memory are marked outdated and no longer treated as current.'),
    ('cross_char_fact_projection', 'cross_char', 902, False, '变化留痕',
     '在「跨角色对齐」开启的前提下，你的近况发生更新时，记忆本里会额外留一条同步记录。',
     'Change projection', 'With cross-character sync enabled, an update to your facts also leaves a sync note in the memory book.'),
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
     '关心、回忆这类不太期待回复的消息，只在中午到夜里这段时间发；你平时在线的时间会适当放宽。',
     'Outreach time window', 'Messages that rarely get a reply, such as check-ins and reminiscing, only go out between midday and night; the hours you are usually around widen that window.'),
    ('outreach_type_mix_v1', 'pacing', 1202, False, '主动消息类型配额',
     '给每类消息各设每日条数上限，避免某一类刷屏；翻出来的旧事也会以一句你真能接的话收尾，而不是空泛问候。',
     'Outreach type quota', 'Each kind of message gets its own daily cap so one type cannot flood you, and recalls now end with something you can actually answer instead of a generic greeting.'),
    ('outreach_session_rate_v1', 'pacing', 1203, False, '会话级节流',
     '同一会话的消息限制条数与最小间隔，避免连环催促。',
     'Per-session rate limit', 'Caps messages per session with a minimum gap, avoiding back-to-back pings.'),

    # ── 主动复习与回忆化（H）──
    ('review_exclude_expired_plan', 'review', 1301, False, '复习跳过过期安排',
     '回忆时不再挑入已经过期的安排和只属于当时的心情状态，怀旧话题每天最多提一次。',
     'Skip expired plans in review', 'Recalls skip expired plans and momentary states, and nostalgic topics come up at most once a day.'),
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
    ('channel_binding_v2', 'channel', 1401, False, '渠道绑定按账号独立',
     '外部聊天渠道由每个账号各自绑定自己的角色，互不共用；关掉则退回所有账号共用同一条旧绑定。',
     'Per-account channel binding', 'External chat channels bind to characters per account instead of sharing one server-wide binding; turning it off falls back to the old shared binding.'),
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
    ('account_purge_scheduler', 'channel', 1406, False, '回收站到期自动清除',
     '宽限期到期的回收站账号，在夜间低峰自动彻底删除；默认关闭，开启前请确认备份策略到位。',
     'Automatic recycle-bin cleanup',
     'Accounts past their grace period are permanently removed during a low-traffic overnight window; '
     'off by default — confirm your backup routine first.'),

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

    # ── 行动通道（C1a：三条闸进常规开关页，默认由服务器锁定、App 内只读）──
    ('device_actions_enabled', 'device_action', 1601, False, '手机操作总开关',
     'AI 能不能在你的手机上替你做事（打开应用、点按、输入文字）的总闸；关掉后这类请求一律不执行。默认关闭。',
     'Phone actions master switch',
     'Whether the AI may act on your phone for you — opening apps, tapping, typing. '
     'When off, such requests never run. Off by default.'),
    ('device_actions_plugin_enabled', 'device_action', 1602, False, '插件提交手机操作',
     '在总开关已开的前提下，允许被放开的插件提交手机操作请求；没被放开的插件仍会被拒绝。默认关闭。',
     'Plugins may request phone actions',
     'With the master switch on, lets released plugins ask for phone actions; '
     'plugins not on the list are still refused. Off by default.'),
    ('device_actions_force_dry_run', 'device_action', 1603, False, '只看结论不动手机',
     '开启后，即使各项许可都已通过，也只给出判断结论，不会真的去操作手机。默认开启。',
     'Judge only, never touch the phone',
     'When on, even fully approved requests only get a verdict and never actually operate '
     'the phone. On by default.'),
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
