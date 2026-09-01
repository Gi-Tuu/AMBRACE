import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/theme/aurora_tokens.dart';
import 'package:ai_companion/widgets/aurora_card.dart';
import 'package:ai_companion/widgets/empty_state.dart';

import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';

/// 记忆检索轨迹（#70-B，2026-08-30）：只读调试面板。
///
/// 进入页面拉一次 `/memory-trace`，列表项显示时间 / query / route / 候选数 / 延迟；
/// 点击展开 steps（dense/sparse/rrf/rerank_top/returned）。纯 GET、只读、不新增依赖。
class MemoryTraceScreen extends StatefulWidget {
  final int characterId;
  final String characterName;

  const MemoryTraceScreen({
    super.key,
    required this.characterId,
    required this.characterName,
  });

  @override
  State<MemoryTraceScreen> createState() => _MemoryTraceScreenState();
}

class _MemoryTraceScreenState extends State<MemoryTraceScreen> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _traces = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final traces = await ApiClient().getMemoryTrace(widget.characterId);
      if (!mounted) return;
      setState(() {
        _traces = traces;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = "$e";
        _loading = false;
      });
    }
  }

  String _fmtTime(String? iso) {
    if (iso == null || iso.isEmpty) return "";
    try {
      final t = DateTime.parse(iso).toLocal();
      return "${t.year}-${t.month.toString().padLeft(2, '0')}-${t.day.toString().padLeft(2, '0')} "
          "${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}";
    } catch (_) {
      return "";
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Scaffold(
      appBar: AppBar(
        backgroundColor: isDark
            ? Colors.black.withValues(alpha: 0.30)
            : Colors.white.withValues(alpha: 0.55),
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        shape: Border(
          bottom: BorderSide(
            color: isDark
                ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
                : Colors.black.withValues(alpha: AppGlass.borderAlpha),
            width: 0.5,
          ),
        ),
        title: Text(l10n.memoryTraceTitle),
        centerTitle: false,
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? ListView(
                  physics: const AlwaysScrollableScrollPhysics(),
                  children: [
                    SizedBox(height: MediaQuery.of(context).size.height * 0.22),
                    EmptyState(
                      icon: Icons.cloud_off_rounded,
                      title: l10n.loadFailed,
                      action: OutlinedButton(onPressed: _load, child: Text(l10n.retry)),
                    ),
                  ],
                )
              : _traces.isEmpty
                  ? Center(
                      child: Padding(
                        padding: const EdgeInsets.all(32),
                        child: Text(l10n.memoryTraceEmpty,
                            textAlign: TextAlign.center,
                            style: const TextStyle(color: IosCardColors.subtitle)),
                      ),
                    )
                  : RefreshIndicator(
                      onRefresh: _load,
                      child: ListView(
                        padding: const EdgeInsets.only(top: 8, bottom: 24),
                        children: [
                          for (int i = 0; i < _traces.length; i++)
                            Padding(
                              padding: const EdgeInsets.only(left: 12, right: 12, bottom: 10),
                              child: _buildTraceCard(context, _traces[i]),
                            ),
                        ],
                      ),
                    ),
    );
  }

  /// 单条检索轨迹卡（AuroraCard 内嵌 ExpansionTile）：摘要行 + 展开步骤。
  Widget _buildTraceCard(BuildContext context, Map<String, dynamic> trace) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    final steps = (trace['steps'] as Map?)?.cast<String, dynamic>() ?? {};
    final query = steps['query'] as String? ?? '';
    final route = trace['route'] as String? ?? steps['route'] as String? ?? '';
    final candidates = (steps['candidate_count'] as num?)?.toInt() ??
        (steps['hit_count'] as num?)?.toInt() ??
        0;
    final latency = trace['latency_ms'] as num? ?? steps['latency_ms'] ?? 0;
    final status = trace['status'] as String? ?? '';
    final time = _fmtTime(trace['created_at'] as String?);

    final summary = [
      if (time.isNotEmpty) time,
      if (route.isNotEmpty) route,
      if (candidates > 0) '$candidates',
      '$latency ms',
      if (status.isNotEmpty) status,
    ].join(' · ');

    return AuroraCard(
      padding: EdgeInsets.zero,
      child: Material(
        type: MaterialType.transparency,
        child: ExpansionTile(
          leading: Icon(Icons.memory, size: 20, color: scheme.primary),
          title: Text(query.isEmpty ? l10n.memoryTraceNoData : query,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: scheme.onSurface)),
          subtitle: Text(summary,
              style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
          shape: const Border(),
          collapsedShape: const Border(),
          childrenPadding: const EdgeInsets.only(left: 16, right: 16, bottom: 12),
          children: [
            if (steps.isNotEmpty) ...[
              _traceStepRow(l10n.memoryTraceQuery,
                  query.isEmpty ? l10n.memoryTraceNoData : query, scheme),
              _traceStepRow(l10n.memoryTraceRoute,
                  route.isEmpty ? l10n.memoryTraceNoData : route, scheme),
              _traceStepRow(l10n.memoryTraceCandidates, '$candidates', scheme),
              _traceStepRow(l10n.memoryTraceLatency, '$latency ms', scheme),
              if (steps['dense_hits'] is List)
                _traceListRow(l10n.memoryTraceDense,
                    (steps['dense_hits'] as List).map((e) => '$e').toList(), scheme),
              if (steps['sparse_hits'] is List)
                _traceListRow(l10n.memoryTraceSparse,
                    (steps['sparse_hits'] as List).map((e) => '$e').toList(), scheme),
              if (steps['rrf_top'] is List)
                _traceListRow(l10n.memoryTraceRrf,
                    (steps['rrf_top'] as List).map((e) => '$e').toList(), scheme),
              if (steps['rerank_top'] is List)
                _traceRerankRow(l10n.memoryTraceRerank, steps['rerank_top'] as List, scheme),
              if (steps['returned'] is List)
                _traceReturnedRow(l10n.memoryTraceReturned, steps['returned'] as List, scheme),
            ] else
              _traceStepRow(l10n.memoryTraceSteps, l10n.memoryTraceNoData, scheme),
          ],
        ),
      ),
    );
  }

  Widget _traceStepRow(String label, String value, ColorScheme scheme) {
    return Padding(
      padding: const EdgeInsets.only(top: 4, bottom: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 96,
            child: Text(label,
                style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(value,
                style: TextStyle(fontSize: 13, color: scheme.onSurface,
                    height: 1.4)),
          ),
        ],
      ),
    );
  }

  Widget _traceListRow(String label, List<String> items, ColorScheme scheme) {
    final text = items.isEmpty ? '-' : items.join(', ');
    return _traceStepRow(label, text, scheme);
  }

  /// rerank_top：逐个显示 id / score / importance（has_why 用对勾表示）。
  Widget _traceRerankRow(String label, List items, ColorScheme scheme) {
    final parts = <String>[];
    for (final e in items) {
      if (e is Map) {
        final id = e['id'];
        final score = e['score'];
        final imp = e['importance'];
        final why = e['has_why'] == true ? ' ✓' : '';
        parts.add('$id (score=$score, imp=$imp$why)');
      } else {
        parts.add('$e');
      }
    }
    return _traceStepRow(label, parts.isEmpty ? '-' : parts.join(', '), scheme);
  }

  /// returned：显示注入记忆的 id + preview（截断）。
  Widget _traceReturnedRow(String label, List items, ColorScheme scheme) {
    final parts = <String>[];
    for (final e in items) {
      if (e is Map) {
        final id = e['id'];
        final preview = (e['preview'] as String? ?? '');
        parts.add('$id: $preview');
      } else {
        parts.add('$e');
      }
    }
    return _traceStepRow(label, parts.isEmpty ? '-' : parts.join('\n'), scheme);
  }
}
