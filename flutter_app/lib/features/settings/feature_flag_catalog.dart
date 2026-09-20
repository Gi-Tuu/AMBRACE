// 服务器「高级开关」目录：模块分组 + 中文说明（2026-08-30，v3.4.0）
//
// 说明：
// - 这里只做「展示文案与分组」，开关真源仍是后端 AGENT_FLAGS / FeatureFlagService；
// - 顶部 4 个常用开关不在本目录，仍由 FeatureFlagsScreen._visibleKeys 管理；
// - 后端新增、这里未登记的键会自动进入「其他高级开关」兜底组，不会丢失。
//
// A4（2026-09-20）：目录元数据改由后端下发（GET /system/feature-flags 的 meta 字段），
// 这里的中文文案降级为**回落**——后端没返回（离线/老后端/新键）时才用，保证不丢文案。
// 纯展示文案/分组数据，无需依赖 Flutter UI 库。
import '../../services/feature_flag_service.dart';

class FlagMeta {
  /// 中文名称（一行）
  final String title;

  /// 简短说明（界面默认最多显示两行）
  final String short_;

  /// 完整说明（点「详情」展开）；为空则不显示详情按钮
  final String detail;

  const FlagMeta({required this.title, required this.short_, this.detail = ''});
}

class FlagGroup {
  final String title;
  final List<String> keys;
  const FlagGroup(this.title, this.keys);
}

class FeatureFlagCatalog {
  FeatureFlagCatalog._();

  /// 单个 flag 的展示元数据：后端下发优先 → 回落硬编码 _metas → 通用兜底文案。
  ///
  /// [backend] 传后端下发的 meta（无则 null）；后端只给一句话说明，既有硬编码的「详情」
  /// 更长，故保留下来一起展示，避免改后端下发后信息变少。
  static FlagMeta metaOf(String key, {FlagMetaInfo? backend}) {
    if (backend != null && backend.title.isNotEmpty) {
      final local = _metas[key];
      return FlagMeta(
        title: backend.title,
        short_: backend.desc,
        detail: local?.detail ?? '',
      );
    }
    return _metas[key] ??
        FlagMeta(
          title: key,
          short_: '高级运行时开关，普通使用保持默认即可，无需调整。',
          detail: '原始标识：$key\n\n这是一个未内置中文说明的开发/调试开关。'
              '若不确定它的作用，请保持默认，或查阅后端 AGENT_FLAGS 中的注释。',
        );
  }

  /// 依据「后端实际存在的高级键集合」产出有序分组（空组自动隐藏，未知键进兜底组）。
  static List<FlagGroup> groupEntries(Set<String> presentAdvancedKeys) {
    final used = <String>{};
    final out = <FlagGroup>[];
    for (final g in _groupOrder) {
      final ks = g.keys.where(presentAdvancedKeys.contains).toList();
      if (ks.isNotEmpty) {
        used.addAll(ks);
        out.add(FlagGroup(g.title, ks));
      }
    }
    final rest = presentAdvancedKeys.where((k) => !used.contains(k)).toList()..sort();
    if (rest.isNotEmpty) out.add(FlagGroup('其他高级开关', rest));
    return out;
  }

  /// 依据**后端元数据**产出有序分组（A4）：分组按 group_order、组内按 order 排序。
  ///
  /// 任意键缺后端 meta（离线 / 老后端 / 新键）时，这些键走既有 [groupEntries] 硬编码逻辑，
  /// 不会丢。[labelOf] 可把分组 id 映射成本地化标题（不传则用分组 id）。
  static List<FlagGroup> groupEntriesFromBackend(
    Set<String> presentAdvancedKeys,
    FeatureFlagService flags, {
    String Function(String groupId)? labelOf,
  }) {
    final metas = <String, FlagMetaInfo>{};
    final rest = <String>{};
    for (final k in presentAdvancedKeys) {
      final m = flags.metaOf(k);
      if (m == null) {
        rest.add(k);
      } else {
        metas[k] = m;
      }
    }
    final buckets = <String, List<String>>{};
    final groupOrder = <String, int>{};
    for (final e in metas.entries) {
      buckets.putIfAbsent(e.value.group, () => <String>[]).add(e.key);
      groupOrder[e.value.group] = e.value.groupOrder;
    }
    final ids = buckets.keys.toList()
      ..sort((a, b) {
        final c = (groupOrder[a] ?? 999).compareTo(groupOrder[b] ?? 999);
        return c != 0 ? c : a.compareTo(b);
      });
    final out = <FlagGroup>[];
    for (final id in ids) {
      final ks = buckets[id]!..sort((a, b) => metas[a]!.order.compareTo(metas[b]!.order));
      out.add(FlagGroup(labelOf?.call(id) ?? id, ks));
    }
    if (rest.isNotEmpty) out.addAll(groupEntries(rest));
    return out;
  }

  // ── 分组顺序（即界面从上到下顺序）──────────────────────────────
  static const List<FlagGroup> _groupOrder = [
    FlagGroup('智能体运行与认知', [
      'agent_loop_chat',
      'agent_loop_scheduler',
      'agent_context_trim',
      'agent_trace_group',
      'agent_loop_group_chat',
      'agent_loop_social',
      'agent_social_light_context',
      'weave_3d',
    ]),
    FlagGroup('主动消息', [
      'proactive_naturalness_score',
      'proactive_user_rhythm',
      'proactive_inactive_char_skip',
      'proactive_strategy_plugins',
      'proactive_segment_guard',
      'proactive_topic_guard',
    ]),
    FlagGroup('群聊小游戏', [
      'group_chat_games',
    ]),
    FlagGroup('AI 自主生活', [
      'life_loop_enabled',
      'life_loop_llm',
      'life_chat_driven_enabled',
      'life_home_worldmap_enabled',
      'promise_self_side_split',
      'timer_render_subject_fix',
      'life_event_no_replay',
      'life_memory_write_retry',
    ]),
    FlagGroup('生命感增强', [
      'reply_delay_enabled',
      'spring_emotion_enabled',
      'life_share_enabled',
      'preoccupation_enabled',
    ]),
    FlagGroup('主动消息自然化（B1）', [
      'proactive_outreach_v2',
    ]),
    FlagGroup('记忆检索与注入（实验灰度）', [
      'memory_temporal_recall',
      'memory_recall_second_hop',
      'memory_story_assemble',
      'memory_peak_cutoff',
      'memory_chain_builder',
      'memory_chain_expand',
      'memory_tiered_decay',
      'memory_tiered_inject',
      'memory_trace_debug',
      'memory_supersede',
      'current_facts_active_only',
      'marker_recovery',
      'review_daily_plus',
      'memory_write_receipt',
      'memory_admission_gate',
      'memory_utility_feedback',
    ]),
    FlagGroup('编纂知识与前瞻意图', [
      'curated_knowledge',
      'prospective_intent_enabled',
      'prospective_intent_trigger',
    ]),
    FlagGroup('跨角色用户事实（B1）', [
      'global_user_facts',
      'user_current_location_share',
      'cross_char_fact_sync',
      'cross_char_fact_projection',
      'user_fact_location',
      'user_fact_job',
      'user_fact_relationship',
      'user_fact_living',
      'user_fact_goal_state',
      'user_fact_health',
    ]),
    FlagGroup('工作记忆（M3）', [
      'working_state_enabled',
      'working_state_inject',
    ]),
    FlagGroup('插件与提供商', [
      'provider_registry',
    ]),
    FlagGroup('主动投放节制（B1）', [
      'outreach_hour_window_v1',
      'outreach_type_mix_v1',
      'outreach_session_rate_v1',
    ]),
    FlagGroup('主动复习与回忆化（H）', [
      'review_exclude_expired_plan',
      'review_reinforce_event_cap',
      'review_reminisce_framework',
      'review_plan_expire_stale',
      'review_plan_validity_extract',
    ]),
    FlagGroup('渠道绑定与群认知', [
      'channel_binding_v2',
      'domain_event_log_enabled',
      'domain_event_retention_days',
      'group_cognition_v2',
      'group_memory_compact',
    ]),
    FlagGroup('工具轨迹治理', [
      'agent_trace_scheduler_only_executed',
      'agent_trace_scheduler_mark_exec_error',
      'mcp_stream_declarations',
      'agent_tool_exec_trace',
    ]),
  ];

  // ── 每个键的中文名称 / 两行短说明 / 完整说明 ────────────────────
  // 注意：以下中文文案仅作兜底，界面展示优先取 feature_flags_screen 里的 l10n（flagXxxTitle/Hint/Detail）。
  // 新增或调整某个开关的说明，请在 app_zh.arb / app_en.arb 双语同步，不要只改这里的中文。
  static const Map<String, FlagMeta> _metas = {
    // 智能体运行与认知
    'agent_loop_chat': FlagMeta(
      title: '聊天主循环',
      short_: 'AI 回复走统一的智能体主循环：理解 → 记忆 → 规划 → 回答。',
      detail: '开启后每条聊天都经过统一 Runtime，注入世界认知与记忆后再回答，'
          '角色更连贯；关闭则回退到旧的直接生成链路。一般保持开启，'
          '仅在排查新链路问题时临时关闭。',
    ),
    'agent_loop_scheduler': FlagMeta(
      title: '主动任务循环',
      short_: '定时/主动触发的任务走统一智能体循环。',
      detail: '让到点触发的提醒、主动关心等经过统一 Runtime 并记录过程；'
          '关闭后主动任务走旧链路。',
    ),
    'agent_context_trim': FlagMeta(
      title: '上下文按热度裁剪',
      short_: '低频角色少注入、高频角色全量注入，以节省 token。',
      detail: '低频角色缩小日摘要/织库等注入量，高频角色保持全量；'
          '关闭后所有角色统一全量注入，token 消耗更高。',
    ),
    'agent_trace_group': FlagMeta(
      title: '群聊过程记录',
      short_: '记录群聊中每个角色回应的决策过程（只写不读，用于排查）。',
      detail: '属于可观测日志，不影响回复内容；关闭后不再写群聊 trace。',
    ),

    // 主动消息
    'proactive_naturalness_score': FlagMeta(
      title: '主动消息自然度评分',
      short_: '低优先主动消息生成后评分，不自然就重试一次，仍差则不发。',
      detail: '用于减少生硬、打扰感的主动消息；关闭则主动消息原样发送。',
    ),
    'proactive_user_rhythm': FlagMeta(
      title: '用户作息学习',
      short_: '学习你的活跃时段，非活跃时间推迟低优先主动消息。',
      detail: '从聊天与主动日志推断作息；关闭则不区分时段，随时可能发。',
    ),

    // 群聊小游戏
    'group_chat_games': FlagMeta(
      title: '群聊游戏总开关',
      short_: '「小家 · 游戏机」整体入口与相关接口的总开关。',
      detail: '关闭后游戏入口与 API 都不展示，可整体回退游戏功能，不影响聊天。',
    ),

    // AI 自主生活
    'life_loop_enabled': FlagMeta(
      title: '生活循环主开关',
      short_: '约每 30 分钟让 AI 自主决策一次行为，形成自己的生活节奏。',
      detail: '关闭后角色的自主生活循环停止；你离线时也不再推进其生活。',
    ),
    'life_loop_llm': FlagMeta(
      title: '生活文案生成',
      short_: '允许用大模型写生活文案（每角色每日上限 2 次）。',
      detail: '让日记/动态更生动；关闭则只用模板规则文案以节省 token。',
    ),
    'life_chat_driven_enabled': FlagMeta(
      title: '聊天驱动生活',
      short_: '从聊天识别生活意图，联动改变 AI 的目标与活动。',
      detail: '例如你提到的事影响角色后续安排；关闭则聊天不联动生活循环。',
    ),
    'life_home_worldmap_enabled': FlagMeta(
      title: '小家大地图',
      short_: '「小家」中的世界大地图功能与相关自主行为。',
      detail: '关闭后小家不展示大地图。',
    ),

    // 生命感增强
    'reply_delay_enabled': FlagMeta(
      title: '动态回复延迟',
      short_: '按情境给回复加自然的短暂延迟（仅你主动发消息时生效）。',
      detail: '模拟真人思考/打字节奏；关闭则回复立即开始。',
    ),
    'spring_emotion_enabled': FlagMeta(
      title: '弹簧阻尼情绪',
      short_: '四维情绪 + 人格基线，情绪起伏后自然回落到基线。',
      detail: '让心情像真人一样波动而非跳变；关闭则用旧的简单情绪模型。',
    ),
    'life_share_enabled': FlagMeta(
      title: '活动自然分享',
      short_: '活动完成后在合适时机自然分享给你（带频率门控，防刷屏）。',
      detail: '关闭后 AI 不会主动分享刚完成的活动。',
    ),
    'preoccupation_enabled': FlagMeta(
      title: '心事微澜',
      short_: '让角色偶尔带着一点没说出口的小心事，更有牵挂感。',
      detail: '复用记忆子类型实现；关闭则无此效果。',
    ),

    // 主动消息自然化（B1）
    'proactive_outreach_v2': FlagMeta(
      title: '主动消息自然化',
      short_: '按闲置时长与素材挑选「接触意图」，告别硬续旧剧情与逐句复读。',
      detail: '开=run_tick 汇总层按闲置分级/素材前提/避开最近意图选接触意图，消息生成走意图分支（含转场句库、必须抛回问题）；关=意图不参与、走旧链路逐字节等价。建议灰度观察后再全量。',
    ),

    // 记忆检索与注入（实验灰度）
    'memory_temporal_recall': FlagMeta(
      title: '时间维度记忆检索',
      short_: '用户提到具体时间（昨天/上周/某月）时，补一条确定性时间窗检索。',
      detail: 'Ariadne 模块A：默认关=零行为变化；开=第一跳解析用户原话时间区间并走时间路召回，与语义检索合并重排。',
    ),
    'memory_recall_second_hop': FlagMeta(
      title: '按需二跳调取记忆（RECALL）',
      short_: '允许 AI 首轮输出 [RECALL] 标记，补查记忆后再生成一次。',
      detail: 'Ariadne 模块B：默认关=只剥离标记零行为；开=非流式路径镜像联网搜索循环做一次记忆二跳（流式只剥离）。',
    ),
    'memory_story_assemble': FlagMeta(
      title: '沿链半故事化组装',
      short_: '把同一记忆链的节点拼成一小段有前因后果的叙述再注入。',
      detail: 'Ariadne 模块C：默认关；当前为框架合入（链数据就绪后开=成链小块注入，否则与原路径等价）。',
    ),
    'memory_peak_cutoff': FlagMeta(
      title: '记忆自然收敛（去硬截断）',
      short_: '弱相关记忆整体低于相关度地板时自然收敛，避免硬塞条数。',
      detail: 'Ariadne 模块D：默认关；阈值经 104 例基准标定（稠密距离地板 0.50）。弃权/弱相关场景条数自然减少。',
    ),
    'memory_chain_builder': FlagMeta(
      title: '记忆链条建链',
      short_: '事件/洞察类新记忆自动挂到相近的既有记忆链上（零额外 LLM）。',
      detail: 'B1②：默认关=不挂链（回归保护）；开=写入后异步挂链，相似度 0.82、14 天窗、链长上限 12。',
    ),
    'memory_chain_expand': FlagMeta(
      title: '沿链上下文补全',
      short_: '检索命中时沿链补最多 2 个相邻节点，前因后果更完整。',
      detail: 'B1②：默认关；开=扩充节点降权 0.9、受 token 配额与 5 轮去重约束，不绕过预算。',
    ),
    'memory_tiered_decay': FlagMeta(
      title: '记忆分层衰减',
      short_: '高置信持久、低置信加速衰减，跌破阈值转冷归档。',
      detail: 'M2-S2 灰度开关：默认关=现状逐字节一致；开启前建议先跑分层快照脚本。',
    ),
    'memory_tiered_inject': FlagMeta(
      title: '记忆分层注入',
      short_: '核心记忆全量、其余记忆精简分层注入，节省 token。',
      detail: '#70 方案A：开=Top1 完整 + 其余精简注入；关=统一旧链路逐字节一致。',
    ),
    'memory_trace_debug': FlagMeta(
      title: '记忆检索轨迹调试',
      short_: '把检索的 query/各路命中/排序分数写入 trace，便于排查。',
      detail: '#70 方案B：只多写观测，不影响回复；关=检索/排序/trace 与现状一致。',
    ),
    'memory_supersede': FlagMeta(
      title: '记忆取代链（supersede）',
      short_: '新事实取代旧事实后，旧记忆按状态过滤不再注入。',
      detail: '#70 方案C：默认关（误取代比不取代更伤）；开=SQLite+Chroma 双通道按状态分流。',
    ),
    'current_facts_active_only': FlagMeta(
      title: '现状面只用现行记忆',
      short_: '注入的「现状/近况」只取当前有效的记忆，旧现状不再冒充新事实。',
      detail: '开（默认）：现状/事实注入面恒只取现行记忆，旧记忆被召回也标「往事/已过时」并统一降权；怀旧/复习面仍可见旧往事。关=一键回退旧行为。',
    ),
    'marker_recovery': FlagMeta(
      title: '标记截断保底',
      short_: '记忆标记被上下文截断时，源消息立即走备选通道补提取。',
      detail: 'M2-S5：写侧查重防重复；关=仅依赖批量补提。',
    ),
    'review_daily_plus': FlagMeta(
      title: '主动复习扩容',
      short_: '主动复习每日额度由 3 条提高到 4 条（M1-S7）。',
      detail: '关=回退每日 3 条，90 分钟最小间隔不变。',
    ),

    // 编纂知识与前瞻意图
    'curated_knowledge': FlagMeta(
      title: '编纂知识层（长期稳定事实）',
      short_: '人格铁律/用户硬档案/关系基线等长期知识确定性注入，不随记忆衰减。',
      detail: 'Ariadne 模块F：复用 world_facts 权威层加 kind 分治；默认关=零行为；开=constraint 无条件在场 + 其余按核心 TopN 与触发词命中注入。',
    ),
    'prospective_intent_enabled': FlagMeta(
      title: '前瞻意图-写入',
      short_: '对话中出现「未来约定/某线索时要做的事」时抽取落表（零新增 LLM）。',
      detail: 'Ariadne 模块G 写入段：默认关；开=extractor 便车多输出 INTENT 行并落 prospective_intents（幂等）。先开此段攒数据。',
    ),
    'prospective_intent_trigger': FlagMeta(
      title: '前瞻意图-触发',
      short_: '到期承诺由 AI 自然提起；线索命中只在当轮提醒、不主动发消息。',
      detail: 'Ariadne 模块G 触发段：默认关；开=时间型到期采集进 arbiter + 聊天线索确定性命中注入。建议在写入段观察 2-3 天后开启。',
    ),

    // 跨角色用户事实（B1）
    'global_user_facts': FlagMeta(
      title: '全局用户事实（USER NOW）',
      short_: '用户级单值事实（位置等）跨角色共享，低活跃角色不再停留在旧信息。',
      detail: '总开关（默认关）。开启后，系统会从对话与 GPS 抽取你的近况（位置、工作、居住、近期目标等），写入 user_facts 并在你所有角色之间共享；感情与健康两类不受这个开关影响，需要单独开启。',
    ),
    'user_current_location_share': FlagMeta(
      title: '位置共享（跨角色）',
      short_: '把权威位置共享给所有角色，低活跃角色不再停留在旧地点。',
      detail: '默认开：只共享「位置」这一类低敏权威值，用于现状/近况注入与主动消息；感情、健康等敏感类别不受此项影响，仍需单独开启。关=不共享位置。',
    ),
    'cross_char_fact_sync': FlagMeta(
      title: '跨角色事实对齐',
      short_: '构建上下文前/每日把各角色同槽旧值记忆标 stale（复用取代链）。',
      detail: 'B1④：默认关；开=惰性对齐 + 每日 sweep，不删可追溯；关=不对齐。',
    ),
    'cross_char_fact_projection': FlagMeta(
      title: '事实变化投影',
      short_: '用户事实变化时在记忆本留一条「跨角色同步」投影（可选）。',
      detail: 'B1④：默认关；开启后变化投影以 source=global_sync 写入并做查重守卫。',
    ),

    // 工作记忆（M3）
    'working_state_enabled': FlagMeta(
      title: '工作记忆数据积累',
      short_: '每轮对话后评估当前认知状态并滚动覆盖写入工作记忆。',
      detail: 'M3-a：已开启做数据积累；注入为 M3-b 另行灰度。关=完全跳过写入。',
    ),

    // 插件与提供商
    'provider_registry': FlagMeta(
      title: '提供商注册口',
      short_: 'LLM/TTS 经 app/providers 注册口解析实现（插件可注册 Provider）。',
      detail: 'X3：开=按配置 provider 字段选实现（内置 openai_compatible/dashscope 默认）；关=直连内置实现，与旧链路逐字节一致。',
    ),
    'agent_loop_group_chat': FlagMeta(
      title: '群聊认知循环',
      short_: '群聊中让 AI 角色参与统一的认知循环（理解/记忆/规划）。',
      detail: '开启后群聊消息也经统一 Runtime 处理，角色表现更连贯；关闭则群聊走旧链路。',
    ),
    'agent_loop_social': FlagMeta(
      title: '社交循环',
      short_: '朋友圈/评论互动等社交行为的统一认知循环。',
      detail: '开启后社交互动经统一 Runtime 并记录过程；关闭走旧链路。',
    ),
    'agent_social_light_context': FlagMeta(
      title: '社交轻量上下文',
      short_: '社交场景注入轻量上下文，降低 token 占用。',
      detail: '本机已显式开启；开启后社交回复携带精简上下文，高频角色保持全量；关闭则统一全量注入。',
    ),
    'weave_3d': FlagMeta(
      title: '织库 3D 视图',
      short_: '在「织库」中展示 3D 关系视图。',
      detail: '前端画布真在读此开关；关闭则织库不展示 3D 视图，不影响其他功能。',
    ),
    'proactive_inactive_char_skip': FlagMeta(
      title: '静默角色免打扰',
      short_: '近 24 小时无互动的角色不再主动搭话（09-08）。',
      detail: '减少打扰；开启后长时间未互动的角色跳过主动消息，其余角色照常。',
    ),
    'proactive_strategy_plugins': FlagMeta(
      title: '内容策略包外放',
      short_: '启用 X6 内容策略包（策略源让位 + 防双发）。',
      detail: '注意：flag 开不等于生效——还需有已启用的策略插件接管该类别；当前仅启用 rhythm 一个策略包。未启用插件时此开关为空。',
    ),
    'proactive_segment_guard': FlagMeta(
      title: '分块护栏',
      short_: '过滤残句/空块并做现实校验（分块护栏）。',
      detail: '主动消息生成的分块经护栏校验后再发，过滤残缺内容与明显不现实的表述。',
    ),
    'proactive_topic_guard': FlagMeta(
      title: '主题熔断',
      short_: '同主题复读/催促收口（主题熔断）。',
      detail: '防止短期内反复同一主题、过度催促；触发熔断时本轮不再重复该主题。',
    ),
    'outreach_hour_window_v1': FlagMeta(
      title: '投放时段窗',
      short_: '主动投放限制在 9–22 点之间。',
      detail: 'outreach 三闸之一。注意：flag 开 + 命中灰度角色白名单（仅 char13）+ 比例桶 才真正生效，当前仅对灰度角色生效；非白名单角色无变化。',
    ),
    'outreach_type_mix_v1': FlagMeta(
      title: '投放类型配额',
      short_: '限制各类型主动消息比例，防止某类刷屏。',
      detail: 'outreach 三闸之一。当前仅对灰度角色生效（flag 开 + 角色白名单 + 比例桶才真正生效）。',
    ),
    'outreach_session_rate_v1': FlagMeta(
      title: '会话级节流',
      short_: '单会话级别的主动消息节流（三闸之一）。',
      detail: 'outreach 三闸之一。注意：本机未显式开启此键，运行时走代码默认 False；且 flag 即使开也仅对灰度角色（char13）生效。',
    ),
    'promise_self_side_split': FlagMeta(
      title: '承诺按受益方分流',
      short_: '承诺按受益方分流，AI 自理的到点自述「我回来了」。',
      detail: '开启后自洽类承诺由 AI 自行处理并在合适时机告知，减少不必要打扰。',
    ),
    'timer_render_subject_fix': FlagMeta(
      title: '到期渲染话术修复',
      short_: '到期提醒按 (owner,type) 三套话术 + 现状锚点 + __SKIP__ 闸门。',
      detail: '让到期/计时提醒更贴合上下文、避免重复；命中 __SKIP__ 闸门的内容不渲染。',
    ),
    'life_event_no_replay': FlagMeta(
      title: '一次性生活动作不回放',
      short_: '一次性生活动作不进复习、不被高频复读。',
      detail: '已发生的一次性生活事件只记录不重复推送，避免反复提醒同一件事。',
    ),
    'life_memory_write_retry': FlagMeta(
      title: '生活写记忆加固',
      short_: '生活写记忆加固（先提交释放锁 + 退避重试，纯加固）。',
      detail: '仅增强写入可靠性，不改变内容；失败自动重试，不影响既有记忆。',
    ),
    'memory_write_receipt': FlagMeta(
      title: '写入回执',
      short_: '记忆写入回执（09-15）。',
      detail: '开启后每次记忆写入产生回执，便于确认写入成功与排查；关闭则无回执。',
    ),
    'memory_admission_gate': FlagMeta(
      title: '世界事实写入准入闸',
      short_: 'M4 world_facts 写入准入闸门（拦截/查重/矛盾裁决）。',
      detail: '开启后写入世界事实前做查重与矛盾裁决，提升事实一致性；关闭则直接写入。',
    ),
    'memory_utility_feedback': FlagMeta(
      title: '召回效用反馈',
      short_: '召回后效用反馈，回调 salience/衰减（09-16）。',
      detail: '根据用户对召回内容的实际反应回调显著性/衰减，优化后续召回；默认关，本机仍关。',
    ),
    'working_state_inject': FlagMeta(
      title: '工作记忆注入',
      short_: 'M3-b：把工作记忆注入到回复上下文。',
      detail: '注入为灰度阶段。注意：即使本键关着，灰度角色 char13 仍有约 15% 会话注入（代码常量灰度，非 runtime flag 可控）；当前仅对灰度角色生效。',
    ),
    'review_exclude_expired_plan': FlagMeta(
      title: '复习排除过期计划',
      short_: 'L1：复习选片排除过期计划/瞬时状态。',
      detail: '复习不再挑入已经过期或仅瞬时有效的计划，减少无效复习。',
    ),
    'review_reinforce_event_cap': FlagMeta(
      title: '事件强化上限',
      short_: 'L2：一次性事件强化按 tense 分流收口 + 上限。',
      detail: '防止一次性事件被过度强化；按时态分流并设上限，保持复习质量。',
    ),
    'review_reminisce_framework': FlagMeta(
      title: '回忆框架',
      short_: 'L3：复习提示改「回忆框架」+ 时态口吻 + 现状锚点。',
      detail: '复习以更自然的「回忆」口吻呈现，带时态与现状锚点，体验更连贯。',
    ),
    'review_plan_expire_stale': FlagMeta(
      title: '过期计划置 stale',
      short_: 'L4：每日维护把过期计划自动置 stale。',
      detail: '每日维护扫描计划，过期的自动标记 stale，不再被当作现行安排。',
    ),
    'review_plan_validity_extract': FlagMeta(
      title: '计划有效期提取',
      short_: 'L4：提取/写入侧给计划写 valid_to。',
      detail: '在计划提取与落库时补充有效期限 valid_to，为后续过期判定提供依据。',
    ),
    'user_fact_location': FlagMeta(
      title: '位置/城市槽',
      short_: '抽取位置/城市近况（GPS + 聊天归槽）。',
      detail: '跨角色用户事实细槽之一：位置/城市。总闸开启时本槽随之启用，也可以单独开启。',
    ),
    'user_fact_job': FlagMeta(
      title: '工作/学业槽',
      short_: '抽取工作/学业近况。',
      detail: '跨角色用户事实细槽之一：工作/学业。总闸开启时本槽随之启用，也可以单独开启。',
    ),
    'user_fact_relationship': FlagMeta(
      title: '感情状态槽（隐私）',
      short_: '抽取感情/恋爱状态近况（隐私）。',
      detail: '跨角色用户事实细槽之一：感情状态，属敏感隐私。不受总闸影响，必须单独开启；开启后会和其他事实一样在你所有角色之间共享。',
    ),
    'user_fact_living': FlagMeta(
      title: '居住状况槽',
      short_: '抽取居住状况（独居/和谁住）。',
      detail: '跨角色用户事实细槽之一：居住状况。总闸开启时本槽随之启用，也可以单独开启。',
    ),
    'user_fact_goal_state': FlagMeta(
      title: '近期目标状态槽',
      short_: '抽取近期目标/状态近况。',
      detail: '跨角色用户事实细槽之一：近期目标/状态。总闸开启时本槽随之启用，也可以单独开启。',
    ),
    'user_fact_health': FlagMeta(
      title: '健康槽（隐私）',
      short_: '抽取健康近况（隐私）。',
      detail: '跨角色用户事实细槽之一：健康，属敏感隐私。不受总闸影响，必须单独开启；开启后会和其他事实一样在你所有角色之间共享。',
    ),
    'channel_binding_v2': FlagMeta(
      title: '渠道绑定 v2',
      short_: '渠道绑定 v2（MCP/抖音/微信按主账号独立绑定）。',
      detail: '文档曾称已转正，但代码硬编码默认仍是 False，当前靠本机 runtime_flags 覆盖为 ON；换机/重建库若无此覆盖则回退为关。',
    ),
    'domain_event_log_enabled': FlagMeta(
      title: '领域事件流水',
      short_: '开启领域事件（domain event）流水写入。',
      detail: '把系统关键事件写入事件流水，便于审计与排查；关闭则不再记录。',
    ),
    'domain_event_retention_days': FlagMeta(
      title: '事件保留天数',
      short_: '领域事件保留天数，0=永久（数字型）。',
      detail: '为数字型键，热切通道只支持布尔，无法通过开关页调整（不可热切，需改配置/代码）；0 表示永久保留。',
    ),
    'group_cognition_v2': FlagMeta(
      title: '群聊认知升级',
      short_: '#72 群聊认知升级（P1/P2 已合，P3/P4 待拆）。',
      detail: '升级群聊认知处理；还需群级 chat_groups.cognition_enabled 二次门控才对具体群生效。',
    ),
    'group_memory_compact': FlagMeta(
      title: '群记忆日终合并',
      short_: '#72 P5：>7 天群记忆日终合并为 1 条摘要、旧行软删。',
      detail: '降低长群记忆存储与注入成本；开启后老旧群记忆合并为摘要，原始行软删除。',
    ),
    'agent_trace_scheduler_only_executed': FlagMeta(
      title: '计划仅记已执行',
      short_: 'R1：未触发的主动任务不再写 task_logs（止血写放大）。',
      detail: '仅记录真实触发的计划，减少无谓日志；关闭则旧行为全量写日志。',
    ),
    'agent_trace_scheduler_mark_exec_error': FlagMeta(
      title: '执行失败记 error',
      short_: 'R1：真执行失败记 status=error（而非 blocked）。',
      detail: '更准确标记执行结果，便于排查；关闭则失败可能被记为 blocked。',
    ),
    'mcp_stream_declarations': FlagMeta(
      title: '流式 MCP 声明',
      short_: 'R3：流式会话也注入 MCP 工具声明（依赖 #59 流尾 tool_result）。',
      detail: '让流式回复也能带 MCP 工具声明；关闭则流式路径不注入。',
    ),
    'agent_tool_exec_trace': FlagMeta(
      title: '工具执行落日志',
      short_: 'R5：插件/内置工具每次执行落 task_logs（MCP 工具不落，避免双记）。',
      detail: '记录工具真实执行轨迹；MCP 工具不重复记录以防双记；关闭则只记录部分。',
    ),
  };
}
