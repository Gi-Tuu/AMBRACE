import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';

/// 服务器功能开关页（2026-08-18）：主账号可热切换运行时 Feature Flag（无需重启）
class FeatureFlagsScreen extends StatefulWidget {
  const FeatureFlagsScreen({super.key});
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
    'agent_loop_douyin',
  ];

  @override
  void initState() {
    super.initState();
    _isAdmin = context.read<SettingsProvider>().userId == 1;
    _load();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = ''; });
    try {
      final flags = await ApiClient().getFeatureFlags();
      for (final f in flags) {
        final k = f['key'] as String? ?? '';
        if (k.isNotEmpty) {
          _flags[k] = (f['enabled'] as bool?) ?? false;
          _sources[k] = f['source'] as String? ?? 'default';
        }
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
    try {
      await ApiClient().updateFeatureFlag(key, value);
      if (mounted) {
        setState(() => _sources[key] = 'db');
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.flagSaved)));
      }
    } catch (_) {
      if (mounted) {
        setState(() { if (prev != null) _flags[key] = prev; });
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.flagError)));
      }
    }
  }

  String _flagTitle(String key, AppLocalizations l10n) {
    switch (key) {
      case 'agent_social_light_context': return l10n.flagLightReply;
      case 'agent_loop_group_chat': return l10n.flagGroupRuntime;
      case 'agent_loop_douyin': return l10n.flagDouyinRuntime;
      default: return key;
    }
  }

  String _flagHint(String key, AppLocalizations l10n) {
    switch (key) {
      case 'agent_social_light_context': return l10n.flagLightReplyHint;
      case 'agent_loop_group_chat': return l10n.flagGroupRuntimeHint;
      case 'agent_loop_douyin': return l10n.flagDouyinRuntimeHint;
      default: return l10n.flagAdvancedHint;
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.featureFlagsTitle)),
      body: _body(l10n),
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
    final advanced = _flags.keys.where((k) => !_visibleKeys.contains(k)).toList()..sort();
    return ListView(
      padding: const EdgeInsets.only(top: 8, bottom: 24),
      children: [
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
        if (advanced.isNotEmpty)
          IosCardGroup(
            title: l10n.flagAdvanced,
            children: [
              for (final k in advanced)
                SwitchListTile(
                  contentPadding: const EdgeInsets.symmetric(horizontal: 16),
                  title: Text(k),
                  subtitle: Text('[${_sources[k] ?? 'default'}] ${_flagHint(k, l10n)}', style: const TextStyle(fontSize: 11)),
                  value: _flags[k] ?? false,
                  onChanged: (v) => _toggle(k, v),
                ),
            ],
          ),
      ],
    );
  }
}
