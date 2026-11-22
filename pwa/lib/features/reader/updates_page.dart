import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

/// 更新公告页（2026-11-22 批 4）：`GET /api/v1/system/updates` 按天折叠，
/// 最新一天默认展开。内容/原因均为后端 docs/changelog.md 解析结果，原文展示。
class UpdatesPage extends StatefulWidget {
  const UpdatesPage({super.key, required this.client, required this.rs});

  final AmbApiClient client;
  final ReaderStrings rs;

  @override
  State<UpdatesPage> createState() => _UpdatesPageState();
}

class _UpdatesPageState extends State<UpdatesPage> {
  late final ApiConfigApi _api = ApiConfigApi(widget.client);
  List<Map<String, dynamic>> _days = const [];
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
      final data = await _api.updates();
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

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(rs.t('updatesTitle'))),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error.isNotEmpty
              ? Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Text(_error, style: theme.textTheme.bodySmall),
                  TextButton(onPressed: _load, child: Text(rs.t('retry'))),
                ]))
              : ListView(padding: const EdgeInsets.all(12), children: [
                  for (var i = 0; i < _days.length; i++)
                    Card(
                      margin: const EdgeInsets.only(bottom: 10),
                      child: ExpansionTile(
                        initiallyExpanded: i == 0,
                        tilePadding:
                            const EdgeInsets.symmetric(horizontal: 12),
                        title: Text(
                            '${_days[i]['title'] ?? ''} · ${_days[i]['date'] ?? ''}',
                            style: theme.textTheme.titleSmall),
                        children: [
                          for (final item in
                              (_days[i]['items'] as List? ?? const []))
                            Padding(
                              padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
                              child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(item['content'] as String? ?? '',
                                        style: theme.textTheme.bodyMedium),
                                    Text(
                                        '${rs.t('updatesReason')}：${item['reason'] ?? ''}',
                                        style: theme.textTheme.bodySmall
                                            ?.copyWith(
                                                color: theme.colorScheme
                                                    .onSurfaceVariant)),
                                  ]),
                            ),
                        ],
                      ),
                    ),
                ]),
    );
  }
}
