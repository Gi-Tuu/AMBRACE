import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import '../../widgets/reader_async_view.dart';
import 'reader_state.dart';

/// 织库卡片只读：标题/摘要/重要度/关联记忆数；生成、去重、画布未做。
class WeavePane extends StatefulWidget {
  const WeavePane({
    super.key,
    required this.client,
    required this.rs,
    required this.cs,
  });

  final AmbApiClient client;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<WeavePane> createState() => _WeavePaneState();
}

class _WeavePaneState extends State<WeavePane> {
  late final WeaveApi _api = WeaveApi(widget.client);
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

  bool _generating = false;

  Future<void> _generate() async {
    setState(() => _generating = true);
    try {
      // weave_api.generateCards 需要角色 id；从已有 cards 取
      // 简化：不带 character_id（后端会用默认）
      await _api.generateCards(0);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(widget.rs.t('weaveGenerateDone'))));
      _reload();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('${widget.rs.t('weaveGenerateFail')}：$e')));
    } finally {
      if (mounted) setState(() => _generating = false);
    }
  }

  void _reload() {
    _loader.load((ct) => _api.cards(cancelToken: ct),
        isEmpty: (items) => items.isEmpty);
  }

  Future<void> _openDetail(Map<String, dynamic> card) async {
    final id = (card['id'] as num?)?.toInt();
    if (id == null) return;
    try {
      final detail = await _api.detail(id);
      if (!mounted) return;
      showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text('${detail['title'] ?? card['title'] ?? ''}'),
          content: SizedBox(
            width: 460,
            child: ListView(shrinkWrap: true, children: [
              if ((detail['character_name'] as String? ?? '').isNotEmpty)
                Text('${widget.rs.t('weaveCharacter')}: ${detail['character_name']}'),
              if ((detail['summary'] as String? ?? '').isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(detail['summary'] as String),
                ),
              if (detail['memories'] is List && (detail['memories'] as List).isNotEmpty) ...[
                const Divider(height: 24),
                Text(widget.rs.t('weaveMemories'),
                    style: Theme.of(ctx).textTheme.titleSmall),
                for (final m in (detail['memories'] as List).take(10))
                  if (m is Map)
                    Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Text('• ${m['snippet'] ?? m['content'] ?? ''}'),
                    ),
              ],
            ]),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: Text(widget.rs.t('cancel'))),
          ],
        ),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    }
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
              child: Text(rs.t('weave'),
                  style: Theme.of(context).textTheme.titleMedium),
            ),
            IconButton(
            tooltip: widget.rs.t('weaveGenerate'),
            onPressed: _generating ? null : _generate,
            icon: _generating
                ? const SizedBox(width: 16, height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.auto_awesome, size: 20),
          ),
          IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
          ]),
          Text(rs.t('weaveReadOnly'),
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 8),
          for (final c in items)
            Card(
              margin: const EdgeInsets.only(bottom: 10),
              child: InkWell(
                onTap: () => _openDetail(c),
                child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('${c['title'] ?? ''}',
                      style: Theme.of(context).textTheme.titleSmall),
                  if ((c['summary'] ?? '').toString().isNotEmpty) ...[
                    const SizedBox(height: 4),
                    Text('${c['summary']}'),
                  ],
                  const SizedBox(height: 6),
                  Text(
                    [
                      if (c['importance'] != null)
                        '${rs.t('importance')}: ${c['importance']}',
                      if (c['memory_count'] != null)
                        '${rs.t('linkedMemories')}: ${c['memory_count']}',
                    ].join(' · '),
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ]),
              ),
              ),
            ),
        ],
      ),
    );
  }
}
