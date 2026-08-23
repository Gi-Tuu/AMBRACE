import 'package:flutter/material.dart';

import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';

/// 抖音记忆收紧开关（2026-08-12）：公开平台记忆注入隐私保护
class DouyinMemoryScreen extends StatefulWidget {
  const DouyinMemoryScreen({super.key});

  @override
  State<DouyinMemoryScreen> createState() => _DouyinMemoryScreenState();
}

class _DouyinMemoryScreenState extends State<DouyinMemoryScreen> {
  final ApiClient _api = ApiClient();
  bool _loading = true;
  bool _restrict = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await _api.getDouyinProfile();
      if (mounted) {
        setState(() {
          _restrict = data['memory_restrict'] == 'relationship';
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _save(bool v) async {
    setState(() => _restrict = v);
    try {
      await _api.updateDouyinProfile(v ? 'relationship' : 'off');
      if (mounted) {
        final l10n = AppLocalizations.of(context)!;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(v ? l10n.dyMemoryOnSave : l10n.dyMemoryOffSave)),
        );
      }
    } catch (e) {
      if (mounted) {
        final l10n = AppLocalizations.of(context)!;
        setState(() => _restrict = !v);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.dyMemorySaveFailed(e))),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.dyMemoryTitle)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.only(top: 8, bottom: 16),
              children: [
                IosCardGroup(title: l10n.dyMemorySection, children: [
                  SwitchListTile(
                    contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                    title: Text(l10n.dyMemorySwitchTitle,
                        style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w500)),
                    subtitle: Text(l10n.dyMemorySwitchSubtitle,
                        style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                    value: _restrict,
                    onChanged: _save,
                    activeColor: scheme.primary,
                  ),
                ]),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                  child: Text(
                    l10n.dyMemoryNote,
                    style: TextStyle(fontSize: 12, color: scheme.onSurface.withValues(alpha: 0.55)),
                  ),
                ),
              ],
            ),
    );
  }
}
