import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

/// Life 首页状态（2026-11-22 批 8，只读）：`GET /api/v1/life-home/state`——
/// 角色/位置/玩家三值（stamina/mood/hunger）/AI 当前状态/房间与宠物计数。
class LifeHomePage extends StatefulWidget {
  const LifeHomePage({
    super.key,
    required this.client,
    required this.characterId,
    required this.rs,
  });

  final AmbApiClient client;
  final int characterId;
  final ReaderStrings rs;

  @override
  State<LifeHomePage> createState() => _LifeHomePageState();
}

class _LifeHomePageState extends State<LifeHomePage> {
  late final ReaderApi _api = ReaderApi(widget.client);
  Map<String, dynamic>? _data;
  bool _loading = true;
  String _error = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = '';
    });
    try {
      final data = await _api.getLifeHomeState(widget.characterId);
      if (!mounted) return;
      setState(() {
        _data = data;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  String _loc(String? key) => (key == null || key.isEmpty) ? '—' : key;

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    final d = _data;
    final player = ((d?['player'] as Map?) ?? const {}).cast<String, dynamic>();
    final ai = ((d?['ai'] as Map?) ?? const {}).cast<String, dynamic>();
    final rooms = ((d?['rooms'] as List?) ?? const []);
    final pets = ((d?['pets'] as List?) ?? const []);

    Widget stat(String label, Object? value) => Expanded(
          child: Column(children: [
            Text('$value', style: theme.textTheme.titleSmall),
            Text(label, style: theme.textTheme.labelSmall),
          ]),
        );

    return Scaffold(
      appBar: AppBar(title: Text(rs.t('lifeHomeTitle'))),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error.isNotEmpty
              ? Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Text(_error, style: theme.textTheme.bodySmall),
                  TextButton(onPressed: _load, child: Text(rs.t('retry'))),
                ]))
              : ListView(padding: const EdgeInsets.all(12), children: [
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(14),
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text('${d?['character_name'] ?? ''}',
                                style: theme.textTheme.titleMedium),
                            const SizedBox(height: 6),
                            Text(
                                '${rs.t('lifeHomeRoom')} ${_loc(d?['current_room'] as String?)} · '
                                '${rs.t('lifeHomeLocation')} ${_loc(d?['location'] as String?)}',
                                style: theme.textTheme.bodySmall),
                            const SizedBox(height: 10),
                            Text(ai['current_status'] as String? ?? '',
                                style: theme.textTheme.bodyMedium),
                          ]),
                    ),
                  ),
                  const SizedBox(height: 10),
                  _section(rs.t('lifeHomePlayer'), theme),
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Row(children: [
                        stat(rs.t('lifeHomeStamina'), player['stamina']),
                        stat(rs.t('lifeHomeMood'), player['mood']),
                        stat(rs.t('lifeHomeHunger'), player['hunger']),
                        stat(rs.t('lifeHomeRooms'), rooms.length),
                        stat(rs.t('lifeHomePets'), pets.length),
                      ]),
                    ),
                  ),
                  const SizedBox(height: 10),
                  _section(rs.t('lifeHomeRooms'), theme),
                  Card(
                    child: Column(
                      children: [
                        for (final r in rooms.cast<Map>())
                          ListTile(
                            dense: true,
                            leading: const Icon(Icons.meeting_room, size: 18),
                            title: Text(r['name'] as String? ?? ''),
                            subtitle: Text(r['id'] as String? ?? ''),
                          ),
                      ],
                    ),
                  ),
                ]),
    );
  }

  Widget _section(String title, ThemeData theme) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 6, 4, 6),
        child: Text(title,
            style: theme.textTheme.titleSmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
      );
}
