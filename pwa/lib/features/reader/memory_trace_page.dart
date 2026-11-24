// PWA「记忆检索轨迹」页（2026-11-24）：只读调试面板，对齐 App `MemoryTraceScreen`。
//
// `GET /api/v1/characters/{id}/memory-trace?limit=` 一次拉取；每条卡片摘要
// （时间 · 路由 · 候选数 · 延迟 · 状态），展开显示 steps（query / dense_hits /
// sparse_hits / rrf_top / rerank_top / returned）。纯 GET，零写入。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

class MemoryTracePage extends StatefulWidget {
  const MemoryTracePage({
    super.key,
    required this.client,
    required this.characterId,
    required this.characterName,
    required this.rs,
  });

  final AmbApiClient client;
  final int characterId;
  final String characterName;
  final ReaderStrings rs;

  @override
  State<MemoryTracePage> createState() => _MemoryTracePageState();
}

class _MemoryTracePageState extends State<MemoryTracePage> {
  late final ReaderApi _api = ReaderApi(widget.client);
  List<Map<String, dynamic>> _traces = const [];
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
      final data = await _api.getMemoryTrace(widget.characterId, limit: 20);
      final raw = data['traces'];
      final traces = raw is List
          ? [for (final e in raw) if (e is Map) e.cast<String, dynamic>()]
          : <Map<String, dynamic>>[];
      if (!mounted) return;
      setState(() {
        _traces = traces;
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

  String _fmtTime(Object? iso) {
    if (iso is! String || iso.isEmpty) return '';
    try {
      final t = DateTime.parse(iso).toLocal();
      return '${t.year}-${t.month.toString().padLeft(2, '0')}-'
          '${t.day.toString().padLeft(2, '0')} '
          '${t.hour.toString().padLeft(2, '0')}:'
          '${t.minute.toString().padLeft(2, '0')}';
    } catch (_) {
      return '';
    }
  }

  List<String> _ids(Object? v) =>
      v is List ? [for (final e in v) '$e'] : const [];

  List<String> _rerank(Object? v) {
    if (v is! List) return const [];
    return [
      for (final e in v)
        if (e is Map)
          '${e['id']} (score=${e['score']}, imp=${e['importance']}'
              '${e['has_why'] == true ? ' ✓' : ''})'
        else '$e',
    ];
  }

  List<String> _returned(Object? v) {
    if (v is! List) return const [];
    return [
      for (final e in v)
        if (e is Map) '${e['id']}: ${e['preview'] ?? ''}' else '$e',
    ];
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text('${rs.t('traceTitle')} · ${widget.characterName}'),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error.isNotEmpty
              ? Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Padding(
                      padding: const EdgeInsets.all(16),
                      child: Text(_error,
                          textAlign: TextAlign.center,
                          style: TextStyle(color: theme.colorScheme.error)),
                    ),
                    OutlinedButton(onPressed: _load, child: Text(rs.t('retry'))),
                  ]),
                )
              : _traces.isEmpty
                  ? Center(
                      child: Padding(
                        padding: const EdgeInsets.all(32),
                        child: Text(rs.t('traceEmpty'),
                            textAlign: TextAlign.center,
                            style: TextStyle(color: theme.colorScheme.outline)),
                      ),
                    )
                  : RefreshIndicator(
                      onRefresh: _load,
                      child: ListView(
                        padding: const EdgeInsets.fromLTRB(12, 8, 12, 28),
                        children: [
                          for (final t in _traces) _traceCard(t),
                        ],
                      ),
                    ),
    );
  }

  Widget _traceCard(Map<String, dynamic> trace) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    final steps = (trace['steps'] as Map?)?.cast<String, dynamic>() ?? const {};
    final query = (steps['query'] as String?) ?? '';
    final route = (trace['route'] as String?) ??
        (steps['route'] as String?) ??
        '';
    final candidates = (steps['candidate_count'] as num?)?.toInt() ??
        (steps['hit_count'] as num?)?.toInt() ??
        0;
    final latency = (trace['latency_ms'] as num?)?.toInt() ??
        (steps['latency_ms'] as num?)?.toInt() ??
        0;
    final status = (trace['status'] as String?) ?? '';
    final time = _fmtTime(trace['created_at']);
    final summary = [
      if (time.isNotEmpty) time,
      if (route.isNotEmpty) route,
      if (candidates > 0) '$candidates',
      '${latency}ms',
      if (status.isNotEmpty) status,
    ].join(' · ');

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      clipBehavior: Clip.antiAlias,
      child: ExpansionTile(
        leading: Icon(Icons.memory, size: 20, color: theme.colorScheme.primary),
        title: Text(query.isEmpty ? rs.t('traceNoData') : query,
            maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(summary,
            style: const TextStyle(fontSize: 11)),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
        children: steps.isEmpty
            ? [
                _row(rs.t('traceSteps'), rs.t('traceNoData')),
              ]
            : [
                _row(rs.t('traceQuery'), query.isEmpty ? rs.t('traceNoData') : query),
                _row(rs.t('traceRoute'), route.isEmpty ? rs.t('traceNoData') : route),
                _row(rs.t('traceCandidates'), '$candidates'),
                _row(rs.t('traceLatency'), '${latency}ms'),
                if (steps['dense_hits'] is List)
                  _row(rs.t('traceDense'), _ids(steps['dense_hits']).join(', ')),
                if (steps['sparse_hits'] is List)
                  _row(rs.t('traceSparse'), _ids(steps['sparse_hits']).join(', ')),
                if (steps['rrf_top'] is List)
                  _row(rs.t('traceRrf'), _ids(steps['rrf_top']).join(', ')),
                if (steps['rerank_top'] is List)
                  _row(rs.t('traceRerank'), _rerank(steps['rerank_top']).join(', ')),
                if (steps['returned'] is List)
                  _row(rs.t('traceReturned'), _returned(steps['returned']).join('\n')),
              ],
      ),
    );
  }

  Widget _row(String label, String value) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(
            width: 92,
            child: Text(label, style: const TextStyle(fontSize: 12)),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(value.isEmpty ? '—' : value,
                style: const TextStyle(fontSize: 13, height: 1.4)),
          ),
        ]),
      );
}
