import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

/// 角色时光轴（2026-11-22 批 5，只读）：`GET /api/v1/timeline/{id}`——
/// 认识天数 / 首次聊天 / 关键节点与近期记忆条目（date/type/title/desc/importance）。
class TimelinePage extends StatefulWidget {
  const TimelinePage({
    super.key,
    required this.client,
    required this.characterId,
    required this.rs,
  });

  final AmbApiClient client;
  final int characterId;
  final ReaderStrings rs;

  @override
  State<TimelinePage> createState() => _TimelinePageState();
}

class _TimelinePageState extends State<TimelinePage> {
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
      final data = await _api.getTimeline(widget.characterId);
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

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    final d = _data;
    final items = ((d?['items'] as List?) ?? const [])
        .cast<Map>()
        .map((e) => e.cast<String, dynamic>())
        .toList();

    return Scaffold(
      appBar: AppBar(title: Text(rs.t('timeline'))),
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
                      child: Row(children: [
                        Expanded(
                          child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text('${rs.t('timelineDays')} · ${d?['days_known'] ?? '—'}',
                                    style: theme.textTheme.titleMedium),
                                const SizedBox(height: 4),
                                Text(
                                    '${rs.t('timelineFirstChat')} · '
                                    '${((d?['first_chat_at'] as String? ?? '').replaceAll('T', ' '))}',
                                    style: theme.textTheme.bodySmall
                                        ?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
                              ]),
                        ),
                      ]),
                    ),
                  ),
                  const SizedBox(height: 10),
                  if (items.isEmpty)
                    Card(
                        child: Padding(
                      padding: const EdgeInsets.all(14),
                      child: Text(rs.t('timelineNoMilestones')),
                    ))
                  else
                    for (final it in items)
                      Card(
                        margin: const EdgeInsets.only(bottom: 8),
                        child: ListTile(
                          dense: true,
                          leading: Text(
                              it['type'] == 'milestone' ? '⭐' : '💬',
                              style: const TextStyle(fontSize: 16)),
                          title: Text(it['title'] as String? ?? '',
                              maxLines: 2, overflow: TextOverflow.ellipsis),
                          subtitle: Text(
                              '${it['date'] ?? ''} · ${it['desc'] ?? ''}',
                              maxLines: 3,
                              overflow: TextOverflow.ellipsis),
                          trailing: Text('${it['importance'] ?? ''}',
                              style: theme.textTheme.labelSmall),
                        ),
                      ),
                ]),
    );
  }
}
