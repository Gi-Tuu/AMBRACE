import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

/// 会话归档（按日期分组只读视图，2026-11-22 批 3）。
///
/// 契约 = `GET /api/v1/chat/sessions/{id}/archive`（北京时区按日分组、日期倒序）；
/// 客户端再按 年 → 月 两级折叠，并带**客户端内容过滤**（App 侧「会话搜索」同口径——
/// 服务端没有搜索端点，不伪造）。
class ArchiveViewPage extends StatefulWidget {
  const ArchiveViewPage({
    super.key,
    required this.sessionId,
    required this.transport,
    required this.rs,
  });

  final int sessionId;
  final ChatTransport transport;
  final ReaderStrings rs;

  @override
  State<ArchiveViewPage> createState() => _ArchiveViewPageState();
}

class _ArchiveViewPageState extends State<ArchiveViewPage> {
  List<Map<String, dynamic>> _days = const [];
  bool _loading = true;
  String _error = '';
  String _query = '';

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
      final data = await widget.transport.getChatArchive(widget.sessionId);
      final days = (data['days'] as List? ?? const [])
          .cast<Map>()
          .map((e) => e.cast<String, dynamic>())
          .toList();
      if (!mounted) return;
      setState(() {
        _days = days;
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

  /// 年 → 月 → 日 的两级折叠分组（App archive_screen 同口径；查询过滤按内容）。
  Map<String, Map<String, List<Map<String, dynamic>>>> _grouped() {
    final q = _query.trim().toLowerCase();
    final result = <String, Map<String, List<Map<String, dynamic>>>>{};
    for (final day in _days) {
      final date = day['date'] as String? ?? '';
      final msgs = ((day['messages'] as List? ?? const [])
              .cast<Map>()
              .map((e) => e.cast<String, dynamic>())
              .where((m) =>
                  q.isEmpty ||
                  ((m['content'] as String? ?? '').toLowerCase().contains(q))))
          .toList();
      if (msgs.isEmpty && q.isNotEmpty) continue;
      if (date.length < 7) continue;
      final year = date.substring(0, 4);
      final month = date.substring(0, 7);
      result.putIfAbsent(year, () => {})[month] ??= [];
      result[year]![month]!.add({
        ...day,
        'messages': msgs,
      });
    }
    return result;
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(rs.t('archiveView'))),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error.isNotEmpty
              ? Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Text(_error, style: theme.textTheme.bodySmall),
                  TextButton(onPressed: _load, child: Text(rs.t('retry'))),
                ]))
              : Column(children: [
                  Padding(
                    padding: const EdgeInsets.all(12),
                    child: TextField(
                      decoration: InputDecoration(
                        prefixIcon: const Icon(Icons.search, size: 20),
                        hintText: rs.t('archiveFilter'),
                        isDense: true,
                        border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(10)),
                      ),
                      onChanged: (v) => setState(() => _query = v),
                    ),
                  ),
                  Expanded(
                    child: _days.isEmpty
                        ? Center(child: Text(rs.t('archiveEmpty')))
                        : _buildGroups(theme),
                  ),
                ]),
    );
  }

  Widget _buildGroups(ThemeData theme) {
    final rs = widget.rs;
    final grouped = _grouped();
    final years = grouped.keys.toList()..sort((a, b) => b.compareTo(a));
    if (years.isEmpty) {
      return Center(child: Text(rs.t('archiveNoMatch')));
    }
    return ListView(padding: const EdgeInsets.all(12), children: [
      for (final year in years) ...[
        Text(year, style: theme.textTheme.titleLarge),
        for (final month in grouped[year]!.keys.toList()
            ..sort((a, b) => b.compareTo(a))) ...[
          Text(month,
              style: theme.textTheme.titleMedium
                  ?.copyWith(color: theme.colorScheme.primary)),
          for (final day in grouped[year]![month]!)
            Card(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(12, 10, 12, 4),
                  child: Row(children: [
                    Text(day['date'] as String? ?? '',
                        style: theme.textTheme.titleSmall),
                    const Spacer(),
                    Text('${day['count']}',
                        style: theme.textTheme.labelSmall),
                  ]),
                ),
                const Divider(height: 1),
                for (final m in day['messages'] as List<Map<String, dynamic>>)
                  ListTile(
                    dense: true,
                    leading: Text(
                        m['sender_type'] == 'user' ? '👤' : '🤖',
                        style: const TextStyle(fontSize: 16)),
                    title: Text((m['content'] as String? ?? ''),
                        maxLines: 4, overflow: TextOverflow.ellipsis),
                    subtitle: Text(
                        (m['created_at'] as String? ?? '').replaceAll('T', ' '),
                        style: theme.textTheme.labelSmall),
                  ),
              ]),
          ),
        ],
        const SizedBox(height: 12),
      ],
    ]);
  }
}
