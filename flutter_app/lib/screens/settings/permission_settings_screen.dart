import 'package:flutter/material.dart';

import '../../services/api/permission_api.dart';
import '../../services/api_client.dart';
import "package:ai_companion/theme/tokens.dart";
import 'package:flutter_gen/gen_l10n/app_localizations.dart';

/// AI 能力权限设置（2026-08-12，参考 Operit 工具权限模型）
/// 三档：允许 / 每次询问 / 禁止；全局默认 + 每能力例外（例外优先）。
class PermissionSettingsScreen extends StatefulWidget {
  const PermissionSettingsScreen({super.key});

  @override
  State<PermissionSettingsScreen> createState() =>
      _PermissionSettingsScreenState();
}

class _PermissionSettingsScreenState extends State<PermissionSettingsScreen> {
  final _api = ApiClient();
  bool _loading = true;
  String _globalLevel = 'allow';
  Map<String, String> _scopes = {};

  static const _scopeOrder = <String>[
    'image_gen',
    'image_understand',
    'tts',
    'asr',
    'browser',
    'douyin',
    'extension',
  ];

  static const _scopeIcons = <String, IconData>{
    'image_gen': Icons.auto_awesome,
    'image_understand': Icons.image_search_outlined,
    'tts': Icons.record_voice_over_outlined,
    'asr': Icons.mic_none,
    'browser': Icons.public,
    'douyin': Icons.music_note_outlined,
    'extension': Icons.extension_outlined,
  };

  static const _levels = <String>['allow', 'ask', 'forbid'];

  String _scopeTitle(String scope, AppLocalizations l10n) {
    switch (scope) {
      case 'image_gen':
        return l10n.permScopeImgTitle;
      case 'image_understand':
        return l10n.permScopeImgUnderstandTitle;
      case 'tts':
        return l10n.permScopeTtsTitle;
      case 'asr':
        return l10n.permScopeAsrTitle;
      case 'browser':
        return l10n.permScopeBrowserTitle;
      case 'douyin':
        return l10n.permScopeDouyinTitle;
      case 'extension':
        return l10n.permScopeExtensionTitle;
      default:
        return scope;
    }
  }

  String _scopeDesc(String scope, AppLocalizations l10n) {
    switch (scope) {
      case 'image_gen':
        return l10n.permScopeImgDesc;
      case 'image_understand':
        return l10n.permScopeImgUnderstandDesc;
      case 'tts':
        return l10n.permScopeTtsDesc;
      case 'asr':
        return l10n.permScopeAsrDesc;
      case 'browser':
        return l10n.permScopeBrowserDesc;
      case 'douyin':
        return l10n.permScopeDouyinDesc;
      case 'extension':
        return l10n.permScopeExtensionDesc;
      default:
        return scope;
    }
  }

  String _levelLabel(String level, AppLocalizations l10n) {
    switch (level) {
      case 'allow':
        return l10n.permLevelAllow;
      case 'ask':
        return l10n.permLevelAsk;
      case 'forbid':
        return l10n.permLevelForbid;
      default:
        return l10n.permLevelAllow;
    }
  }

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await _api.getPermissions();
      if (!mounted) return;
      setState(() {
        _globalLevel = data['global_level'] as String? ?? 'allow';
        final scopes = (data['scopes'] as Map?) ?? {};
        _scopes = scopes.map((k, v) => MapEntry(k.toString(), v.toString()));
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _save({String? globalLevel, Map<String, String>? scopes}) async {
    try {
      final data = await _api.updatePermissions(
        globalLevel: globalLevel,
        scopes: scopes,
      );
      if (!mounted) return;
      setState(() {
        if (globalLevel != null) {
          _globalLevel = data['global_level'] as String? ?? _globalLevel;
        }
        final updated = (data['scopes'] as Map?) ?? {};
        _scopes = updated.map((k, v) => MapEntry(k.toString(), v.toString()));
      });
    } catch (_) {
      if (mounted) {
        final l10n = AppLocalizations.of(context)!;
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.permSaveFailed)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      backgroundColor: AppColors.bgLight,
      appBar: AppBar(
        title: Text(l10n.permTitle),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.only(top: 8, bottom: 24),
              children: [
                _group(
                  title: l10n.permGlobalDefault,
                  children: [
                    Padding(
                      padding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            l10n.permGlobalDefaultHint,
                            style: const TextStyle(
                                fontSize: 12, color: AppColors.textSecondary),
                          ),
                          const SizedBox(height: 10),
                          _levelSelector(
                            value: _globalLevel,
                            onChanged: (v) {
                              setState(() => _globalLevel = v);
                              _save(globalLevel: v);
                            },
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
                _group(
                  title: l10n.permScopes,
                  children: [
                    for (final scope in _scopeOrder) _scopeRow(scope),
                  ],
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                  child: Text(
                    l10n.permAskNote,
                    style: const TextStyle(
                        fontSize: 11, color: AppColors.textSecondary, height: 1.4),
                  ),
                ),
              ],
            ),
    );
  }

  Widget _group({required String title, required List<Widget> children}) {
    return Padding(
      padding: const EdgeInsets.only(left: 12, right: 12, bottom: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(left: 16, bottom: 6),
            child: Text(
              title,
              style: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: AppColors.textSecondary,
              ),
            ),
          ),
          Container(
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(children: children),
          ),
        ],
      ),
    );
  }

  Widget _scopeRow(String scope) {
    final l10n = AppLocalizations.of(context)!;
    final level = _scopes[scope] ?? _globalLevel;
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
      decoration: const BoxDecoration(
        border:
            Border(bottom: BorderSide(color: Color(0xFFF0F0F2), width: 0.5)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(_scopeIcons[scope] ?? Icons.extension, size: 18, color: AppColors.accent),
              const SizedBox(width: 8),
              Text(
                _scopeTitle(scope, l10n),
                style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w500,
                    color: AppColors.textPrimary),
              ),
              const Spacer(),
              Text(
                _levelLabel(level, l10n),
                style: const TextStyle(fontSize: 12, color: AppColors.textSecondary),
              ),
            ],
          ),
          const SizedBox(height: 3),
          Padding(
            padding: const EdgeInsets.only(left: 26),
            child: Text(
              _scopeDesc(scope, l10n),
              style: const TextStyle(fontSize: 11, color: AppColors.textSecondary),
            ),
          ),
          const SizedBox(height: 8),
          Padding(
            padding: const EdgeInsets.only(left: 26),
            child: _levelSelector(
              value: level,
              onChanged: (v) {
                setState(() => _scopes[scope] = v);
                _save(scopes: {scope: v});
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _levelSelector(
      {required String value, required ValueChanged<String> onChanged}) {
    final l10n = AppLocalizations.of(context)!;
    return SegmentedButton<String>(
      segments: [
        for (final lv in _levels)
          ButtonSegment(
            value: lv,
            label: Text(_levelLabel(lv, l10n),
                style: TextStyle(
                    fontSize: 12,
                    color: value == lv ? AppColors.accent : null)),
          ),
      ],
      selected: {value},
      onSelectionChanged: (s) => onChanged(s.first),
      showSelectedIcon: false,
      style: const ButtonStyle(
        visualDensity: VisualDensity.compact,
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),
    );
  }
}
