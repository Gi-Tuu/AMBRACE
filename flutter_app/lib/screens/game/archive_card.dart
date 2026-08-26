import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

/// 游乐手札折叠卡片：游戏名/时间/人数/玩家列表/胜负/回合数/时间线折叠。
/// 纯数据渲染（后端 build_archive 零 LLM 生成结构化 JSON）。
class ArchiveCard extends StatefulWidget {
  final Map<String, dynamic> archive;
  const ArchiveCard({super.key, required this.archive});

  @override
  State<ArchiveCard> createState() => _ArchiveCardState();
}

class _ArchiveCardState extends State<ArchiveCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final a = widget.archive;
    final name = (a['game_name'] as String?) ?? (a['game_type'] as String? ?? '');
    final players = ((a['players'] as List?) ?? const []).cast<Map<String, dynamic>>();
    final timeline = ((a['timeline'] as List?) ?? const []).cast<Map<String, dynamic>>();
    final winnerSide = (a['winner_side'] as String?) ?? '';
    final rounds = (a['rounds'] as num?)?.toInt() ?? 0;
    final playerCount = (a['player_count'] as num?)?.toInt() ?? players.length;

    final winnerNames = players
        .where((p) => p['result'] == 'won')
        .map((p) => (p['name'] as String?) ?? '')
        .where((s) => s.isNotEmpty)
        .toList();

    return Card(
      elevation: 0,
      margin: const EdgeInsets.symmetric(vertical: 6),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: Theme.of(context).colorScheme.outlineVariant),
      ),
      child: ExpansionTile(
        initiallyExpanded: _expanded,
        onExpansionChanged: (v) => setState(() => _expanded = v),
        shape: const Border(),
        leading: const Icon(Icons.auto_awesome),
        title: Text(name, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(
          '${l10n.archivePlayerCount('$playerCount')} · ${l10n.archiveRounds('$rounds')} · '
          '${winnerNames.isNotEmpty ? l10n.archiveWinner(winnerNames.join('、')) : l10n.archiveDraw}',
          style: const TextStyle(fontSize: 12),
        ),
        children: [
          Divider(height: 1, color: Theme.of(context).colorScheme.outlineVariant),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l10n.archivePlayers, style: const TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(height: 6),
                for (final p in players)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 2),
                    child: Row(
                      children: [
                        Text(
                          '${p['name'] ?? ''}',
                          style: TextStyle(
                            fontWeight: p['result'] == 'won' ? FontWeight.bold : FontWeight.normal,
                          ),
                        ),
                        const SizedBox(width: 8),
                        Text(
                          '${p['role'] ?? ''}',
                          style: const TextStyle(fontSize: 12, color: Colors.grey),
                        ),
                        const Spacer(),
                        Text(
                          p['result'] == 'won' ? '🏆' : '',
                          style: const TextStyle(fontSize: 14),
                        ),
                      ],
                    ),
                  ),
                const SizedBox(height: 10),
                Text(l10n.archiveTimeline, style: const TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(height: 6),
                for (final ev in timeline)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 2),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '${l10n.archiveRoundLabel('${ev['round'] ?? 0}')} · ',
                          style: const TextStyle(fontSize: 11, color: Colors.grey),
                        ),
                        Expanded(
                          child: Text(
                            (ev['content'] as String?) ?? '',
                            style: const TextStyle(fontSize: 12),
                          ),
                        ),
                      ],
                    ),
                  ),
                if (winnerSide.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                      '${l10n.archiveWinnerSide}: $winnerSide',
                      style: const TextStyle(fontSize: 12, color: Colors.grey),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
