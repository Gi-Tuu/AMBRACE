import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/providers/game_provider.dart';
import 'package:ai_companion/widgets/app_page_route.dart';
import 'game_room_screen.dart';

/// 游戏机面板：按单人/双人/多人分区展示 3 款游戏；
/// 选参与者 AI 角色列表；用户身份玩家/观战选择（默认观战）；开始按钮。
class GameConsoleScreen extends StatefulWidget {
  const GameConsoleScreen({super.key});

  @override
  State<GameConsoleScreen> createState() => _GameConsoleScreenState();
}

class _GameConsoleScreenState extends State<GameConsoleScreen> {
  final GameProvider _provider = GameProvider();
  Map<String, dynamic>? _selected;
  bool _userAsPlayer = false; // 默认观战
  final Set<int> _selectedCharIds = {};

  @override
  void initState() {
    super.initState();
    _provider.loadCatalogAndCharacters();
  }

  @override
  void dispose() {
    _provider.dispose();
    super.dispose();
  }

  String _modeLabel(AppLocalizations l10n, String mode) {
    switch (mode) {
      case 'single':
        return l10n.gameSingle;
      case 'dual':
        return l10n.gameDual;
      case 'multi':
        return l10n.gameMulti;
      default:
        return mode;
    }
  }

  int get _neededMin {
    final m = _selected;
    if (m == null) return 0;
    final min = (m['min_players'] as num?)?.toInt() ?? 0;
    return min - (_userAsPlayer ? 1 : 0);
  }

  int get _neededMax {
    final m = _selected;
    if (m == null) return 0;
    final max = (m['max_players'] as num?)?.toInt() ?? 0;
    return max - (_userAsPlayer ? 1 : 0);
  }

  bool get _validSelection {
    if (_selected == null) return false;
    final n = _selectedCharIds.length;
    return n >= _neededMin && n <= _neededMax;
  }

  Future<void> _start(AppLocalizations l10n) async {
    if (_selected == null || !_validSelection) return;
    final ok = await _provider.createSession(
      gameType: _selected!['game_type'] as String,
      playerIds: _selectedCharIds.toList(),
      spectatorIds: const [],
      userAsPlayer: _userAsPlayer,
    );
    if (!mounted) return;
    if (ok) {
      Navigator.of(context).push(AppPageRoute(builder: (_) => GameRoomScreen(provider: _provider)));
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.gameStartFailed(_provider.error ?? ''))),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.gameTitle)),
      body: _provider.loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(l10n.gameSelectGameType,
                    style: const TextStyle(fontWeight: FontWeight.w700)),
                const SizedBox(height: 8),
                for (final mode in ['single', 'dual', 'multi']) ...[
                  Text(_modeLabel(l10n, mode),
                      style: const TextStyle(fontWeight: FontWeight.w600, color: Colors.grey)),
                  const SizedBox(height: 6),
                  for (final g in _provider.catalog
                      .where((g) => g['player_mode'] == mode))
                    _gameCard(g, l10n),
                  const SizedBox(height: 8),
                ],
                if (_selected != null) ...[
                  const Divider(height: 24),
                  _participantSection(l10n),
                  const SizedBox(height: 16),
                  FilledButton(
                    onPressed: _validSelection ? () => _start(l10n) : null,
                    child: Text(l10n.gameStart),
                  ),
                ],
              ],
            ),
    );
  }

  Widget _gameCard(Map<String, dynamic> g, AppLocalizations l10n) {
    final selected = _selected?['game_type'] == g['game_type'];
    final name = (g['name'] as String?) ?? '';
    final desc = (g['description'] as String?) ?? '';
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(
          color: selected ? Theme.of(context).colorScheme.primary : Colors.transparent,
          width: 2,
        ),
      ),
      child: ListTile(
        leading: const Icon(Icons.sports_esports),
        title: Text(name),
        subtitle: Text(desc),
        trailing: const Icon(Icons.chevron_right),
        onTap: () {
          setState(() {
            _selected = g;
            _selectedCharIds.clear();
          });
        },
      ),
    );
  }

  Widget _participantSection(AppLocalizations l10n) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('${l10n.gameSelectPlayers}（$_neededMin-$_neededMax）',
            style: const TextStyle(fontWeight: FontWeight.w700)),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final c in _provider.characters)
              if (c.isActive)
                FilterChip(
                  label: Text(c.name),
                  selected: _selectedCharIds.contains(c.id),
                  onSelected: (v) => setState(() {
                    if (v) {
                      if (_selectedCharIds.length < _neededMax) {
                        _selectedCharIds.add(c.id);
                      }
                    } else {
                      _selectedCharIds.remove(c.id);
                    }
                  }),
                ),
          ],
        ),
        const SizedBox(height: 16),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: Text(l10n.gameUserRole),
          subtitle: Text(l10n.gameUserAsSpectator),
          value: _userAsPlayer,
          onChanged: (v) => setState(() => _userAsPlayer = v),
        ),
      ],
    );
  }
}
