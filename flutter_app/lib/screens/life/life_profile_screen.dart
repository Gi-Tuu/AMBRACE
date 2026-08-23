import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';

import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';
import "package:ai_companion/theme/tokens.dart";

/// AI 生活兴趣与目标（Life Engine v2 Phase 3，2026-08-12）
class LifeProfileScreen extends StatefulWidget {
  const LifeProfileScreen({super.key, required this.characterId, required this.characterName, this.showScaffold = true});

  final int characterId;
  final String characterName;
  final bool showScaffold;

  @override
  State<LifeProfileScreen> createState() => _LifeProfileScreenState();
}

class _LifeProfileScreenState extends State<LifeProfileScreen> {
  final ApiClient _api = ApiClient();
  List<Map<String, dynamic>> _interests = [];
  List<Map<String, dynamic>> _goals = [];
  bool _loading = true;

  Map<String, String> _goalTypeLabel(AppLocalizations l10n) => {
    'relationship': l10n.goalTypeRelationship,
    'creative': l10n.goalTypeCreative,
    'growth': l10n.goalTypeGrowth,
    'explore': l10n.goalTypeExplore,
    'skill': l10n.goalTypeSkill,
  };
  Map<String, String> _goalStatusLabel(AppLocalizations l10n) => {
    'active': l10n.goalActive,
    'completed': l10n.goalCompleted,
    'failed': l10n.goalFailed,
  };

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([
        _api.getLifeInterests(widget.characterId),
        _api.getLifeGoals(widget.characterId),
      ]);
      if (mounted) {
        setState(() {
          _interests = results[0];
          _goals = results[1];
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Color _levelColor(int level) {
    if (level >= 60) return const Color(0xFFE8682C); // 热爱
    if (level >= 30) return const Color(0xFF3E8E7E); // 兴趣中
    return AppColors.textSecondary; // 一般
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    final body = _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(12),
                children: [
                  IosCardGroup(children: [
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                      child: Row(
                        children: [
                          Icon(Icons.favorite_outline, size: 18, color: scheme.primary),
                          const SizedBox(width: 6),
                          Text(l10n.interests,
                              style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                        ],
                      ),
                    ),
                    if (_interests.isEmpty)
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                        child: Text(l10n.noInterests, style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
                      )
                    else
                      Padding(
                        padding: const EdgeInsets.fromLTRB(12, 0, 12, 10),
                        child: Wrap(
                          spacing: 8,
                          runSpacing: 8,
                          children: [
                            for (final it in _interests) _interestChip(it),
                          ],
                        ),
                      ),
                  ]),
                  const SizedBox(height: 12),
                  IosCardGroup(children: [
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                      child: Row(
                        children: [
                          Icon(Icons.flag_outlined, size: 18, color: scheme.primary),
                          const SizedBox(width: 6),
                          Text(l10n.goal,
                              style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                        ],
                      ),
                    ),
                    if (_goals.isEmpty)
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                        child: Text(l10n.noGoals, style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
                      )
                    else
                      for (var i = 0; i < _goals.length; i++) ...[
                        if (i > 0) const IosCardDivider(indent: 12),
                        _goalRow(_goals[i], scheme),
                      ],
                  ]),
                ],
              ),
            );
    if (!widget.showScaffold) return body;
    return Scaffold(
      backgroundColor: AppColors.bgLight,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: Text(l10n.interestsGoalsTitle(widget.characterName)),
        centerTitle: true,
      ),
      body: body,
    );
  }

  Widget _interestChip(Map<String, dynamic> it) {
    final name = it['name'] as String? ?? '';
    final level = (it['level'] as num? ?? 0).toInt();
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: _levelColor(level).withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(name, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w500)),
          const SizedBox(width: 6),
          Text('$level', style: TextStyle(fontSize: 11, color: _levelColor(level))),
        ],
      ),
    );
  }

  Widget _goalRow(Map<String, dynamic> g, ColorScheme scheme) {
    final l10n = AppLocalizations.of(context)!;
    final type = _goalTypeLabel(l10n)[g['type']] ?? l10n.goal;
    final status = g['status'] as String? ?? 'active';
    final done = status == 'completed';
    final progress = (g['progress'] as num? ?? 0).toInt();
    final total = (g['progress_total'] as num? ?? 1).toInt();
    final pct = total <= 0 ? 0.0 : (progress / total).clamp(0.0, 1.0);
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
      title: Text(g['title'] as String? ?? '',
          style: TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w500,
            color: done ? IosCardColors.subtitle : scheme.onSurface,
            decoration: done ? TextDecoration.lineThrough : null,
          )),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(type,
                    style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                const SizedBox(width: 8),
                Text(_goalStatusLabel(l10n)[status] ?? status,
                    style: TextStyle(
                        fontSize: 11,
                        color: status == 'completed'
                            ? const Color(0xFF3E8E7E)
                            : status == 'failed'
                                ? const Color(0xFFC0392B)
                                : scheme.primary)),
              ],
            ),
            const SizedBox(height: 4),
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(value: pct, minHeight: 5),
            ),
            const SizedBox(height: 2),
            Text('$progress / $total',
                style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
          ],
        ),
      ),
    );
  }
}
