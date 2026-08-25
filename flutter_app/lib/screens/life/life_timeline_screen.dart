import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

import '../../services/api_client.dart';
import '../../utils/beijing_time.dart';
import '../../widgets/ios_card_group.dart';
import "package:ai_companion/theme/tokens.dart";

/// AI 生活时间线（Life Engine v2，2026-08-12）：只读展示角色自己的生活点滴（source=life 记忆）
class LifeTimelineScreen extends StatefulWidget {
  const LifeTimelineScreen({super.key, required this.characterId, required this.characterName, this.showScaffold = true});

  final int characterId;
  final String characterName;
  final bool showScaffold;

  @override
  State<LifeTimelineScreen> createState() => _LifeTimelineScreenState();
}

class _LifeTimelineScreenState extends State<LifeTimelineScreen> {
  final ApiClient _api = ApiClient();
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;

  Map<String, String> _typeLabel(AppLocalizations l10n) => {
    'life_event': l10n.lifeTypeLife,
    'reflection': l10n.lifeTypeReflection,
    'note': l10n.lifeTypeNote,
    'interest': l10n.lifeTypeInterest,
    'goal': l10n.lifeTypeGoal,
  };

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final items = await _api.getLifeTimeline(characterId: widget.characterId);
      if (mounted) {
        setState(() {
          _items = items;
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  String _fmt(String iso) {
    try {
      return formatBeijingTime(iso).substring(0, 16);
    } catch (_) {
      return '';
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    final body = _loading
          ? const Center(child: CircularProgressIndicator())
          : _items.isEmpty
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.self_improvement_outlined, size: 48, color: Colors.grey),
                      const SizedBox(height: 8),
                      Text(l10n.noLifeRecords, style: const TextStyle(color: Colors.grey)),
                      const SizedBox(height: 6),
                      Text(
                        l10n.offlineLifeHint,
                        style: TextStyle(fontSize: 12, color: scheme.onSurface.withValues(alpha: 0.55)),
                      ),
                    ],
                  ),
                )
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.all(12),
                    children: [
                      IosCardGroup(children: [
                        for (var i = 0; i < _items.length; i++) ...[
                          if (i > 0) const IosCardDivider(indent: 56),
                          _row(_items[i], scheme),
                        ],
                      ]),
                    ],
                  ),
                );
    if (!widget.showScaffold) return body;
    return Scaffold(
      backgroundColor: AppColors.bgLight,
      appBar: AppBar(title: Text(l10n.lifeHomeTitle(widget.characterName))),
      body: body,
    );
  }

  Widget _row(Map<String, dynamic> m, ColorScheme scheme) {
    final l10n = AppLocalizations.of(context)!;
    final sub = (m['sub_type'] as String? ?? 'life_event');
    final label = _typeLabel(l10n)[sub] ?? l10n.lifeTypeLife;
    final icon = sub == 'reflection'
        ? Icons.psychology_outlined
        : sub == 'note'
            ? Icons.notes
            : Icons.self_improvement_outlined;
    final t = _fmt(m['created_at'] as String? ?? '');
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
      leading: Container(
        width: 38,
        height: 38,
        decoration: BoxDecoration(
          color: scheme.primary.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Icon(icon, size: 20, color: scheme.primary),
      ),
      title: Text(
        m['content'] as String? ?? '',
        maxLines: 3,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontSize: 14, height: 1.4),
      ),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
              decoration: BoxDecoration(
                color: scheme.primary.withValues(alpha: 0.08),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(label, style: TextStyle(fontSize: 10, color: scheme.primary)),
            ),
            const SizedBox(width: 8),
            Text(t, style: TextStyle(fontSize: 11, color: scheme.onSurface.withValues(alpha: 0.45))),
          ],
        ),
      ),
    );
  }
}
