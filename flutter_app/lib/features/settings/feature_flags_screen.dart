import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../providers/settings_provider.dart';
import '../../services/feature_flag_service.dart';
import '../../widgets/ios_card_group.dart';
import 'feature_flag_catalog.dart';

/// 服务器功能管理页（2026-08-18）：主账号可热切换运行时 Feature Flag（无需重启）
class FeatureFlagsScreen extends StatefulWidget {
  const FeatureFlagsScreen({super.key, this.showAppBar = true});

  /// 是否渲染独立 AppBar/Scaffold；作为「权限管理」合并页 tab body 时传 false。
  final bool showAppBar;

  @override
  State<FeatureFlagsScreen> createState() => _FeatureFlagsScreenState();
}

class _FeatureFlagsScreenState extends State<FeatureFlagsScreen> {
  bool _loading = true;
  bool _isAdmin = false;
  String _error = '';
  final Map<String, bool> _flags = {};
  final Map<String, String> _sources = {};

  // 用户语义白名单（2026-09-17 开关瘦身批次）：直接可见；其余内部/运维开关收进折叠区
  static const List<String> _visibleKeys = [
    'weave_3d',
    'agent_social_light_context',
    'agent_loop_group_chat',
    'agent_loop_social',
    'global_user_facts',
    'user_fact_location',
    'user_current_location_share',
    'user_fact_relationship',
    'user_fact_health',
    'proactive_outreach_v2',
  ];

  @override
  void initState() {
    super.initState();
    _isAdmin = context.read<SettingsProvider>().isAdmin;
    _load();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = ''; });
    try {
      // 走 FeatureFlagService：既同步服务器值，也让画布等监听方即时生效
      await FeatureFlagService.instance.refresh();
      _flags.clear();
      _sources.clear();
      for (final k in FeatureFlagService.instance.keys) {
        _flags[k] = FeatureFlagService.instance.isEnabled(k);
        _sources[k] = FeatureFlagService.instance.sourceOf(k);
      }
      if (mounted) setState(() { _loading = false; });
    } catch (e) {
      if (mounted) setState(() { _error = e.toString(); _loading = false; });
    }
  }

  Future<void> _toggle(String key, bool value) async {
    final prev = _flags[key];
    setState(() => _flags[key] = value);
    final l10n = AppLocalizations.of(context)!;
    final ok = await FeatureFlagService.instance.setFlag(key, value);
    if (!mounted) return;
    if (ok) {
      setState(() => _sources[key] = 'db');
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.flagSaved)));
    } else {
      setState(() => _flags[key] = prev ?? false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.flagError)));
    }
  }

  /// 各高级开关的 l10n 文案（界面优先取此；catalog 中文仅作兜底）。
  /// 不依赖动态 key 查找（Flutter l10n 不支持），逐键静态映射。
  static final Map<String, FlagMeta Function(AppLocalizations)> _flagMetaL10n = {
    'agent_loop_chat': (l) => FlagMeta(title: l.flagAgentLoopChatTitle, short_: l.flagAgentLoopChatHint, detail: l.flagAgentLoopChatDetail),
    'agent_loop_scheduler': (l) => FlagMeta(title: l.flagAgentLoopSchedulerTitle, short_: l.flagAgentLoopSchedulerHint, detail: l.flagAgentLoopSchedulerDetail),
    'agent_context_trim': (l) => FlagMeta(title: l.flagAgentContextTrimTitle, short_: l.flagAgentContextTrimHint, detail: l.flagAgentContextTrimDetail),
    'current_facts_active_only': (l) => FlagMeta(title: l.flagCurrentFactsActiveOnlyTitle, short_: l.flagCurrentFactsActiveOnlyHint, detail: l.flagCurrentFactsActiveOnlyDetail),
    'agent_trace_group': (l) => FlagMeta(title: l.flagAgentTraceGroupTitle, short_: l.flagAgentTraceGroupHint, detail: l.flagAgentTraceGroupDetail),
    'proactive_naturalness_score': (l) => FlagMeta(title: l.flagProactiveNaturalnessScoreTitle, short_: l.flagProactiveNaturalnessScoreHint, detail: l.flagProactiveNaturalnessScoreDetail),
    'proactive_user_rhythm': (l) => FlagMeta(title: l.flagProactiveUserRhythmTitle, short_: l.flagProactiveUserRhythmHint, detail: l.flagProactiveUserRhythmDetail),
    'group_chat_games': (l) => FlagMeta(title: l.flagGroupChatGamesTitle, short_: l.flagGroupChatGamesHint, detail: l.flagGroupChatGamesDetail),
    'life_loop_enabled': (l) => FlagMeta(title: l.flagLifeLoopEnabledTitle, short_: l.flagLifeLoopEnabledHint, detail: l.flagLifeLoopEnabledDetail),
    'life_loop_llm': (l) => FlagMeta(title: l.flagLifeLoopLlmTitle, short_: l.flagLifeLoopLlmHint, detail: l.flagLifeLoopLlmDetail),
    'life_chat_driven_enabled': (l) => FlagMeta(title: l.flagLifeChatDrivenEnabledTitle, short_: l.flagLifeChatDrivenEnabledHint, detail: l.flagLifeChatDrivenEnabledDetail),
    'life_home_worldmap_enabled': (l) => FlagMeta(title: l.flagLifeHomeWorldmapEnabledTitle, short_: l.flagLifeHomeWorldmapEnabledHint, detail: l.flagLifeHomeWorldmapEnabledDetail),
    'reply_delay_enabled': (l) => FlagMeta(title: l.flagReplyDelayEnabledTitle, short_: l.flagReplyDelayEnabledHint, detail: l.flagReplyDelayEnabledDetail),
    'spring_emotion_enabled': (l) => FlagMeta(title: l.flagSpringEmotionEnabledTitle, short_: l.flagSpringEmotionEnabledHint, detail: l.flagSpringEmotionEnabledDetail),
    'life_share_enabled': (l) => FlagMeta(title: l.flagLifeShareEnabledTitle, short_: l.flagLifeShareEnabledHint, detail: l.flagLifeShareEnabledDetail),
    'preoccupation_enabled': (l) => FlagMeta(title: l.flagPreoccupationEnabledTitle, short_: l.flagPreoccupationEnabledHint, detail: l.flagPreoccupationEnabledDetail),
    'proactive_outreach_v2': (l) => FlagMeta(title: l.flagProactiveOutreachV2Title, short_: l.flagProactiveOutreachV2Hint, detail: l.flagProactiveOutreachV2Detail),
    'memory_temporal_recall': (l) => FlagMeta(title: l.flagMemoryTemporalRecallTitle, short_: l.flagMemoryTemporalRecallHint, detail: l.flagMemoryTemporalRecallDetail),
    'memory_recall_second_hop': (l) => FlagMeta(title: l.flagMemoryRecallSecondHopTitle, short_: l.flagMemoryRecallSecondHopHint, detail: l.flagMemoryRecallSecondHopDetail),
    'memory_story_assemble': (l) => FlagMeta(title: l.flagMemoryStoryAssembleTitle, short_: l.flagMemoryStoryAssembleHint, detail: l.flagMemoryStoryAssembleDetail),
    'memory_peak_cutoff': (l) => FlagMeta(title: l.flagMemoryPeakCutoffTitle, short_: l.flagMemoryPeakCutoffHint, detail: l.flagMemoryPeakCutoffDetail),
    'memory_chain_builder': (l) => FlagMeta(title: l.flagMemoryChainBuilderTitle, short_: l.flagMemoryChainBuilderHint, detail: l.flagMemoryChainBuilderDetail),
    'memory_chain_expand': (l) => FlagMeta(title: l.flagMemoryChainExpandTitle, short_: l.flagMemoryChainExpandHint, detail: l.flagMemoryChainExpandDetail),
    'memory_tiered_decay': (l) => FlagMeta(title: l.flagMemoryTieredDecayTitle, short_: l.flagMemoryTieredDecayHint, detail: l.flagMemoryTieredDecayDetail),
    'memory_tiered_inject': (l) => FlagMeta(title: l.flagMemoryTieredInjectTitle, short_: l.flagMemoryTieredInjectHint, detail: l.flagMemoryTieredInjectDetail),
    'memory_trace_debug': (l) => FlagMeta(title: l.flagMemoryTraceDebugTitle, short_: l.flagMemoryTraceDebugHint, detail: l.flagMemoryTraceDebugDetail),
    'memory_supersede': (l) => FlagMeta(title: l.flagMemorySupersedeTitle, short_: l.flagMemorySupersedeHint, detail: l.flagMemorySupersedeDetail),
    'marker_recovery': (l) => FlagMeta(title: l.flagMarkerRecoveryTitle, short_: l.flagMarkerRecoveryHint, detail: l.flagMarkerRecoveryDetail),
    'review_daily_plus': (l) => FlagMeta(title: l.flagReviewDailyPlusTitle, short_: l.flagReviewDailyPlusHint, detail: l.flagReviewDailyPlusDetail),
    'curated_knowledge': (l) => FlagMeta(title: l.flagCuratedKnowledgeTitle, short_: l.flagCuratedKnowledgeHint, detail: l.flagCuratedKnowledgeDetail),
    'prospective_intent_enabled': (l) => FlagMeta(title: l.flagProspectiveIntentEnabledTitle, short_: l.flagProspectiveIntentEnabledHint, detail: l.flagProspectiveIntentEnabledDetail),
    'prospective_intent_trigger': (l) => FlagMeta(title: l.flagProspectiveIntentTriggerTitle, short_: l.flagProspectiveIntentTriggerHint, detail: l.flagProspectiveIntentTriggerDetail),
    'global_user_facts': (l) => FlagMeta(title: l.flagGlobalUserFactsTitle, short_: l.flagGlobalUserFactsHint, detail: l.flagGlobalUserFactsDetail),
    'user_current_location_share': (l) => FlagMeta(title: l.flagUserCurrentLocationShareTitle, short_: l.flagUserCurrentLocationShareHint, detail: l.flagUserCurrentLocationShareDetail),
    'cross_char_fact_sync': (l) => FlagMeta(title: l.flagCrossCharFactSyncTitle, short_: l.flagCrossCharFactSyncHint, detail: l.flagCrossCharFactSyncDetail),
    'cross_char_fact_projection': (l) => FlagMeta(title: l.flagCrossCharFactProjectionTitle, short_: l.flagCrossCharFactProjectionHint, detail: l.flagCrossCharFactProjectionDetail),
    'working_state_enabled': (l) => FlagMeta(title: l.flagWorkingStateEnabledTitle, short_: l.flagWorkingStateEnabledHint, detail: l.flagWorkingStateEnabledDetail),
    'provider_registry': (l) => FlagMeta(title: l.flagProviderRegistryTitle, short_: l.flagProviderRegistryHint, detail: l.flagProviderRegistryDetail),
    'agent_loop_group_chat': (l) => FlagMeta(title: l.flagAgentLoopGroupChatTitle, short_: l.flagAgentLoopGroupChatHint, detail: l.flagAgentLoopGroupChatDetail),
    'agent_loop_social': (l) => FlagMeta(title: l.flagAgentLoopSocialTitle, short_: l.flagAgentLoopSocialHint, detail: l.flagAgentLoopSocialDetail),
    'agent_social_light_context': (l) => FlagMeta(title: l.flagAgentSocialLightContextTitle, short_: l.flagAgentSocialLightContextHint, detail: l.flagAgentSocialLightContextDetail),
    'weave_3d': (l) => FlagMeta(title: l.flagWeave3DTitle, short_: l.flagWeave3DHint, detail: l.flagWeave3DDetail),
    'proactive_inactive_char_skip': (l) => FlagMeta(title: l.flagProactiveInactiveCharSkipTitle, short_: l.flagProactiveInactiveCharSkipHint, detail: l.flagProactiveInactiveCharSkipDetail),
    'proactive_strategy_plugins': (l) => FlagMeta(title: l.flagProactiveStrategyPluginsTitle, short_: l.flagProactiveStrategyPluginsHint, detail: l.flagProactiveStrategyPluginsDetail),
    'proactive_segment_guard': (l) => FlagMeta(title: l.flagProactiveSegmentGuardTitle, short_: l.flagProactiveSegmentGuardHint, detail: l.flagProactiveSegmentGuardDetail),
    'proactive_topic_guard': (l) => FlagMeta(title: l.flagProactiveTopicGuardTitle, short_: l.flagProactiveTopicGuardHint, detail: l.flagProactiveTopicGuardDetail),
    'outreach_hour_window_v1': (l) => FlagMeta(title: l.flagOutreachHourWindowV1Title, short_: l.flagOutreachHourWindowV1Hint, detail: l.flagOutreachHourWindowV1Detail),
    'outreach_type_mix_v1': (l) => FlagMeta(title: l.flagOutreachTypeMixV1Title, short_: l.flagOutreachTypeMixV1Hint, detail: l.flagOutreachTypeMixV1Detail),
    'outreach_session_rate_v1': (l) => FlagMeta(title: l.flagOutreachSessionRateV1Title, short_: l.flagOutreachSessionRateV1Hint, detail: l.flagOutreachSessionRateV1Detail),
    'promise_self_side_split': (l) => FlagMeta(title: l.flagPromiseSelfSideSplitTitle, short_: l.flagPromiseSelfSideSplitHint, detail: l.flagPromiseSelfSideSplitDetail),
    'timer_render_subject_fix': (l) => FlagMeta(title: l.flagTimerRenderSubjectFixTitle, short_: l.flagTimerRenderSubjectFixHint, detail: l.flagTimerRenderSubjectFixDetail),
    'life_event_no_replay': (l) => FlagMeta(title: l.flagLifeEventNoReplayTitle, short_: l.flagLifeEventNoReplayHint, detail: l.flagLifeEventNoReplayDetail),
    'life_memory_write_retry': (l) => FlagMeta(title: l.flagLifeMemoryWriteRetryTitle, short_: l.flagLifeMemoryWriteRetryHint, detail: l.flagLifeMemoryWriteRetryDetail),
    'memory_write_receipt': (l) => FlagMeta(title: l.flagMemoryWriteReceiptTitle, short_: l.flagMemoryWriteReceiptHint, detail: l.flagMemoryWriteReceiptDetail),
    'memory_admission_gate': (l) => FlagMeta(title: l.flagMemoryAdmissionGateTitle, short_: l.flagMemoryAdmissionGateHint, detail: l.flagMemoryAdmissionGateDetail),
    'memory_utility_feedback': (l) => FlagMeta(title: l.flagMemoryUtilityFeedbackTitle, short_: l.flagMemoryUtilityFeedbackHint, detail: l.flagMemoryUtilityFeedbackDetail),
    'working_state_inject': (l) => FlagMeta(title: l.flagWorkingStateInjectTitle, short_: l.flagWorkingStateInjectHint, detail: l.flagWorkingStateInjectDetail),
    'review_exclude_expired_plan': (l) => FlagMeta(title: l.flagReviewExcludeExpiredPlanTitle, short_: l.flagReviewExcludeExpiredPlanHint, detail: l.flagReviewExcludeExpiredPlanDetail),
    'review_reinforce_event_cap': (l) => FlagMeta(title: l.flagReviewReinforceEventCapTitle, short_: l.flagReviewReinforceEventCapHint, detail: l.flagReviewReinforceEventCapDetail),
    'review_reminisce_framework': (l) => FlagMeta(title: l.flagReviewReminisceFrameworkTitle, short_: l.flagReviewReminisceFrameworkHint, detail: l.flagReviewReminisceFrameworkDetail),
    'review_plan_expire_stale': (l) => FlagMeta(title: l.flagReviewPlanExpireStaleTitle, short_: l.flagReviewPlanExpireStaleHint, detail: l.flagReviewPlanExpireStaleDetail),
    'review_plan_validity_extract': (l) => FlagMeta(title: l.flagReviewPlanValidityExtractTitle, short_: l.flagReviewPlanValidityExtractHint, detail: l.flagReviewPlanValidityExtractDetail),
    'user_fact_location': (l) => FlagMeta(title: l.flagUserFactLocationTitle, short_: l.flagUserFactLocationHint, detail: l.flagUserFactLocationDetail),
    'user_fact_job': (l) => FlagMeta(title: l.flagUserFactJobTitle, short_: l.flagUserFactJobHint, detail: l.flagUserFactJobDetail),
    'user_fact_relationship': (l) => FlagMeta(title: l.flagUserFactRelationshipTitle, short_: l.flagUserFactRelationshipHint, detail: l.flagUserFactRelationshipDetail),
    'user_fact_living': (l) => FlagMeta(title: l.flagUserFactLivingTitle, short_: l.flagUserFactLivingHint, detail: l.flagUserFactLivingDetail),
    'user_fact_goal_state': (l) => FlagMeta(title: l.flagUserFactGoalStateTitle, short_: l.flagUserFactGoalStateHint, detail: l.flagUserFactGoalStateDetail),
    'user_fact_health': (l) => FlagMeta(title: l.flagUserFactHealthTitle, short_: l.flagUserFactHealthHint, detail: l.flagUserFactHealthDetail),
    'channel_binding_v2': (l) => FlagMeta(title: l.flagChannelBindingV2Title, short_: l.flagChannelBindingV2Hint, detail: l.flagChannelBindingV2Detail),
    'domain_event_log_enabled': (l) => FlagMeta(title: l.flagDomainEventLogEnabledTitle, short_: l.flagDomainEventLogEnabledHint, detail: l.flagDomainEventLogEnabledDetail),
    'domain_event_retention_days': (l) => FlagMeta(title: l.flagDomainEventRetentionDaysTitle, short_: l.flagDomainEventRetentionDaysHint, detail: l.flagDomainEventRetentionDaysDetail),
    'group_cognition_v2': (l) => FlagMeta(title: l.flagGroupCognitionV2Title, short_: l.flagGroupCognitionV2Hint, detail: l.flagGroupCognitionV2Detail),
    'group_memory_compact': (l) => FlagMeta(title: l.flagGroupMemoryCompactTitle, short_: l.flagGroupMemoryCompactHint, detail: l.flagGroupMemoryCompactDetail),
    'agent_trace_scheduler_only_executed': (l) => FlagMeta(title: l.flagAgentTraceSchedulerOnlyExecutedTitle, short_: l.flagAgentTraceSchedulerOnlyExecutedHint, detail: l.flagAgentTraceSchedulerOnlyExecutedDetail),
    'agent_trace_scheduler_mark_exec_error': (l) => FlagMeta(title: l.flagAgentTraceSchedulerMarkExecErrorTitle, short_: l.flagAgentTraceSchedulerMarkExecErrorHint, detail: l.flagAgentTraceSchedulerMarkExecErrorDetail),
    'mcp_stream_declarations': (l) => FlagMeta(title: l.flagMcpStreamDeclarationsTitle, short_: l.flagMcpStreamDeclarationsHint, detail: l.flagMcpStreamDeclarationsDetail),
    'agent_tool_exec_trace': (l) => FlagMeta(title: l.flagAgentToolExecTraceTitle, short_: l.flagAgentToolExecTraceHint, detail: l.flagAgentToolExecTraceDetail),
  };

  /// 开关文案（A4，2026-09-20）：后端下发元数据 → 既有 l10n → catalog 中文兜底。
  /// 后端只下发一句话说明，既有 l10n 的「详情」更长，故保留下来，避免改后端下发后信息变少。
  FlagMeta _localizedMeta(String key, AppLocalizations l10n) {
    final backend = FeatureFlagService.instance.metaOf(key);
    if (backend != null) {
      final local = _flagMetaL10n[key];
      return FlagMeta(
        title: backend.title,
        short_: backend.desc,
        detail: local != null ? local(l10n).detail : '',
      );
    }
    final f = _flagMetaL10n[key];
    if (f != null) return f(l10n);
    return FeatureFlagCatalog.metaOf(key);
  }

  /// 副标题：一句话说明 + 一行作用范围提示（A5：用户级键显示账号覆盖状态、服务器级显示影响范围；文案走 l10n）。
  String _subtitle(String key, AppLocalizations l10n) {
    final base = _localizedMeta(key, l10n).short_;
    final svc = FeatureFlagService.instance;
    if (!svc.isUserScoped(key)) return '$base\n${l10n.flagScopeServerHint}';
    final u = svc.userEnabledOf(key);
    final scope = u == null
        ? l10n.flagUserOverrideNone
        : (u ? l10n.flagUserOverrideEnabled : l10n.flagUserOverrideDisabled);
    return '$base\n$scope';
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final body = _body(l10n);
    if (!widget.showAppBar) return body;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.featureFlagsTitle)),
      body: body,
    );
  }

  Widget _body(AppLocalizations l10n) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error.isNotEmpty) {
      return Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(_error)));
    }
    if (!_isAdmin) return _nonAdminBody(l10n);
    return _adminBody(l10n);
  }

  Widget _nonAdminBody(AppLocalizations l10n) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.lock_outline, size: 48, color: Colors.grey),
            const SizedBox(height: 12),
            Text(l10n.featureFlagsAdminOnly, textAlign: TextAlign.center),
          ],
        ),
      ),
    );
  }

  Widget _adminBody(AppLocalizations l10n) {
    // 常用开关（A4）：优先取后端目录里 visible=true 的键；后端没给（离线/老后端）回落既有白名单
    final backendVisible =
        FeatureFlagService.instance.visibleKeys.where(_flags.containsKey).toList();
    final visible = backendVisible.isNotEmpty
        ? backendVisible
        : _visibleKeys.where((k) => _flags.containsKey(k)).toList();

    // 其余内部/运维开关：收进一个默认折叠的区块（标题复用既有 l10n.flagGroupOther，不新增硬编码文案）
    // 顺序按后端目录的分组/组内序；缺后端元数据的键由 groupEntriesFromBackend 内部回落硬编码分组
    final advancedKeys = FeatureFlagCatalog
        .groupEntriesFromBackend(
          _flags.keys.toSet().difference(visible.toSet()),
          FeatureFlagService.instance,
        )
        .expand((g) => g.keys)
        .toList();
    final advancedTiles = advancedKeys
        .map((k) => _FlagTileData(
              rawKey: k,
              meta: _localizedMeta(k, l10n),
              value: _flags[k] ?? false,
              source: _sources[k] ?? 'default',
              type: FeatureFlagService.instance.flagType(k),
              numValue: FeatureFlagService.instance.flagValue(k),
              serverLevel: !FeatureFlagService.instance.isUserScoped(k),
              // A5：用户级键带上本账号覆盖值，供行内展示覆盖状态（服务器级键恒为 null，不展示）
              userEnabled: FeatureFlagService.instance.userEnabledOf(k),
            ))
        .toList();

    return ListView(
      padding: const EdgeInsets.only(top: 8, bottom: 24),
      children: [
        // 常用/用户语义开关
        IosCardGroup(
          title: l10n.featureFlagsHint,
          children: [
            for (final k in visible)
              SwitchListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 16),
                title: Text(_localizedMeta(k, l10n).title),
                subtitle: Text(_subtitle(k, l10n),
                    style: const TextStyle(fontSize: 11)),
                value: _flags[k] ?? false,
                onChanged: (v) => _toggle(k, v),
              ),
          ],
        ),
        // 内部/运维开关：默认折叠，不点开不渲染、不占屏
        if (advancedTiles.isNotEmpty)
          _CollapsibleFlagGroup(
            title: l10n.flagGroupOther,
            initiallyOpen: false,
            tiles: advancedTiles,
            onChanged: _toggle,
            detailLabel: l10n.flagDetail,
            collapseLabel: l10n.flagCollapse,
          ),
      ],
    );
  }
}

/// 折叠组：模块标题 + 已开数量 n/m + 旋转箭头，展开后是一组中文说明开关。
class _CollapsibleFlagGroup extends StatefulWidget {
  final String title;
  final bool initiallyOpen;
  final List<_FlagTileData> tiles;
  final Future<void> Function(String key, bool value) onChanged;
  final String detailLabel;
  final String collapseLabel;

  const _CollapsibleFlagGroup({
    required this.title,
    required this.initiallyOpen,
    required this.tiles,
    required this.onChanged,
    required this.detailLabel,
    required this.collapseLabel,
  });

  @override
  State<_CollapsibleFlagGroup> createState() => _CollapsibleFlagGroupState();
}

class _CollapsibleFlagGroupState extends State<_CollapsibleFlagGroup> {
  late bool _open = widget.initiallyOpen;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final onCount = widget.tiles.where((t) => t.value).length;
    final total = widget.tiles.length;

    return Padding(
      padding: const EdgeInsets.only(left: 12, right: 12, bottom: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 组标题行（与 IosCardGroup 小标题同样式，可点按折叠）
          InkWell(
            borderRadius: BorderRadius.circular(8),
            onTap: () => setState(() => _open = !_open),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 8, 6),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      widget.title,
                      style: const TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                        color: IosCardColors.subtitle,
                      ),
                    ),
                  ),
                  // 已开数量（纯数字，语言无关）
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                    decoration: BoxDecoration(
                      color: scheme.surfaceContainerHighest,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      '$onCount/$total',
                      style: TextStyle(fontSize: 10, color: scheme.onSurfaceVariant),
                    ),
                  ),
                  const SizedBox(width: 2),
                  AnimatedRotation(
                    turns: _open ? 0.5 : 0,
                    duration: const Duration(milliseconds: 200),
                    child: Icon(Icons.expand_more,
                        size: 18, color: IosCardColors.chevron),
                  ),
                ],
              ),
            ),
          ),
          AnimatedSize(
            duration: const Duration(milliseconds: 200),
            curve: Curves.easeOut,
            alignment: Alignment.topCenter,
            child: _open
                ? Container(
                    decoration: BoxDecoration(
                      color: scheme.surface,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Material(
                      type: MaterialType.transparency,
                      child: Column(
                        children: [
                          for (var i = 0; i < widget.tiles.length; i++) ...[
                            if (i > 0) const IosCardDivider(indent: 16),
                            _FlagTile(
                              data: widget.tiles[i],
                              onChanged: (v) => widget.onChanged(widget.tiles[i].rawKey, v),
                              detailLabel: widget.detailLabel,
                              collapseLabel: widget.collapseLabel,
                            ),
                          ],
                        ],
                      ),
                    ),
                  )
                : const SizedBox(width: double.infinity),
          ),
        ],
      ),
    );
  }
}

/// 传给开关行的数据
class _FlagTileData {
  final String rawKey;
  final FlagMeta meta;
  final bool value;
  final String source;
  final String? type;
  final num? numValue;
  /// 服务器级（非按账号生效）：副标题下追加一行作用范围提示（A4）。
  final bool serverLevel;
  /// 用户级覆盖值（仅 user-scoped 旗标有意义）：true=用户开启、false=用户关闭、null=未覆盖（回落全局）。
  final bool? userEnabled;
  const _FlagTileData({
    required this.rawKey,
    required this.meta,
    required this.value,
    required this.source,
    this.type,
    this.numValue,
    this.serverLevel = false,
    this.userEnabled,
  });
}

/// 单个高级开关：中文名 + 两行短说明 + 「详情」展开 + 右侧开关。
class _FlagTile extends StatefulWidget {
  final _FlagTileData data;
  final ValueChanged<bool> onChanged;
  final String detailLabel;
  final String collapseLabel;

  const _FlagTile({
    required this.data,
    required this.onChanged,
    required this.detailLabel,
    required this.collapseLabel,
  });

  @override
  State<_FlagTile> createState() => _FlagTileState();
}

class _FlagTileState extends State<_FlagTile> {
  bool _openDetail = false;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    final m = widget.data.meta;
    final hasDetail = m.detail.isNotEmpty;
    final expanded = _openDetail && hasDetail;
    // 数字型（type != bool 或 value 为数值）：只读展示当前值，不渲染 Switch；
    // 老后端未下发 type/value 时退化为原 Switch 行为（isNumeric=false）。
    final isNumeric =
        (widget.data.type != null && widget.data.type != 'bool') ||
        widget.data.numValue != null;

    // 用户级覆盖状态文案
    String? userOverrideText;
    if (widget.data.userEnabled != null) {
      userOverrideText = widget.data.userEnabled!
          ? l10n.flagUserOverrideEnabled
          : l10n.flagUserOverrideDisabled;
    } else if (widget.data.userEnabled == null && widget.data.serverLevel == false) {
      // user-scoped 但无覆盖行
      userOverrideText = l10n.flagUserOverrideNone;
    }

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  m.title,
                  style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 3),
                Text(
                  expanded ? '${m.short_}\n\n${m.detail}' : m.short_,
                  maxLines: expanded ? null : 2,
                  overflow: expanded ? null : TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant, height: 1.35),
                ),
                // 服务器级：改动会影响本服务器上的所有账号（文案走 l10n，不硬编码）
                if (widget.data.serverLevel)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      l10n.flagScopeServerHint,
                      style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant),
                    ),
                  ),
                // 用户级覆盖状态
                if (userOverrideText != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      userOverrideText,
                      style: TextStyle(fontSize: 11, color: scheme.primary),
                    ),
                  ),
                if (isNumeric)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      l10n.flagNumericReadOnly,
                      style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant),
                    ),
                  ),
                if (hasDetail)
                  Align(
                    alignment: Alignment.centerRight,
                    child: TextButton(
                      style: TextButton.styleFrom(
                        minimumSize: const Size(0, 28),
                        padding: const EdgeInsets.symmetric(horizontal: 6),
                        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      ),
                      onPressed: () => setState(() => _openDetail = !_openDetail),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(
                            expanded ? widget.collapseLabel : widget.detailLabel,
                            style: const TextStyle(fontSize: 12),
                          ),
                          Icon(expanded ? Icons.expand_less : Icons.expand_more, size: 15),
                        ],
                      ),
                    ),
                  ),
                // 展开时露出原始键与来源，方便和后端对照排错
                if (expanded)
                  Text(
                    '${widget.data.rawKey} · ${widget.data.source}',
                    style: TextStyle(
                      fontSize: 10,
                      fontFamily: 'monospace',
                      color: scheme.onSurfaceVariant.withValues(alpha: 0.7),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          if (isNumeric)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              decoration: BoxDecoration(
                color: scheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                widget.data.numValue != null
                    ? l10n.flagNumericValue(widget.data.numValue!)
                    : (widget.data.type ?? ''),
                style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
              ),
            )
          else
            Switch.adaptive(
              value: widget.data.value,
              onChanged: widget.onChanged,
            ),
        ],
      ),
    );
  }
}
