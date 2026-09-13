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

  static const List<String> _visibleKeys = [
    'agent_social_light_context',
    'agent_loop_group_chat',
    'agent_loop_social',
    'weave_3d',
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

  String _flagTitle(String key, AppLocalizations l10n) {
    switch (key) {
      case 'agent_social_light_context': return l10n.flagLightReply;
      case 'agent_loop_group_chat': return l10n.flagGroupRuntime;
      case 'agent_loop_social': return l10n.flagSocialRuntime;
      case 'weave_3d': return l10n.flagWeave3D;
      default: return key;
    }
  }

  String _flagHint(String key, AppLocalizations l10n) {
    switch (key) {
      case 'agent_social_light_context': return l10n.flagLightReplyHint;
      case 'agent_loop_group_chat': return l10n.flagGroupRuntimeHint;
      case 'agent_loop_social': return l10n.flagSocialRuntimeHint;
      case 'weave_3d': return l10n.flagWeave3DHint;
      default: return l10n.flagAdvancedHint;
    }
  }

  /// 各高级开关的 l10n 文案（界面优先取此；catalog 中文仅作兜底）。
  /// 不依赖动态 key 查找（Flutter l10n 不支持），逐键静态映射。
  static final Map<String, FlagMeta Function(AppLocalizations)> _flagMetaL10n = {
    'agent_loop_chat': (l) => FlagMeta(title: l.flagAgentLoopChatTitle, short_: l.flagAgentLoopChatHint, detail: l.flagAgentLoopChatDetail),
    'agent_loop_search': (l) => FlagMeta(title: l.flagAgentLoopSearchTitle, short_: l.flagAgentLoopSearchHint, detail: l.flagAgentLoopSearchDetail),
    'agent_loop_scheduler': (l) => FlagMeta(title: l.flagAgentLoopSchedulerTitle, short_: l.flagAgentLoopSchedulerHint, detail: l.flagAgentLoopSchedulerDetail),
    'agent_tool_events': (l) => FlagMeta(title: l.flagAgentToolEventsTitle, short_: l.flagAgentToolEventsHint, detail: l.flagAgentToolEventsDetail),
    'agent_context_trim': (l) => FlagMeta(title: l.flagAgentContextTrimTitle, short_: l.flagAgentContextTrimHint, detail: l.flagAgentContextTrimDetail),
    'agent_trace_group': (l) => FlagMeta(title: l.flagAgentTraceGroupTitle, short_: l.flagAgentTraceGroupHint, detail: l.flagAgentTraceGroupDetail),
    'agent_daily_reflection': (l) => FlagMeta(title: l.flagAgentDailyReflectionTitle, short_: l.flagAgentDailyReflectionHint, detail: l.flagAgentDailyReflectionDetail),
    'agent_reflection_inject': (l) => FlagMeta(title: l.flagAgentReflectionInjectTitle, short_: l.flagAgentReflectionInjectHint, detail: l.flagAgentReflectionInjectDetail),
    'agent_daily_memory_maintenance': (l) => FlagMeta(title: l.flagAgentDailyMemoryMaintenanceTitle, short_: l.flagAgentDailyMemoryMaintenanceHint, detail: l.flagAgentDailyMemoryMaintenanceDetail),
    'proactive_naturalness_score': (l) => FlagMeta(title: l.flagProactiveNaturalnessScoreTitle, short_: l.flagProactiveNaturalnessScoreHint, detail: l.flagProactiveNaturalnessScoreDetail),
    'proactive_user_rhythm': (l) => FlagMeta(title: l.flagProactiveUserRhythmTitle, short_: l.flagProactiveUserRhythmHint, detail: l.flagProactiveUserRhythmDetail),
    'group_chat_games': (l) => FlagMeta(title: l.flagGroupChatGamesTitle, short_: l.flagGroupChatGamesHint, detail: l.flagGroupChatGamesDetail),
    'game_undercover': (l) => FlagMeta(title: l.flagGameUndercoverTitle, short_: l.flagGameUndercoverHint, detail: l.flagGameUndercoverDetail),
    'game_truth_or_dare': (l) => FlagMeta(title: l.flagGameTruthOrDareTitle, short_: l.flagGameTruthOrDareHint, detail: l.flagGameTruthOrDareDetail),
    'game_twenty_q': (l) => FlagMeta(title: l.flagGameTwentyQTitle, short_: l.flagGameTwentyQHint, detail: l.flagGameTwentyQDetail),
    'game_werewolf': (l) => FlagMeta(title: l.flagGameWerewolfTitle, short_: l.flagGameWerewolfHint, detail: l.flagGameWerewolfDetail),
    'game_liars_bar': (l) => FlagMeta(title: l.flagGameLiarsBarTitle, short_: l.flagGameLiarsBarHint, detail: l.flagGameLiarsBarDetail),
    'game_turtle_soup': (l) => FlagMeta(title: l.flagGameTurtleSoupTitle, short_: l.flagGameTurtleSoupHint, detail: l.flagGameTurtleSoupDetail),
    'game_memory_bridge': (l) => FlagMeta(title: l.flagGameMemoryBridgeTitle, short_: l.flagGameMemoryBridgeHint, detail: l.flagGameMemoryBridgeDetail),
    'game_ai_autoplay': (l) => FlagMeta(title: l.flagGameAiAutoplayTitle, short_: l.flagGameAiAutoplayHint, detail: l.flagGameAiAutoplayDetail),
    'life_loop_enabled': (l) => FlagMeta(title: l.flagLifeLoopEnabledTitle, short_: l.flagLifeLoopEnabledHint, detail: l.flagLifeLoopEnabledDetail),
    'life_loop_visible': (l) => FlagMeta(title: l.flagLifeLoopVisibleTitle, short_: l.flagLifeLoopVisibleHint, detail: l.flagLifeLoopVisibleDetail),
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
    'recall_top5': (l) => FlagMeta(title: l.flagRecallTop5Title, short_: l.flagRecallTop5Hint, detail: l.flagRecallTop5Detail),
    'recall_diversify': (l) => FlagMeta(title: l.flagRecallDiversifyTitle, short_: l.flagRecallDiversifyHint, detail: l.flagRecallDiversifyDetail),
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
    'cross_char_fact_sync': (l) => FlagMeta(title: l.flagCrossCharFactSyncTitle, short_: l.flagCrossCharFactSyncHint, detail: l.flagCrossCharFactSyncDetail),
    'cross_char_fact_projection': (l) => FlagMeta(title: l.flagCrossCharFactProjectionTitle, short_: l.flagCrossCharFactProjectionHint, detail: l.flagCrossCharFactProjectionDetail),
    'working_state_enabled': (l) => FlagMeta(title: l.flagWorkingStateEnabledTitle, short_: l.flagWorkingStateEnabledHint, detail: l.flagWorkingStateEnabledDetail),
    'provider_registry': (l) => FlagMeta(title: l.flagProviderRegistryTitle, short_: l.flagProviderRegistryHint, detail: l.flagProviderRegistryDetail),
  };

  /// 高级开关文案：优先取 l10n；catalog 未登记（理论上不会发生）时回退中文兜底
  FlagMeta _localizedMeta(String key, AppLocalizations l10n) {
    final f = _flagMetaL10n[key];
    if (f != null) return f(l10n);
    return FeatureFlagCatalog.metaOf(key);
  }

  /// 组标题本地化（catalog 中文仅作兜底，界面优先取 l10n）
  String _groupTitle(String zh, AppLocalizations l10n) {
    switch (zh) {
      case "智能体运行与认知": return l10n.flagGroupAgentRuntime;
      case "主动消息": return l10n.flagGroupProactive;
      case "群聊小游戏": return l10n.flagGroupGroupGames;
      case "AI 自主生活": return l10n.flagGroupLifeLoop;
      case "生命感增强": return l10n.flagGroupLifeSense;
      case "主动消息自然化（B1）": return l10n.flagGroupProactiveNatural;
      case "记忆检索与注入（实验灰度）": return l10n.flagGroupMemory;
      case "编纂知识与前瞻意图": return l10n.flagGroupCurated;
      case "跨角色用户事实（B1）": return l10n.flagGroupCrossChar;
      case "工作记忆（M3）": return l10n.flagGroupWorking;
      case "插件与提供商": return l10n.flagGroupProvider;
      case "其他高级开关": return l10n.flagGroupOther;
      default: return zh;
    }
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
    final visible = _visibleKeys.where((k) => _flags.containsKey(k)).toList();
    // 高级键 = 全部已加载键 - 顶部常用键
    final advancedKeys =
        _flags.keys.where((k) => !_visibleKeys.contains(k)).toSet();
    final groups = FeatureFlagCatalog.groupEntries(advancedKeys);

    return ListView(
      padding: const EdgeInsets.only(top: 8, bottom: 24),
      children: [
        // 常用开关：保持原样
        IosCardGroup(
          title: l10n.featureFlagsHint,
          children: [
            for (final k in visible)
              SwitchListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 16),
                title: Text(_flagTitle(k, l10n)),
                subtitle: Text(_flagHint(k, l10n), style: const TextStyle(fontSize: 11)),
                value: _flags[k] ?? false,
                onChanged: (v) => _toggle(k, v),
              ),
          ],
        ),
        // 高级开关：按模块折叠
        for (final g in groups)
          _CollapsibleFlagGroup(
            title: _groupTitle(g.title, l10n),
            // 2026-09-04：全部默认折叠（不因组内被改过而自动展开），需要时手动点开
            initiallyOpen: false,
            tiles: [
              for (final k in g.keys)
                _FlagTileData(
                  rawKey: k,
                  meta: _localizedMeta(k, l10n),
                  value: _flags[k] ?? false,
                  source: _sources[k] ?? 'default',
                ),
            ],
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
  const _FlagTileData({
    required this.rawKey,
    required this.meta,
    required this.value,
    required this.source,
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
    final scheme = Theme.of(context).colorScheme;
    final m = widget.data.meta;
    final hasDetail = m.detail.isNotEmpty;
    final expanded = _openDetail && hasDetail;

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
          Switch.adaptive(
            value: widget.data.value,
            onChanged: widget.onChanged,
          ),
        ],
      ),
    );
  }
}
