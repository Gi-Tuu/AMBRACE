import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';
import '../../services/api_client.dart';
import 'archive_card.dart';

/// 游乐手札列表页：按游戏类型筛选，展示历史对局，点击展开 ArchiveCard。
class GameHistoryScreen extends StatefulWidget {
  const GameHistoryScreen({super.key});

  @override
  State<GameHistoryScreen> createState() => _GameHistoryScreenState();
}

class _GameHistoryScreenState extends State<GameHistoryScreen> {
  final ApiClient _api = ApiClient();
  List<Map<String, dynamic>> _catalog = [];
  List<Map<String, dynamic>> _items = [];
  String? _filter; // null = 全部
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final catalog = await _api.getGameCatalog();
      final items = await _api.getGameHistory(limit: 50, gameType: _filter);
      if (!mounted) return;
      setState(() {
        _catalog = catalog;
        _items = items;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  void _setFilter(String? type) {
    setState(() => _filter = type);
    _load();
  }

  String _gameName(Map<String, dynamic> item) {
    final archive = item['archive'] as Map<String, dynamic>? ?? {};
    final name = (archive['game_name'] as String?) ?? '';
    if (name.isNotEmpty) return name;
    final type = (item['game_type'] as String?) ?? '';
    for (final g in _catalog) {
      if (g['game_type'] == type) return (g['name'] as String?) ?? type;
    }
    return type;
  }

  String _timeStr(String? iso) {
    if (iso == null || iso.isEmpty) return '';
    try {
      final dt = DateTime.parse(iso).toLocal();
      return '${dt.year}-${dt.month.toString().padLeft(2, '0')}-'
          '${dt.day.toString().padLeft(2, '0')} '
          '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
    } catch (_) {
      return iso;
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final filterTypes = <String>[
      'werewolf', 'liars_bar', 'turtle_soup', 'undercover', 'truth_or_dare', 'twenty_q',
    ];
    final labelOf = {
      'werewolf': l10n.gameFilterWerewolf,
      'liars_bar': l10n.gameFilterLiarsBar,
      'turtle_soup': l10n.gameFilterTurtleSoup,
      'undercover': l10n.gameFilterUndercover,
      'truth_or_dare': l10n.gameFilterTruthOrDare,
      'twenty_q': l10n.gameFilterTwentyQ,
    };
    return Scaffold(
      appBar: AppBar(title: Text(l10n.gameHistoryTitle)),
      body: Column(
        children: [
          // 顶部筛选 chips
          SizedBox(
            height: 52,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              children: [
                _filterChip(l10n.gameFilterAll, null),
                for (final t in filterTypes) _filterChip(labelOf[t] ?? t, t),
              ],
            ),
          ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _items.isEmpty
                    ? Center(child: Text(l10n.gameHistoryEmpty))
                    : ListView.builder(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                        itemCount: _items.length,
                        itemBuilder: (context, i) => _historyCard(_items[i], l10n),
                      ),
          ),
        ],
      ),
    );
  }

  Widget _filterChip(String label, String? type) {
    final selected = _filter == type;
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: ChoiceChip(
        label: Text(label),
        selected: selected,
        onSelected: (_) => _setFilter(type),
      ),
    );
  }

  Widget _historyCard(Map<String, dynamic> item, AppLocalizations l10n) {
    final archive = item['archive'] as Map<String, dynamic>? ?? {};
    final playerCount = ((archive['player_count'] as num?)?.toInt() ?? 0);
    final winnerSide = (archive['winner_side'] as String?) ?? '';
    final hasArchive = archive.isNotEmpty;
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: Theme.of(context).colorScheme.outlineVariant),
      ),
      child: ExpansionTile(
        shape: const Border(),
        leading: const Icon(Icons.videogame_asset),
        title: Text(_gameName(item),
            style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(
          '$playerCount 人 · ${_timeStr((item['created_at'] as String?) ?? '')}'
          '${winnerSide.isNotEmpty ? ' · 🏆 $winnerSide' : ''}',
          style: const TextStyle(fontSize: 12),
        ),
        children: hasArchive
            ? [ArchiveCard(archive: archive)]
            : [
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: Text(l10n.gameNoArchive),
                ),
              ],
      ),
    );
  }
}
