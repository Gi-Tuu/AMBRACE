import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';
import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../services/api_exception.dart';

/// 成就与统计页（#62 Phase 3）。
///
/// 统计与成就均按「主体」查询：不传 character_id = 用户本人（我），
/// 传 character_id = 某个 AI 角色（后端会校验角色归属）。
/// 统计展示每个游戏的 局数/胜/负/平/中止/回合数/胜率 + 合计；
/// 成就展示全部定义，已解锁高亮、未解锁显示 `{progress}/{target}` 进度。
class GameAchievementsScreen extends StatefulWidget {
  const GameAchievementsScreen({super.key});

  @override
  State<GameAchievementsScreen> createState() => _GameAchievementsScreenState();
}

class _GameAchievementsScreenState extends State<GameAchievementsScreen> {
  final ApiClient _api = ApiClient();

  List<AICharacter> _characters = [];
  List<Map<String, dynamic>> _stats = [];
  List<Map<String, dynamic>> _achievements = [];
  int? _characterId; // null = 我
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final characters = _characters.isEmpty
          ? await _api.getCharacters()
          : _characters;
      final results = await Future.wait<Object?>([
        _api.getGameStats(characterId: _characterId),
        _api.getGameAchievements(characterId: _characterId),
      ], eagerError: true);
      if (!mounted) return;
      setState(() {
        _characters = characters;
        _stats = (results[0] as List).cast<Map<String, dynamic>>();
        _achievements = (results[1] as List).cast<Map<String, dynamic>>();
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = ApiException.messageOf(e);
      });
    }
  }

  void _pickScope(int? characterId) {
    setState(() => _characterId = characterId);
    _load();
  }

  int _sum(String metric) => _stats.fold<int>(
      0, (acc, s) => acc + ((s[metric] as num?)?.toInt() ?? 0));

  int _intOf(Map<String, dynamic> m, String k) =>
      (m[k] as num?)?.toInt() ?? 0;

  String _pct(double v) => '${(v * 100).round()}%';

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.gameAchievementsTitle)),
      body: Column(
        children: [
          _scopeSelector(l10n),
          Expanded(child: _body(l10n)),
        ],
      ),
    );
  }

  Widget _scopeSelector(AppLocalizations l10n) {
    return SizedBox(
      height: 52,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        children: [
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: ChoiceChip(
              label: Text(l10n.me),
              selected: _characterId == null,
              onSelected: (_) => _pickScope(null),
            ),
          ),
          for (final c in _characters)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: ChoiceChip(
                label: Text(c.name),
                selected: _characterId == c.id,
                onSelected: (_) => _pickScope(c.id),
              ),
            ),
        ],
      ),
    );
  }

  Widget _body(AppLocalizations l10n) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(l10n.loadFailedErr(_error!),
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.grey)),
            const SizedBox(height: 12),
            FilledButton.tonal(
              onPressed: _load,
              child: Text(l10n.retry),
            ),
          ],
        ),
      );
    }
    final scheme = Theme.of(context).colorScheme;
    return ListView(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      children: [
        _sectionTitle(l10n.gameStatsTitle),
        const SizedBox(height: 6),
        if (_stats.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 12),
            child: Text(l10n.gameStatsEmpty,
                style: TextStyle(color: scheme.onSurfaceVariant)),
          )
        else ...[
          _statsCard(l10n.gameStatsTotal, {
            'games_played': _sum('games_played'),
            'wins': _sum('wins'),
            'losses': _sum('losses'),
            'draws': _sum('draws'),
            'aborted': _sum('aborted'),
            'total_rounds': _sum('total_rounds'),
          }, l10n, highlight: true),
          for (final s in _stats)
            _statsCard(_gameName((s['game_type'] as String?) ?? '', l10n), s,
                l10n),
        ],
        const SizedBox(height: 18),
        _sectionTitle(
            '${l10n.gameAchievementsList} · ${l10n.gameAchievementsUnlockedCount(_unlockedCount, _achievements.length)}'),
        const SizedBox(height: 6),
        for (final a in _achievements) _achievementCard(a, l10n),
        const SizedBox(height: 16),
      ],
    );
  }

  int get _unlockedCount =>
      _achievements.where((a) => a['unlocked'] == true).length;

  Widget _sectionTitle(String text) {
    return Text(text,
        style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15));
  }

  Widget _statsCard(String title, Map<String, dynamic> s, AppLocalizations l10n,
      {bool highlight = false}) {
    final scheme = Theme.of(context).colorScheme;
    final games = _intOf(s, 'games_played');
    final wins = _intOf(s, 'wins');
    final rate = games > 0 ? wins / games : 0.0;
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 5),
      elevation: 0,
      color: highlight ? scheme.primaryContainer.withValues(alpha: 0.35) : null,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: scheme.outlineVariant),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(title,
                      style: const TextStyle(fontWeight: FontWeight.w600)),
                ),
                Text('${l10n.gameStatsWinRate} ${_pct(rate)}',
                    style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                        color: scheme.primary)),
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                _metricChip(l10n.gameStatsGames, games, scheme),
                _metricChip(l10n.gameStatsWins, wins, scheme),
                _metricChip(l10n.gameStatsLosses, _intOf(s, 'losses'), scheme),
                _metricChip(l10n.gameStatsDraws, _intOf(s, 'draws'), scheme),
                _metricChip(l10n.gameStatsAborted, _intOf(s, 'aborted'), scheme),
                _metricChip(
                    l10n.gameStatsRounds, _intOf(s, 'total_rounds'), scheme),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _metricChip(String label, int value, ColorScheme scheme) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text('$label $value',
          style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant)),
    );
  }

  Widget _achievementCard(Map<String, dynamic> a, AppLocalizations l10n) {
    final scheme = Theme.of(context).colorScheme;
    final unlocked = a['unlocked'] == true;
    final target = _intOf(a, 'target');
    final progress = _intOf(a, 'progress');
    final value = target > 0 ? (progress / target).clamp(0.0, 1.0) : 0.0;
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 5),
      elevation: 0,
      color: unlocked ? scheme.primaryContainer.withValues(alpha: 0.35) : null,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(
            color: unlocked ? scheme.primary : scheme.outlineVariant),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  unlocked ? Icons.military_tech : Icons.lock_outline,
                  size: 20,
                  color: unlocked ? scheme.primary : scheme.onSurfaceVariant,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text((a['title'] as String?) ?? '',
                      style: TextStyle(
                        fontWeight: FontWeight.w600,
                        color: unlocked ? null : scheme.onSurfaceVariant,
                      )),
                ),
                Text(
                  unlocked
                      ? l10n.gameAchievementsProgress(progress, target)
                      : l10n.gameAchievementsLocked,
                  style: TextStyle(
                    fontSize: 11,
                    color: unlocked ? scheme.primary : scheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text((a['description'] as String?) ?? '',
                style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant)),
            const SizedBox(height: 6),
            Row(
              children: [
                Expanded(
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: LinearProgressIndicator(
                      value: value,
                      minHeight: 6,
                      color: unlocked ? scheme.primary : null,
                      backgroundColor: scheme.surfaceContainerHighest,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Text(_gameName((a['game_type'] as String?) ?? '', l10n),
                    style:
                        TextStyle(fontSize: 11, color: scheme.onSurfaceVariant)),
              ],
            ),
          ],
        ),
      ),
    );
  }

  /// game_type -> 展示名（'*' = 跨游戏聚合；未收录则回退原始 type）。
  String _gameName(String type, AppLocalizations l10n) {
    switch (type) {
      case '*':
        return l10n.gameAchievementsAll;
      case 'werewolf':
        return l10n.gameFilterWerewolf;
      case 'liars_bar':
        return l10n.gameFilterLiarsBar;
      case 'turtle_soup':
        return l10n.gameFilterTurtleSoup;
      case 'undercover':
        return l10n.gameFilterUndercover;
      case 'truth_or_dare':
        return l10n.gameFilterTruthOrDare;
      case 'twenty_q':
        return l10n.gameFilterTwentyQ;
      default:
        return type;
    }
  }
}
