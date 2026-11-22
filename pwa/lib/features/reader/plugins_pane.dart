import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import 'channel_qr_page.dart';
import '../../widgets/reader_async_view.dart';
import 'reader_state.dart';

/// 插件列表只读：不展示 config（可能含凭据），只显示名称/版本/状态/描述。
class PluginsPane extends StatefulWidget {
  const PluginsPane({
    super.key,
    required this.client,
    required this.rs,
    required this.cs,
  });

  final AmbApiClient client;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<PluginsPane> createState() => _PluginsPaneState();
}

class _PluginsPaneState extends State<PluginsPane> {
  late final PluginApi _api = PluginApi(widget.client);
  late final ReaderLoader<List<Map<String, dynamic>>> _loader =
      ReaderLoader<List<Map<String, dynamic>>>();

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _loader.dispose();
    super.dispose();
  }

  void _reload() {
    _loader.load((ct) => _api.list(cancelToken: ct),
        isEmpty: (items) => items.isEmpty);
  }

  String _s(Map<String, dynamic> m, List<String> keys) {
    for (final k in keys) {
      final v = m[k];
      if (v != null && '$v'.trim().isNotEmpty) return '$v';
    }
    return '';
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    return ReaderAsyncView<List<Map<String, dynamic>>>(
      loader: _loader,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (items) => ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Row(children: [
            Expanded(
              child:
                  Text(rs.t('plugins'), style: Theme.of(context).textTheme.titleMedium),
            ),
            IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
          ]),
          Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              leading: const Icon(Icons.qr_code_scanner, size: 20),
              title: Text(rs.t('qrChannelTitle')),
              subtitle: Text(rs.t('qrChannelEntryDesc')),
              onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                      builder: (_) =>
                          ChannelQrPage(client: widget.client, rs: rs))),
            ),
          ),
          Text(rs.t('pluginReadOnly'),
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 8),
          for (final p in items)
            Card(
              margin: const EdgeInsets.only(bottom: 8),
              child: ListTile(
                leading: Icon(
                  p['enabled'] == true ? Icons.extension : Icons.extension_off,
                ),
                title: Text(_s(p, ['name', 'title'])),
                subtitle: Text([
                  if (_s(p, ['version']).isNotEmpty)
                    'v${_s(p, ['version'])}',
                  if (_s(p, ['description', 'desc']).isNotEmpty)
                    _s(p, ['description', 'desc']),
                  if (_s(p, ['type', 'kind']).isNotEmpty)
                    _s(p, ['type', 'kind']),
                ].join(' · ')),
                trailing: Text(p['enabled'] == true
                    ? rs.t('enabled')
                    : rs.t('disabled')),
              ),
            ),
        ],
      ),
    );
  }
}
