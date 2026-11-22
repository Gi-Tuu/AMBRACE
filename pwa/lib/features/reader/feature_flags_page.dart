import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

/// 功能开关页（2026-11-22 批 4）：`GET /system/feature-flags` 全量（含 meta），
/// 按 meta.group 分组、组内按 order 排序；切换走 `PUT /{key}` `{enabled}`——
/// 用户语义键写本账号覆盖行，其余写全局；403（锁定/非自助）原样提示。
class FeatureFlagsPage extends StatefulWidget {
  const FeatureFlagsPage({super.key, required this.client, required this.rs});

  final AmbApiClient client;
  final ReaderStrings rs;

  @override
  State<FeatureFlagsPage> createState() => _FeatureFlagsPageState();
}

class _FeatureFlagsPageState extends State<FeatureFlagsPage> {
  late final ApiConfigApi _api = ApiConfigApi(widget.client);
  List<Map<String, dynamic>> _flags = const [];
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
      final data = await _api.featureFlags();
      final flags = (data['flags'] as List? ?? const [])
          .cast<Map>()
          .map((e) => e.cast<String, dynamic>())
          .toList();
      if (!mounted) return;
      setState(() {
        _flags = flags;
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

  Future<void> _toggle(Map<String, dynamic> flag, bool v) async {
    final key = flag['key'] as String? ?? '';
    try {
      final updated = await _api.setFeatureFlag(key, v);
      if (!mounted) return;
      setState(() {
        _flags = [
          for (final f in _flags)
            if (f['key'] == key) {...f, ...updated} else f,
        ];
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content:
              Text('${widget.rs.t('pageToggleFail')}：$e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    if (_loading) {
      return Scaffold(
          appBar: AppBar(title: Text(rs.t('flagsTitle'))),
          body: const Center(child: CircularProgressIndicator()));
    }
    if (_error.isNotEmpty) {
      return Scaffold(
          appBar: AppBar(title: Text(rs.t('flagsTitle'))),
          body: Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(_error, style: theme.textTheme.bodySmall),
            TextButton(onPressed: _load, child: Text(rs.t('retry'))),
          ])));
    }

    // 分组（meta.group），组内按 meta.order 排序；组间按最小 order 排序。
    final groups = <String, List<Map<String, dynamic>>>{};
    for (final f in _flags) {
      final meta = (f['meta'] as Map? ?? {}).cast<String, dynamic>();
      final g = meta['group'] as String? ?? 'other';
      groups.putIfAbsent(g, () => []).add({...f, 'meta': meta});
    }
    num firstOrder(List<Map<String, dynamic>> items) =>
        items.isEmpty ? 9999 : ((items.first['meta']['order'] as num?) ?? 9999);
    final sorted = groups.entries.map((e) {
      final items = e.value
        ..sort((a, b) =>
            ((a['meta']['order'] as num?) ?? 9999)
                .compareTo(((b['meta']['order'] as num?) ?? 9999)));
      return MapEntry(e.key, items);
    }).toList()
      ..sort((a, b) => firstOrder(a.value).compareTo(firstOrder(b.value)));

    return Scaffold(
      appBar: AppBar(title: Text(rs.t('flagsTitle'))),
      body: ListView(padding: const EdgeInsets.all(12), children: [
        for (final g in sorted) ...[
          Padding(
            padding: const EdgeInsets.fromLTRB(4, 10, 4, 4),
            child: Text('${g.key} (${g.value.length})',
                style: theme.textTheme.titleSmall
                    ?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
          ),
          Card(
            child: Column(
              children: [
                for (final f in g.value)
                  SwitchListTile(
                    dense: true,
                    secondary: const Icon(Icons.toggle_off_outlined, size: 22),
                    title: Text((f['meta']['title'] as String? ?? '')
                            .trim()
                            .isNotEmpty
                        ? f['meta']['title'] as String
                        : f['key'] as String),
                    subtitle: (f['meta']['desc'] as String? ?? '')
                            .trim()
                            .isNotEmpty
                        ? Text(f['meta']['desc'] as String,
                            maxLines: 2, overflow: TextOverflow.ellipsis)
                        : null,
                    value: (f['user_enabled'] ?? f['enabled']) == true,
                    onChanged: (v) => _toggle(f, v),
                  ),
              ],
            ),
          ),
        ],
      ]),
    );
  }
}
