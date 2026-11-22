import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import 'game_console_page.dart';
import '../../widgets/reader_async_view.dart';
import 'reader_state.dart';

/// 游戏只读入口：目录 + 历史（创建对局/行动/实时 WS 暂不做）。
class GamesPane extends StatefulWidget {
  const GamesPane({
    super.key,
    required this.client,
    required this.rs,
    required this.cs,
  });

  final AmbApiClient client;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<GamesPane> createState() => _GamesPaneState();
}

class _GamesPaneState extends State<GamesPane> {
  late final GameApi _api = GameApi(widget.client);
  late final ReaderLoader<List<Map<String, dynamic>>> _catalog =
      ReaderLoader<List<Map<String, dynamic>>>();
  late final ReaderLoader<List<Map<String, dynamic>>> _history =
      ReaderLoader<List<Map<String, dynamic>>>();
  late final ReaderLoader<List<Map<String, dynamic>>> _achievements =
      ReaderLoader<List<Map<String, dynamic>>>();

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _catalog.dispose();
    _history.dispose();
    _achievements.dispose();
    super.dispose();
  }

  void _reload() {
    _catalog.load((ct) => _api.catalog(cancelToken: ct),
        isEmpty: (items) => items.isEmpty);
    _history.load((ct) => _api.history(cancelToken: ct),
        isEmpty: (items) => items.isEmpty);
    _achievements.load((ct) => _api.achievements(cancelToken: ct),
        isEmpty: (items) => items.isEmpty);
  }

  String _s(Map<String, dynamic> m, List<String> keys) {
    for (final k in keys) {
      final v = m[k];
      if (v != null && '$v'.trim().isNotEmpty) return '$v';
    }
    return '';
  }

  String _time(String? iso) {
    if (iso == null) return '';
    final dt = DateTime.tryParse(iso.endsWith('Z') ? iso : '${iso}Z');
    if (dt == null) return iso;
    final local = dt.toUtc().add(const Duration(hours: 8));
    String two(int v) => v.toString().padLeft(2, '0');
    return '${local.year}-${two(local.month)}-${two(local.day)} '
        '${two(local.hour)}:${two(local.minute)}';
  }

  Widget _catalogView() {
    final rs = widget.rs;
    return ReaderAsyncView<List<Map<String, dynamic>>>(
      loader: _catalog,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (items) => ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(rs.t('gamePlayNotSupported'),
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 10),
          for (final g in items)
            Card(
              margin: const EdgeInsets.only(bottom: 10),
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    Expanded(
                      child: Text(
                        _s(g, ['name', 'title', 'game_type']),
                        style: Theme.of(context).textTheme.titleSmall,
                      ),
                    ),
                    if (_s(g, ['game_type']).isNotEmpty)
                      Chip(
                        label: Text(_s(g, ['game_type'])),
                        visualDensity: VisualDensity.compact,
                      ),
                  ]),
                  if (_s(g, ['description', 'desc']).isNotEmpty) ...[
                    const SizedBox(height: 4),
                    Text(_s(g, ['description', 'desc'])),
                  ],
                  if (_s(g, ['player_mode']).isNotEmpty)
                    Text('${rs.t('gameMode')}: ${_s(g, ['player_mode'])}',
                        style: Theme.of(context).textTheme.bodySmall),
                ]),
              ),
            ),
        ],
      ),
    );
  }

  Widget _historyView() {
    final rs = widget.rs;
    return ReaderAsyncView<List<Map<String, dynamic>>>(
      loader: _history,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (items) => ListView(
        padding: const EdgeInsets.all(16),
        children: [
          for (final h in items)
            Card(
              margin: const EdgeInsets.only(bottom: 10),
              child: ListTile(
                title: Text(
                    '#${h['session_id']} · ${_s(h, ['game_type'])}'),
                subtitle: Text([
                  if (_s(h, ['status']).isNotEmpty)
                    '${rs.t('gameStatus')}: ${_s(h, ['status'])}',
                  if (h['winner_side'] != null &&
                      '${h['winner_side']}'.isNotEmpty)
                    '${rs.t('gameWinner')}: ${h['winner_side']}',
                  if (h['rounds'] != null) '${rs.t('gameRounds')}: ${h['rounds']}',
                  if (h['created_at'] != null)
                    _time(h['created_at'] as String?),
                ].join(' · ')),
              ),
            ),
        ],
      ),
    );
  }

  Widget _achievementsView() {
    final rs = widget.rs;
    return ReaderAsyncView<List<Map<String, dynamic>>>(
      loader: _achievements,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (items) => ListView(
        padding: const EdgeInsets.all(16),
        children: [
          for (final a in items)
            Card(
              margin: const EdgeInsets.only(bottom: 8),
              child: ListTile(
                leading: Icon(
                  a['unlocked'] == true
                      ? Icons.emoji_events
                      : Icons.emoji_events_outlined,
                ),
                title: Text('${a['title'] ?? a['key'] ?? ''}'),
                subtitle: Text(
                  '${a['description'] ?? ''}\n'
                  '${rs.t('gameProgress')}: ${a['progress'] ?? 0}/${a['target'] ?? 0}',
                ),
                isThreeLine: true,
                trailing: a['unlocked'] == true
                    ? Text(rs.t('gameUnlocked'))
                    : Text('${a['progress'] ?? 0}/${a['target'] ?? 0}'),
              ),
            ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 3,
      child: Column(children: [
        Row(children: [
          const SizedBox(width: 16),
          Expanded(
            child: Text(widget.rs.t('games'),
                style: Theme.of(context).textTheme.titleMedium),
          ),
          IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
        ]),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          child: Card(
            child: ListTile(
              leading: const Icon(Icons.sports_esports, size: 20),
              title: Text(widget.rs.t('gameConsoleTitle')),
              subtitle: Text(widget.rs.t('gameConsoleEntryDesc')),
              onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                      builder: (_) => GameConsolePage(
                          client: widget.client, rs: widget.rs))),
            ),
          ),
        ),
        TabBar(tabs: [
          Tab(text: widget.rs.t('gameCatalog')),
          Tab(text: widget.rs.t('gameHistory')),
          Tab(text: widget.rs.t('gameAchievements')),
        ]),
        Expanded(
          child: TabBarView(children: [
            _catalogView(),
            _historyView(),
            _achievementsView(),
          ]),
        ),
      ]),
    );
  }
}
