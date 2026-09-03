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
            title: g.title,
            // 2026-09-04：全部默认折叠（不因组内被改过而自动展开），需要时手动点开
            initiallyOpen: false,
            tiles: [
              for (final k in g.keys)
                _FlagTileData(
                  rawKey: k,
                  meta: FeatureFlagCatalog.metaOf(k),
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
